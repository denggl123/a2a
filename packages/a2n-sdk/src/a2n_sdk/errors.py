"""SDK 异常：把服务端的门禁语义翻成调用方可判别的类型。

"需要支付"（402）和"没资格"（403）是两回事：前者带钱重来就行，后者要先去
补一种支付方式。调用方要能靠异常类型做机器判别，而不是去解析错误文案。
"""
from __future__ import annotations


class A2NError(Exception):
    """SDK/服务端错误的基类。"""


class PaymentRequiredError(A2NError):
    """x402：该调用需要支付，但（重试时）没带有效凭证。

    `requirement` 就是服务端签发的 402 挑战体——把它交给你的支付方，拿到
    凭证后用 `call_agent(..., payment=凭证)` 重来。
    """

    def __init__(self, requirement: dict | None = None, message: str = "需要支付") -> None:
        super().__init__(message)
        self.requirement = requirement or {}


class CallDeniedError(A2NError):
    """没资格调用：该 agent 收费，而你当前没有任何可用支付方式。

    `hint` 是"我与这个 agent 的支付能力交集"：accepted / mine_channels /
    can_call_with / missing —— 差哪样、补哪样，一目了然。
    """

    def __init__(self, message: str, hint: dict | None = None) -> None:
        super().__init__(message)
        self.hint = hint or {}


__all__ = ["A2NError", "PaymentRequiredError", "CallDeniedError"]
