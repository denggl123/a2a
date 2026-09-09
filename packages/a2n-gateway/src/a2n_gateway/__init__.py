"""a2n-gateway（L5 入口）：调用编排。

把"门禁 → 执行 → 验收 → 记账"从 HTTP 路由里抽出来，成为**入口无关**的一条链。
A2A 只是它的一个适配面；以后接别的协议，写个适配层调用 `invoke` 即可，
规矩不用重写一遍。

红线不变：这一层不碰钱。资金动作（校验凭证、真扣款）一律经
`a2n-custodian` 端口，业务层只知道"通过/不通过"。
"""
from .call import CallResult, invoke
from .gate import (FREE, PaymentRequired, charging, choose_currency, resolve,
                   unit_price_fen)
from .settle import (Capability, STATE_AUTHORIZED, STATE_CAPTURED, STATE_FAILED,
                     capture, capture_after_payment, dispatch, fail_charge,
                     mandate_chain, record_charge, record_peer)

__all__ = [
    "invoke", "CallResult",
    "resolve", "charging", "choose_currency", "unit_price_fen",
    "PaymentRequired", "FREE",
    "Capability", "dispatch", "mandate_chain",
    "record_peer", "record_charge", "capture", "capture_after_payment", "fail_charge",
    "STATE_AUTHORIZED", "STATE_CAPTURED", "STATE_FAILED",
]
