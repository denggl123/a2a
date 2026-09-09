"""使用方支付方式登记：直付类结算的"我加个支付渠道"。

哲学与 party_accounts 完全一致：**A2N 不碰钱、不校验真伪、不认识品牌**。
只记录"这个主体声明自己在某个渠道有账户，并愿意用它即时支付"。
渠道是自由字符串、属于数据——品牌知识归持牌托管层（a2n-custodian），
业务层只认 direct_pay + 渠道字符串。
资金流走渠道，账（授权链、单价快照、计量、凭证）走 A2N。

门禁语义：agent 声明 accepts 含 direct_pay:<渠道>（或裸 direct_pay）
→ 使用方登记过 ACTIVE 的同款渠道 → 交集非空，即可调用。
随时可加、随时可注销，不需要找任何人开通。
"""
from __future__ import annotations

from typing import Any

from a2n_kernel import new_id, now_iso
from a2n_store import conn

from .accounts import DIRECT_PAY

STATE_ACTIVE = "ACTIVE"
STATE_CLOSED = "CLOSED"


class PayMethods:
    """主体登记的直付渠道。method 恒为 direct_pay，channel 是自由字符串。

    channel 结算什么币种，是渠道的资金属性：登记时显式指定（默认 CNY），
    币种必须能落到已注册的媒介上（a2n-custodian.media）——门禁选币时
    用它判断"这个渠道付不出美元"。
    """

    def register(self, principal_id: str, channel: str, ref: str | None = None,
                 currency: str = "CNY") -> dict:
        if not principal_id:
            raise ValueError("缺少主体（X-Principal）")
        channel = (channel or "").strip().lower()
        if not channel or ":" in channel:
            raise ValueError("channel 必填且不能含限定符（直接给渠道名，不是 'direct_pay:xx'）")
        from a2n_custodian import UnknownMedium, by_currency
        candidates = by_currency(currency)
        if not candidates:
            raise UnknownMedium(f"币种 {currency} 没有已注册的媒介（先在持牌层注册）")
        medium_code = candidates[0]["code"]
        pm_id = f"pm_{new_id('')}"
        ts = now_iso()
        conn().execute(
            "INSERT INTO payment_methods (pm_id, principal_id, method, channel, ref,"
            " status, created_at, medium, currency) VALUES (?,?,?,?,?,?,?,?,?)",
            (pm_id, principal_id, DIRECT_PAY, channel, ref, STATE_ACTIVE, ts,
             medium_code, currency.upper()))
        conn().commit()
        return self.get(pm_id) or {}

    def get(self, pm_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM payment_methods WHERE pm_id=?", (pm_id,)).fetchone()
        return dict(r) if r else None

    def list_by_owner(self, principal_id: str, channel: str | None = None,
                      only_active: bool = True) -> list[dict]:
        q = "SELECT * FROM payment_methods WHERE principal_id=?"
        args: list[Any] = [principal_id]
        if channel:
            q += " AND channel=?"
            args.append(channel)
        if only_active:
            q += " AND status=?"
            args.append(STATE_ACTIVE)
        return [dict(r) for r in conn().execute(q + " ORDER BY created_at", args).fetchall()]

    def channels(self, principal_id: str) -> set[str]:
        """主体已登记的全部 ACTIVE 渠道。"""
        return {r["channel"] for r in self.list_by_owner(principal_id)}

    def pick(self, principal_id: str, channel: str) -> dict | None:
        """选一张该渠道的 ACTIVE 登记记录（先登先用）。"""
        rows = self.list_by_owner(principal_id, channel, only_active=True)
        return rows[0] if rows else None

    def close(self, pm_id: str, principal_id: str) -> dict:
        pm = self.get(pm_id)
        if not pm:
            raise ValueError("支付方式不存在")
        if pm["principal_id"] != principal_id:
            raise PermissionError("只能注销自己登记的支付方式")
        conn().execute("UPDATE payment_methods SET status=? WHERE pm_id=?",
                       (STATE_CLOSED, pm_id))
        conn().commit()
        return self.get(pm_id) or {}


paymethods = PayMethods()
