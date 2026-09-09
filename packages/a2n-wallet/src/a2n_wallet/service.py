"""M7 钱包与提现：冻结 → 指令 → 打款 → 销毁。

提现是积分唯一的出口。销毁只能由持牌方打款回执触发（见 routers/custodian）。
"""
from __future__ import annotations

from typing import Any

from a2n_custodian import get_custodian
from a2n_store import conn, tx
from a2n_kernel.events import publish
from a2n_kernel.hashing import new_id, now_iso
from a2n_ledger import Ledger, ensure_account

MAX_WITHDRAW_PER_DAY = 5_000_00  # 风控：单日上限（分）


class Wallet:
    def __init__(self) -> None:
        self.ledger = Ledger()

    def request(self, account_id: str, amount: int) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("提现金额必须为正")
        if amount > MAX_WITHDRAW_PER_DAY:
            raise ValueError(f"超出单日提现上限 {MAX_WITHDRAW_PER_DAY / 100:.0f} 元")
        wd_id = new_id("wd")
        hold = f"hold:{wd_id}"
        ensure_account(hold, "hold", hold)   # 幂等建户，放在事务外（它自己会提交）
        # 校验余额与冻结必须是同一个原子动作：否则两笔并发提现各自读到同一份
        # 余额、双双通过校验，账本被打穿（超提 = 资损）。tx() 拿写锁串行化。
        with tx():
            bal = self.ledger.balance(account_id)
            if bal < amount:
                raise ValueError(f"余额不足：{bal} < {amount}")
            self.ledger.post(account_id, -amount, "withdraw_freeze", wd_id, commit=False)
            self.ledger.post(hold, amount, "withdraw_freeze", wd_id, commit=False)
            conn().execute(
                "INSERT INTO withdrawals (id, account_id, amount, state, created_at,"
                " currency, amount_minor) VALUES (?,?,?,?,?,?,?)",
                (wd_id, account_id, amount, "FROZEN", now_iso(), "CNY", amount),
            )
        publish("withdrawal.requested", {"withdrawal_id": wd_id, "account_id": account_id, "amount": amount})
        return {"withdrawal_id": wd_id, "state": "FROZEN", "amount": amount}

    def payout(self, withdrawal_id: str) -> dict[str, Any]:
        """只做打款（持牌方动作），不碰账本 —— 销毁由持牌方回执触发。"""
        r = conn().execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
        if not r:
            raise ValueError("提现单不存在")
        if r["state"] != "FROZEN":
            return {"ok": False, "state": r["state"]}
        ref = get_custodian().payout(r["account_id"], r["amount"], withdrawal_id)
        return {"ok": True, "account_id": r["account_id"], "amount": r["amount"],
                "custodian_ref": ref, "hold": f"hold:{withdrawal_id}"}

    def mark_paid(self, withdrawal_id: str, custodian_ref: str) -> dict[str, Any]:
        """持牌方回执：条件更新保证幂等 —— 重复回执不会二次记账。"""
        r = conn().execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
        if not r:
            raise ValueError("提现单不存在")
        cur = conn().execute(
            "UPDATE withdrawals SET state='PAID', custodian_ref=? WHERE id=? AND state='FROZEN'",
            (custodian_ref, withdrawal_id))
        conn().commit()
        if cur.rowcount == 0:                      # 已 PAID / 已撤销：如实回现状
            return {"withdrawal_id": withdrawal_id, "state": r["state"],
                    "custodian_ref": r["custodian_ref"], "duplicate": True}
        publish("withdrawal.paid", {"withdrawal_id": withdrawal_id, "account_id": r["account_id"],
                                    "amount": r["amount"], "custodian_ref": custodian_ref})
        return {"withdrawal_id": withdrawal_id, "state": "PAID", "custodian_ref": custodian_ref}

    def list(self, account_id: str | None = None, limit: int = 50) -> list[dict]:
        if account_id:
            rows = conn().execute(
                "SELECT * FROM withdrawals WHERE account_id=? ORDER BY rowid DESC LIMIT ?", (account_id, limit)
            ).fetchall()
        else:
            rows = conn().execute("SELECT * FROM withdrawals ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


wallet = Wallet()
