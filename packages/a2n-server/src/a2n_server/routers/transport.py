"""传输层路由：反向长连接（隧道）、中继转发、通道协商。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from a2n_store import conn
from a2n_registry import registry
from a2n_kernel.hashing import now_iso
from a2n_registry.reachability import reachable
from a2n_transport import hub, negotiate
from a2n_transport.call_token import issue as issue_call_token
from a2n_transport.call_token import subject as token_subject
from a2n_transport.call_token import verify as verify_call_token

router = APIRouter(prefix="/v1", tags=["transport"])


class TunnelOpenIn(BaseModel):
    meta: dict = {}
    mode: str = "tunnel"          # tunnel 或 relay（relay = 隧道 + 平台公网入口）


class TunnelUpIn(BaseModel):
    req_id: str
    status: int = 200
    body: Any = None


def _touch_heartbeat(agent_id: str) -> None:
    """隧道下行轮询本身就是"我在线"的证据，顺带刷心跳。"""
    conn().execute("UPDATE agents SET last_seen_at=? WHERE agent_id=?", (now_iso(), agent_id))
    conn().commit()


@router.post("/nodes/{node_id}/tunnel")
def tunnel_open(node_id: str, body: TunnelOpenIn,
                x_node_id: str = "") -> dict:
    """节点出站建立反向通道（生产为 WSS；v1 用长轮询下行，接口同构）。"""
    if not registry.get(node_id):
        raise HTTPException(404, "agent 不存在")
    r = hub.open(node_id, dict(body.meta or {}, declared_mode=body.mode))
    c = conn()
    row = c.execute("SELECT connection FROM agents WHERE agent_id=?", (node_id,)).fetchone()
    import json
    cj = json.loads(row["connection"]) if row["connection"] else {}
    cj["mode"] = body.mode  # relay 模式声明进入 connection，参与可达性与发现
    c.execute("UPDATE agents SET connection=? WHERE agent_id=?",
              (json.dumps(cj, ensure_ascii=False), node_id))
    c.commit()
    return r


@router.get("/nodes/{node_id}/tunnel/next")
def tunnel_next(node_id: str, tid: str, wait: float = 25.0):
    """下行长轮询：挂住等任务/转发请求。有则立即返回。"""
    _touch_heartbeat(node_id)
    msg = hub.poll(tid, wait=wait)
    return msg if msg is not None else {"type": "idle"}


@router.post("/nodes/{node_id}/tunnel/up")
def tunnel_up(node_id: str, body: TunnelUpIn):
    """上行回包：中继转发请求的响应从这里回去。"""
    if not hub.reply(node_id, body.req_id,
                     {"status": body.status, "body": body.body}):
        raise HTTPException(404, "转发请求不存在或已超时")
    return {"ok": True}


@router.post("/relay/{agent_id}/{path:path}")
@router.post("/relay/{agent_id}")
async def relay(agent_id: str, request: Request, path: str = "",
                x_a2n_call: str = Header(default="", alias="X-A2N-Call")):
    """中继转发：平台公网入口 → 隧道 → 节点本地 HTTP 服务。

    这就是"个人电脑提供的服务在公网被发现和调用"的最终形态：
    外部只认平台的公网地址，节点侧零暴露、零端口映射。

    **门禁**：必须出示调用凭据（X-A2N-Call）。没有凭据的请求一律 401 ——
    否则中继就是谁都能蹭的免费公网跳板，还能绕过计量与对账直接打节点。
    节点侧无需再验：请求能从隧道进来，必然已经过了这道门。

    注意：forward 是阻塞等待（最长 15s），必须丢进线程池执行——
    在事件循环上直接等会把整个服务冻住，所有长轮询一起超时。
    """
    if not registry.get(agent_id):
        raise HTTPException(404, "agent 不存在")
    ok, why = verify_call_token(x_a2n_call, agent_id)
    if not ok:
        raise HTTPException(401, f"调用凭据无效：{why}（先 POST /v1/transport/call-token 获取）")
    raw = await request.body()
    try:
        body = json.loads(raw.decode()) if raw else None
    except ValueError:
        body = raw.decode(errors="replace")
    payload = await run_in_threadpool(hub.forward, agent_id, "POST", "/" + path, body,
                                      caller=token_subject(x_a2n_call))
    if payload.get("error") == "no_tunnel":
        raise HTTPException(409, payload["message"])
    if payload.get("error") == "timeout":
        raise HTTPException(504, payload["message"])
    return payload.get("body", payload)


class CallTokenIn(BaseModel):
    agent_id: str
    ttl: int = 600


@router.post("/transport/call-token")
def call_token(body: CallTokenIn,
               principal: str = Header(default="", alias="X-Principal")):
    """签发调用凭据：只给与该 agent 存在 ACTIVE 对等账户配对的使用方。

    一期的权限边界就一条：没配过对，就没有调用资格。
    二期接入预付费积分后，这里再加一条：预算已冻结。
    """
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    if not registry.get(body.agent_id):
        raise HTTPException(404, "agent 不存在")
    linked = conn().execute(
        "SELECT 1 FROM peer_links pl JOIN party_accounts pa ON pa.account_id = pl.account_id"
        " WHERE pl.agent_id=? AND pa.owner_id=? AND pl.state='ACTIVE'",
        (body.agent_id, principal),
    ).fetchone()
    if not linked:
        raise HTTPException(403, "没有与该 agent 的 ACTIVE 对等账户配对，无调用资格")
    return {"token": issue_call_token(body.agent_id, principal, body.ttl),
            "ttl": body.ttl, "agent_id": body.agent_id}


@router.get("/nodes/{node_id}/transport")
def transport_negotiate(node_id: str):
    """通道协商：列出候选（direct/holepunch/tunnel/relay/pull）与当前最优。"""
    a = registry.get(node_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    # 注入权威可达性判定：transport 不反向依赖 registry，由装配层接线
    return negotiate(a, is_reachable=reachable)
