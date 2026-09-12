"""a2n-account（L2）：主体多账户 + 对等账户配对。

一期的全部内容：**建立能达成交易的关系，不碰资金。**

- `accounts`：一个主体可以持有多个结算账户（对公/对私/海外/不同业务线）。
- `peers`：账户与 agent 的配对。条款（单价、账期、额度）谈妥并双方确认后，
  这对账户之间才能达成交易 —— 把陌生人交易变成熟人交易。
- `settle`：agent 在 card 里声明自己接受哪些结算方式，供发现层筛选。

金额单位一律用**分（fen）**。积分是二期的事：托管商发行后 1 积分 = 0.01 元，
这里的账单金额可 1:1 兑换，但一期不引入任何账户余额概念。
"""
from .accounts import (DEFAULT_TERMS, DIRECT_PAY, Accounts, Peers, PEER_ACCOUNT,
                       PREPAID_POINTS, SETTLE_MODES, X402, accounts, merge_terms,
                       peers)
from .paymethods import PayMethods, paymethods
from .settle import (PEER_ACCOUNT, PREPAID_POINTS, SETTLE_MODES, accepts_of_agent,
                     accepts_of_card, compatible_with, direct_channels, supports)
# 渠道绑定引导是持牌层的品牌知识（铁律二：品牌词只出现在 a2n-custodian）；
# 这里 re-export 给账户域的调用方一个稳定的入口。
from a2n_custodian import (all_guides, guide_of, masked_ref, ref_detail, ref_of,
                           register_guide, validate_binding)

__all__ = [
    "Accounts", "Peers", "accounts", "peers", "DEFAULT_TERMS", "merge_terms",
    "PEER_ACCOUNT", "PREPAID_POINTS", "DIRECT_PAY", "X402", "SETTLE_MODES",
    "PayMethods", "paymethods",
    "accepts_of_agent", "accepts_of_card", "direct_channels", "supports",
    "compatible_with",
    "all_guides", "guide_of", "register_guide", "validate_binding",
    "ref_of", "ref_detail", "masked_ref",
]
