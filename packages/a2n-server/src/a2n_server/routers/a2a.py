"""A2A v1.0 协议适配层：Agent Card + JSON-RPC 2.0。

这一层**只做协议适配**，不做业务编排：
  - 把 A2A 的 Message / contextId / artifact 翻成 A2N 能懂的输入；
  - 调 `a2n-gateway.invoke` 走统一链路（门禁 → 任务 → 执行 → 验收 → 记账）；
  - 把规范事实翻回 A2A 的 Task 视图（状态、artifact、metadata）。

编排为什么不放这里：放在这里，换一种协议就得把"验收、公证、记账"重写一遍，
而重写的那一遍总会少点什么（第一版就少了验收）。现在规矩只有一份。

两套 id 的问题在这里一并解决：A2A 客户端看到的 Task id **就是规范任务主键**，
一个 id、一份事实；A2A 专有字段（contextId/message）作为适配视图 1:1 挂着。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from a2n_gateway import PaymentRequired, invoke
from a2n_registry import registry
from a2n_store import conn
from a2n_task import tasks
from a2n_task.a2a import save_view as a2a_save_view
from a2n_task.a2a import to_a2a

router = APIRouter(prefix="/a2a", tags=["a2a"])

JSONRPC = "2.0"
SUPPORTED = {"message/send", "tasks/get", "tasks/cancel"}
PAYMENT_REQUIRED = -32002          # x402：需要支付（对齐 HTTP 402 语义）


def _card_of(agent_id: str) -> dict:
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    card = json.loads(a["card_json"] or "{}")
    # 投影：对外只给 A2N 中继入口，节点真实地址不出注册表
    card["url"] = f"/a2a/{agent_id}"
    card["preferredTransport"] = "JSONRPC"
    card.setdefault("capabilities", {})["streaming"] = False
    card["x-a2n"] = dict(card.get("x-a2n") or {},
                         agent_id=agent_id,
                         relay=f"/v1/relay/{agent_id}",
                         settle_modes=(card.get("x-a2n") or {}).get("accepts")
                         or card.get("accepts") or [])
    return card


def _ok(result: Any, rid: Any) -> dict:
    return {"jsonrpc": JSONRPC, "id": rid, "result": result}


def _err(code: int, message: str, rid: Any, data: Any = None) -> dict:
    e: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": JSONRPC, "id": rid, "error": e}


@router.get("/{agent_id}/.well-known/agent.json")
def agent_card(agent_id: str):
    """A2A 标准的 Agent Card 发现入口（多租户：按 agent_id 区分）。"""
    return _card_of(agent_id)


root_router = APIRouter(tags=["a2a"])


@root_router.get("/.well-known/agent.json")
def agent_card_query(agent_id: str = ""):
    """域名级 Agent Card：标准 A2A 客户端会先打这个地址。"""
    if not agent_id:
        raise HTTPException(400, "需要 agent_id")
    return _card_of(agent_id)


@router.post("/{agent_id}")
async def jsonrpc(agent_id: str, request: Request, response: Response,
                  principal: str = Header(default="", alias="X-Principal"),
                  x_payment: str = Header(default="", alias="X-PAYMENT")):
    """A2A JSON-RPC 2.0 入口。同步逻辑丢线程池（转发最长等 15s）。"""
    try:
        body = json.loads((await request.body()) or b"{}")
    except ValueError:
        return _err(-32700, "Parse error", None)

    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if body.get("jsonrpc") != JSONRPC:
        return _err(-32600, "只支持 JSON-RPC 2.0", rid)
    if method not in SUPPORTED:
        return _err(-32601, f"不支持的方法：{method}（支持 {sorted(SUPPORTED)}）", rid)
    if not registry.get(agent_id):
        return _err(-32602, "agent 不存在", rid)

    from a2n_custodian import parse_payment
    payment = parse_payment(x_payment)

    try:
        if method == "message/send":
            out = await run_in_threadpool(_message_send, agent_id, principal, params, payment)
            if isinstance(out, tuple):          # (402, requirement)
                response.status_code = 402
                return _err(PAYMENT_REQUIRED, "需要支付", rid, out[1])
            return _ok(out, rid)
        if method == "tasks/get":
            return _ok(await run_in_threadpool(_tasks_get, params), rid)
        return _ok(await run_in_threadpool(_tasks_cancel, params, principal), rid)
    except PaymentRequired as e:
        response.status_code = 402
        return _err(PAYMENT_REQUIRED, "需要支付", rid, e.requirement)
    except PermissionError as e:
        # 没资格调用。A2A 视角是授权失败，不是协议错误。
        return _err(-32001, str(e), rid)
    except ValueError as e:
        return _err(-32602, str(e), rid)


def _message_send(agent_id: str, principal: str, params: dict, payment: dict | None):
    """发消息 → 走统一编排 → 写协议视图 → 返回 A2A Task。"""
    if not principal:
        raise ValueError("缺少 X-Principal")

    message = params.get("message") or {}
    payload: Any = None
    for p in message.get("parts") or []:
        if p.get("kind") == "data" or "data" in p:
            payload = p.get("data")
            break
        if p.get("kind") == "text" or "text" in p:
            payload = p.get("text")
            break
    if payload is None:
        payload = message

    skill = (params.get("metadata") or {}).get("skill") or ""
    context_id = params.get("contextId") or params.get("context_id") or ""

    call = invoke(agent_id=agent_id, principal=principal, skill=skill,
                  message=message, payload=payload, payment=payment, source="a2a")

    # 协议视图：写入口归任务域（save_view），路由不直写表；
    # 只存 A2A 专有字段，状态一律以规范任务为准
    artifacts = None
    if call.ok and call.result is not None:
        artifacts = [{"artifactId": f"art_{call.task_id}",
                      "parts": [{"kind": "data", "data": call.result}
                                if not isinstance(call.result, str)
                                else {"kind": "text", "text": call.result}]}]
    a2a_save_view(
        call.task_id, agent_id=agent_id, context_id=context_id, skill=skill,
        message=message, artifacts=artifacts, error=call.error,
        deal_id=(call.settle or {}).get("id") if (call.settle or {}).get("kind") == "deal" else None,
        charge_id=(call.settle or {}).get("id") if (call.settle or {}).get("kind") == "charge" else None,
        settle_mode=call.capability)
    return _task_view(call.task_id)


def _tasks_get(params: dict) -> dict:
    task_id = params.get("id") or params.get("taskId")
    if not task_id:
        raise ValueError("缺少任务 id")
    return _task_view(task_id)


def _tasks_cancel(params: dict, principal: str) -> dict:
    task_id = params.get("id") or params.get("taskId")
    if not task_id:
        raise ValueError("缺少任务 id")
    t = tasks.get(task_id)
    if not t:
        raise ValueError("任务不存在")
    # 越权防护：取消必须用调用方身份校验，不能拿任务里的 requester_id 直接取消
    # （否则任何人知道 task_id 就能取消别人的任务并触发退款）
    if t["requester_id"] != principal:
        raise PermissionError("只有任务发起方能取消该任务")
    if t.get("state") in ("CREATED", "ASSIGNED"):
        tasks.cancel(task_id, principal)
    return _task_view(task_id)


def _task_view(task_id: str) -> dict:
    """规范任务 + A2A 适配视图 → 标准 A2A Task。"""
    t = tasks.get(task_id)
    if not t:
        raise ValueError("任务不存在")
    row = conn().execute("SELECT * FROM a2a_tasks WHERE task_id=?", (task_id,)).fetchone()
    view = dict(row) if row else {}

    artifacts = json.loads(view["artifacts"]) if view.get("artifacts") else []
    error = json.loads(view["error"]) if view.get("error") else None
    state = to_a2a(t["state"])
    task: dict[str, Any] = {
        "id": t["id"],
        "contextId": view.get("context_id") or t["id"],
        "kind": "task",
        "status": {"state": state, "timestamp": t["updated_at"]},
        "artifacts": artifacts,
        "metadata": {"x-a2n": {
            "agent_id": t.get("node_id"),
            "skill": t.get("skill_id"),
            "amount": t.get("amount"),
            "result_hash": t.get("result_hash"),
            "settle_mode": view.get("settle_mode"),
            "deal_id": view.get("deal_id"),
            "charge_id": view.get("charge_id"),
            "source": t.get("source"),
        }},
    }
    if error:
        task["status"]["error"] = error
    if t.get("reject_reason"):
        task["status"]["message"] = {"role": "agent",
                                     "parts": [{"kind": "text", "text": t["reject_reason"]}]}
    return task
