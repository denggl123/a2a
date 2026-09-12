"""提现。"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from a2n_wallet import wallet
from a2n_settlement.reconcile import is_frozen

router = APIRouter(prefix="/v1", tags=["wallet"])


class WithdrawIn(BaseModel):
    amount: int


@router.post("/wallet/withdraw")
def withdraw(body: WithdrawIn, principal: str = Header(alias="X-Principal")):
    if is_frozen():
        raise HTTPException(423, "对账不一致，提现已冻结")
    try:
        return wallet.request(principal, body.amount)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/wallet/withdrawals")
def withdrawals(principal: str | None = Header(default=None, alias="X-Principal")):
    # 提现清单包含账户级出入款流向，匿名不能枚举。
    if not principal:
        raise HTTPException(401, "缺少 X-Principal")
    return wallet.list(principal)
