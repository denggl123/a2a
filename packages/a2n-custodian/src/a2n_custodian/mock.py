"""MockCustodian：本地模拟持牌清算方。

关键语义 —— 分账指令不减少托管余额（钱还在托管里，只是归属变了），
只有打款才让钱真正离开托管体系。这与真实持牌方的资金流一致。
"""
from __future__ import annotations

from typing import Any

from a2n_store import conn
from a2n_kernel.hashing import new_id, now_iso
from .media import DEFAULT_CURRENCY
from .port import CustodianPort


class MockCustodian(CustodianPort):
    """显式继承端口：端口加方法而 mock 忘改时，构造即失败而不是运行时炸。"""
    def _book(self, kind: str, amount: int, detail: str, ref: str) -> str:
        bid = new_id("cb")
        conn().execute(
            "INSERT INTO custodian_book (id, kind, detail, amount, ref, created_at) VALUES (?,?,?,?,?,?)",
            (bid, kind, detail, amount, ref, now_iso()),
        )
        conn().commit()
        return bid

    def deposit(self, account_ref: str, amount_fen: int, ref: str) -> str:
        assert amount_fen > 0
        return self._book("deposit", amount_fen, f"充值 account={account_ref}", ref)

    def balance_fen(self) -> int:
        row = conn().execute("SELECT COALESCE(SUM(amount),0) AS b FROM custodian_book").fetchone()
        return int(row["b"])

    def create_settlement_order(self, splits: dict[str, int], ref: str) -> str:
        return self._book("settlement", 0, f"分账指令 {splits}", ref)

    def payout(self, account_ref: str, amount_fen: int, ref: str) -> str:
        assert amount_fen > 0
        return self._book("payout", -amount_fen, f"打款 account={account_ref}", ref)

    def verify_payment(self, requirement: dict, payment: dict) -> tuple[bool, str]:
        """Mock 校验：只认形状与金额，不联网。

        真实持牌方在这里做的是"找支付网络核签"；mock 只检查：
        凭证声明的金额 ≥ 要求金额，且带签名。判据与网络细节都不出本层。
        签名在凭证的 payload 里（x402 形状），顶层若有也认——但两处都没有就是没签。
        """
        if not payment or payment.get("scheme") != (requirement.get("scheme") or "exact"):
            return False, "支付凭证缺失或 scheme 不匹配"
        sig = payment.get("signature") or (payment.get("payload") or {}).get("signature")
        if not sig:
            return False, "支付凭证缺少签名"
        need = int(requirement.get("maxAmountRequired") or 0)
        paid = int((payment.get("payload") or {}).get("amount") or 0)
        if paid < need:
            return False, f"支付金额不足：{paid} < {need}"
        return True, "ok"

    def settle_payment(self, payment: dict, amount_minor: int, ref: str,
                       currency: str = DEFAULT_CURRENCY) -> tuple[bool, str]:
        """Mock 扣款：落地一笔托管流水，返回渠道流水号。币种随流水记账。"""
        if amount_minor <= 0:
            return False, "扣款金额必须大于 0"
        tx = new_id("tx")
        self._book("payin", amount_minor, f"外部支付扣款 {ref} ({currency})", tx)
        return True, tx

    def payment_requirement(self, resource: str, amount_minor: int,
                            description: str = "",
                            currency: str = DEFAULT_CURRENCY) -> dict:
        from .x402 import build_requirement
        return build_requirement(resource, amount_minor, description, currency)

    def payment_medium(self) -> str:
        """本 mock 清算方模拟的是"外部渠道扣款进托管"（CNY 分口径）。"""
        return "channel_pay"

    def book(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = conn().execute(
            "SELECT * FROM custodian_book ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


_custodian: CustodianPort | None = None


def get_custodian() -> CustodianPort:
    global _custodian
    if _custodian is None:
        _custodian = MockCustodian()
    return _custodian
