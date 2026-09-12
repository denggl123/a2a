"""任务：发单、拉取、提交结果与计量。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from a2n_store import conn
from a2n_task import tasks

router = APIRouter(prefix="/v1", tags=["tasks"])


class TaskIn(BaseModel):
    skill: str
    payload: dict | None = None
    budget: int = 100
    preferred_agents: list[str] | None = None


class SubmitIn(BaseModel):
    result: dict | str
    usage: dict


@router.post("/tasks")
def create_task(body: TaskIn, principal: str = Header(alias="X-Principal")):
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    try:
        return tasks.create(principal, body.skill, body.payload, body.budget, body.preferred_agents)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/tasks")
def list_tasks(principal: str | None = Header(default=None, alias="X-Principal"),
               as_node: str | None = None):
    # 反向漏消费活动：principal=空时不再回全量；要么自己看自己的（requester_id=principal），
    # 要么节点 ID 走 /v1/nodes/{node_id}/tasks 长轮询。
    if not principal and not as_node:
        raise HTTPException(401, "缺少 X-Principal 或 X-Node-Id")
    return tasks.list(requester_id=principal, node_id=as_node)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, principal: str | None = Header(default=None, alias="X-Principal")):
    # 按 id 查也要鉴权：里面含 requester_id（消费方身份）/node_id 配对；
    # task_id 不可猜不假，但匿名无限枚举有损。节点自带 X-Node-Id，自己可以看自己的任务；
    # 使用方必须 X-Principal，且必须是 requester。
    from a2n_task import tasks as _tasks  # 本地别名避免遮蔽
    t = _tasks.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if principal and t.get("requester_id") == principal:
        return t
    raise HTTPException(403, "任务不属于当前身份")


@router.get("/tasks/{task_id}/a2a")
def get_task_a2a(task_id: str):
    """标准 A2A v1.0 Task 视图 —— 给别的 agent/程序看的就是这个形状。"""
    v = tasks.a2a(task_id)
    if not v:
        raise HTTPException(404, "任务不存在")
    return v


class CancelIn(BaseModel):
    reason: str = "使用方取消"


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: CancelIn,
                principal: str = Header(alias="X-Principal")):
    try:
        return tasks.cancel(task_id, principal, reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/nodes/{node_id}/tasks")
def node_tasks(node_id: str, wait: int = 0, limit: int = 5):
    """节点取活。wait=秒数 启用长轮询（家宽节点零入站、低延迟取任务）。"""
    return tasks.pending_for(node_id, wait=wait, limit=limit)


@router.post("/tasks/{task_id}/result")
def submit_result(task_id: str, body: SubmitIn, x_node_id: str = Header(alias="X-Node-Id")):
    try:
        return tasks.submit(task_id, x_node_id, body.result, body.usage)
    except ValueError as e:
        raise HTTPException(400, str(e))


class FailIn(BaseModel):
    reason: str = "执行失败或超时"


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: str, body: FailIn, x_node_id: str = Header(alias="X-Node-Id")):
    """节点自报执行失败/超时：任务走 FAILED 终态，冻结预算原路退回。

    与 REJECTED（干了但没过验收）分开：FAILED = 根本没干完，
    记账理由与信誉扣分都不同。只有被派单的节点自己能上报。
    """
    t = tasks.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t["node_id"] != x_node_id:
        raise HTTPException(403, "只有被派单的节点能上报该任务的失败")
    try:
        return tasks.fail(task_id, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/usage")
def usage(limit: int = 50, principal: str | None = Header(default=None, alias="X-Principal"),
           as_node: str | None = Header(default=None, alias="X-Node-Id")):
    # usage 全量是节点维度（哪个节点跑了多少）；无身份不能枚举。
    # 自己节点自己看：X-Node-Id 即 node_id；使用方按 X-Principal 通过 task 表反查自己任务产生的报告。
    if not principal and not as_node:
        raise HTTPException(401, "缺少 X-Principal 或 X-Node-Id")
    if as_node:
        q = "SELECT * FROM usage_reports WHERE node_id=? ORDER BY rowid DESC LIMIT ?"
        args = [as_node, limit]
    else:
        # usage_reports 不存 requester_id；join tasks 查归属
        q = ("SELECT u.* FROM usage_reports u"
             " JOIN tasks t ON t.id = u.task_id"
             " WHERE t.requester_id=? ORDER BY u.rowid DESC LIMIT ?")
        args = [principal, limit]
    rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["dims"] = json.loads(d["dims"])
        d["observed"] = json.loads(d["observed"] or "{}")
        d["contract"] = json.loads(d["contract"])
        out.append(d)
    return out
