"""持牌方回调：全系统唯一能触发积分增发/销毁的入口。

Ledger.mint / Ledger.burn 会检查调用者模块，只有这里能通过。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from a2n_custodian import get_custodian
from a2n_ledger import Ledger, ensure_account
from a2n_wallet import wallet
from a2n_kernel.events import publish
from a2n_kernel.hashing import sha256
from a2n_settlement.reconcile import is_frozen

router = APIRouter(prefix="/v1/custodian", tags=["custodian"])


class DepositIn(BaseModel):
    account_id: str
    amount_fen: int          # 分（1 元 = 100 分）
    provider_ref: str = ""


class PayoutIn(BaseModel):
    withdrawal_id: str


@router.post("/deposit")
def deposit(body: DepositIn):
    """充值：钱进入托管 → 网内增发等值积分。"""
    if body.amount_fen <= 0:
        raise HTTPException(400, "充值金额必须为正")
    ensure_account(body.account_id, "user", body.account_id, "verified")
    ref = body.provider_ref or sha256(f"{body.account_id}{body.amount_fen}")
    get_custodian().deposit(body.account_id, body.amount_fen, ref)
    entry = Ledger().mint(body.account_id, body.amount_fen, ref)
    publish("deposit.confirmed", {"account_id": body.account_id, "amount_fen": body.amount_fen, "ref": ref})
    return {"minted": body.amount_fen, "entry": entry, "custodian_ref": ref}


@router.post("/payout-callback")
def payout_callback(body: PayoutIn):
    """打款回执：钱离开托管 → 销毁对应积分。

    顺序不可颠倒：先打款成功，再销毁积分。若先销毁后打款失败，积分就凭空消失了。
    """
    try:
        res = wallet.payout(body.withdrawal_id)
        if not res.get("ok"):
            return {"withdrawal_id": body.withdrawal_id, "state": res.get("state")}
        Ledger().burn(res["hold"], res["amount"], body.withdrawal_id)
        return wallet.mark_paid(body.withdrawal_id, res["custodian_ref"])
    except ValueError as e:
        raise HTTPException(400, str(e))


class PayCallbackIn(BaseModel):
    charge_id: str
    ok: bool = True
    channel_ref: str = ""          # 渠道侧流水号
    why: str = ""                  # 失败原因


@router.post("/pay-callback")
def pay_callback(body: PayCallbackIn):
    """直付回执：渠道/托管确认到账 → 凭证 AUTHORIZED → CAPTURED。

    **只有真回执能调它**——没有回执的凭证永远是 AUTHORIZED，
    不允许"调用成功就当钱到了"。这是"状态不许撒谎"在直付上的落点。
    """
    from a2n_gateway import capture, fail_charge
    if not body.ok:
        ch = fail_charge(body.charge_id, body.why or "渠道失败")
        if not ch:
            raise HTTPException(404, "成交凭证不存在")
        return {"charge_id": body.charge_id, "state": ch["state"]}
    ch = capture(body.charge_id, body.channel_ref or None)
    if not ch:
        raise HTTPException(404, "成交凭证不存在")
    return {"charge_id": ch["charge_id"], "state": ch["state"],
            "amount_fen": ch["amount_fen"], "captured_at": ch["captured_at"]}


@router.get("/balance")
def balance():
    return {"escrow_balance_fen": get_custodian().balance_fen(), "withdrawals_frozen": is_frozen()}


@router.get("/book")
def book(limit: int = 50):
    """托管流水：充值、分账指令、打款。注意分账指令金额为 0（钱仍在托管内）。"""
    return get_custodian().book(limit)
