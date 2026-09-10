"""a2n-custodian（L1 资产）：持牌清算方适配器。

**全仓库唯一允许与外部资金系统通信的包。** 其余任何包 import 支付 SDK
都会被架构测试判失败（tests/test_architecture.py）。

v1 只有 MockCustodian，接口与真实持牌方对齐后换驱动类即可，业务代码不动。
"""
from .mock import MockCustodian, get_custodian
from .port import CustodianPort
from .media import (MEDIA, DEFAULT_MEDIUM, UnknownMedium, by_currency, currency_of,
                    exponent_of, get_medium, list_media, medium_of, money,
                    register_medium, same_currency, supports, to_minor)
from .channel_guides import (all_guides, guide_of, masked_ref, ref_detail, ref_of,
                             register_guide, validate_binding)
from .x402 import (PAYMENT_HEADER, X402_VERSION, amount_of, build_requirement,
                   encode_payment, parse_payment)

__all__ = ["CustodianPort", "MockCustodian", "get_custodian",
           "X402_VERSION", "PAYMENT_HEADER",
           "build_requirement", "parse_payment", "encode_payment", "amount_of",
           "MEDIA", "DEFAULT_MEDIUM", "UnknownMedium", "register_medium",
           "get_medium", "medium_of", "list_media", "by_currency",
           "currency_of", "exponent_of", "supports", "money", "to_minor",
           "same_currency",
           "all_guides", "guide_of", "register_guide", "validate_binding",
           "ref_of", "ref_detail", "masked_ref"]
