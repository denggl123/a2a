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
    return tasks.list(requester_id=principal, node_id=as_node)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    t = tasks.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


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


@router.get("/usage")
def usage(limit: int = 50):
    rows = conn().execute("SELECT * FROM usage_reports ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["dims"] = json.loads(d["dims"])
        d["observed"] = json.loads(d["observed"] or "{}")
        d["contract"] = json.loads(d["contract"])
        out.append(d)
    return out
