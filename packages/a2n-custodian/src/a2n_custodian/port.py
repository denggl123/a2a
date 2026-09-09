"""持牌清算方端口（A1）。

这是全系统唯一能与外部资金系统对话的抽象。领域代码只依赖这个接口，
換持牌方 = 换一个实现类，业务代码零改动。
"""
from __future__ import annotations

from typing import Protocol


class CustodianPort(Protocol):
    def deposit(self, account_ref: str, amount_fen: int, ref: str) -> str:
        """充值：钱进入托管账户，返回托管流水号。"""

    def balance_fen(self) -> int:
        """托管账户余额（分）。对账的唯一外部真相。"""

    def create_settlement_order(self, splits: dict[str, int], ref: str) -> str:
        """分账指令：只在托管账户内部改变归属，不改变托管总额。"""

    def payout(self, account_ref: str, amount_fen: int, ref: str) -> str:
        """打款：钱真正离开托管体系。"""

    def verify_payment(self, requirement: dict, payment: dict) -> tuple[bool, str]:
        """校验外部支付凭证（x402 的 facilitator 角色）。

        只回答"这张凭证是不是真的、够不够付这笔"，不回答别的。
        业务层拿到 (ok, 原因) 之后决定放行或拒绝 —— 判据在业务，
        真伪在持牌方。A2N 代码里不该出现任何支付网络的细节。
        """

    def settle_payment(self, payment: dict, amount_fen: int, ref: str) -> tuple[bool, str]:
        """真正扣款（x402 settle）。成功才有资格把凭证标成 CAPTURED。"""

    def payment_requirement(self, resource: str, amount_fen: int,
                            description: str = "") -> dict:
        """生成外部支付要求（x402 402 挑战体）。网络/资产/收款地址归本层。"""

    def payment_medium(self) -> str:
        """本清算方结算外部支付（x402 即付）所用的媒介（a2n-custodian.media 的 code）。

        "x402 的钱以什么形态存在"是资金侧知识，只能由持牌方声明——
        业务层只把这个字符串记进凭证快照，不猜不缓存。
        """
        return "channel_pay"
