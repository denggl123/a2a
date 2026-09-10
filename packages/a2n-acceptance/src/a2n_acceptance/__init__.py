"""a2n-acceptance（L3 业务）：活干得算不算数。

维度有三条性质：可计量、可验证、可复算。只有"可计量 + 可验证"的维度能进分账。
节点自报是内部真相、平台观测是外部真相，谁的单方口径都不采信。
"""
from .dispute import (OVERTURN_PAY, PARTIAL, UPHOLD_REJECT, RULINGS, Disputes,
                      disputes)
from .service import (VARIANCE_TOLERANCE, BaselineSamplePolicy, judge, policy_ref,
                      set_policy)

__all__ = ["judge", "policy_ref", "set_policy", "BaselineSamplePolicy",
           "VARIANCE_TOLERANCE",
           "disputes", "Disputes", "UPHOLD_REJECT", "OVERTURN_PAY", "PARTIAL", "RULINGS"]
