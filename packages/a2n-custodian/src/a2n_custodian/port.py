"""持牌清算方端口（A1）。

这是全系统唯一能与外部资金系统对话的抽象。领域代码只依赖这个接口，
換持牌方 = 换一个实现类，业务代码零改动。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .media import DEFAULT_CURRENCY


@runtime_checkable
class CustodianPort(Protocol):
    """@runtime_checkable：实现类必须显式继承本 Protocol。

    历史上 MockCustodian 是鸭子类型（不继承），port 加了方法 mock 忘加，
    静态检查抓不到、只在运行时炸。继承 + runtime_checkable 之后，
    少一个方法在构造时就会报出来。
    """

    def deposit(self, account_ref: str, amount_fen: int, ref: str,
                currency: str = DEFAULT_CURRENCY) -> str:
        """充值：钱进入托管账户，返回托管流水号。"""

    def balance_fen(self) -> int:
        """托管账户余额（CNY 分口径）。对账的唯一外部真相。

        只计充值/打款（deposit/payout）：积分只随持牌方充值 1:1 增发、
        随打款销毁，只有这两类流水与积分总量构成恒等式；渠道过账
        （payin，如 x402 即付）不增发积分，不入锚。其他币种（如 USDC
        通道扣款）看 balance_of(currency)，不许混币累加。
        """

    def balance_of(self, currency: str = DEFAULT_CURRENCY) -> int:
        """某币种的流水净额（该币种整数最小单位）。分币种单列，不换算、不混加。

        这是通道流水的如实读数（含 payin），不是对账锚 —— 恒等式只锚 balance_fen。
        """

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

    def settle_payment(self, payment: dict, amount_minor: int, ref: str,
                       currency: str = DEFAULT_CURRENCY) -> tuple[bool, str]:
        """真正扣款（x402 settle）。成功才有资格把凭证标成 CAPTURED。

        带币种：x402 是多币种通道，不带币种等于让持牌方猜这个数是什么单位。
        """

    def payment_requirement(self, resource: str, amount_minor: int,
                            description: str = "",
                            currency: str = DEFAULT_CURRENCY) -> dict:
        """生成外部支付要求（x402 402 挑战体）。网络/资产/收款地址归本层。"""

    def payment_medium(self) -> str:
        """本清算方结算外部支付（x402 即付）所用的媒介（a2n-custodian.media 的 code）。

        "x402 的钱以什么形态存在"是资金侧知识，只能由持牌方声明——
        业务层只把这个字符串记进凭证快照，不猜不缓存。
        """
        return "channel_pay"
