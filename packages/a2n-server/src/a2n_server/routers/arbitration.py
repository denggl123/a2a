"""仲裁工作台接口：争议列表、开单、裁定。

这个路由只做两件事：把材料摊在桌面上，把裁定记进凭证链。
退钱不在这一层 —— 裁定事件由装配层转给结算层执行（wiring.py 第 4 条线）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from a2n_acceptance.dispute import disputes

router = APIRouter(prefix="/v1/arbitration", tags=["arbitration"])


class DisputeIn(BaseModel):
    task_id: str
    side: str                      # requester | node
    reason: str
    opened_by: str = "system"
    evidence: dict | None = None


class ResolveIn(BaseModel):
    ruling: str                    # uphold_reject | overturn_pay | partial
    arbitrator: str
    refund_points: int = 0
    resolution: str = ""


@router.get("/disputes")
def list_disputes(state: str | None = None, task_id: str | None = None, limit: int = 50):
    return disputes.list(state=state, task_id=task_id, limit=limit)


@router.get("/disputes/{dispute_id}")
def get_dispute(dispute_id: str):
    d = disputes.get(dispute_id)
    if not d:
        raise HTTPException(404, "争议不存在")
    return d


@router.post("/disputes")
def open_dispute(body: DisputeIn):
    try:
        return disputes.open(body.task_id, body.side, body.reason,
                             opened_by=body.opened_by, evidence=body.evidence)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/disputes/{dispute_id}/resolve")
def resolve(dispute_id: str, body: ResolveIn):
    try:
        return disputes.resolve(dispute_id, body.ruling, body.arbitrator,
                                refund_points=body.refund_points, resolution=body.resolution)
    except ValueError as e:
        raise HTTPException(400, str(e))
