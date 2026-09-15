"""a2n-settlement（L3 业务）：分账与对账。

分账只发指令，钱一直在持牌方托管账户内变动（只改归属）。
reconcile 每日核对「积分总量 ≡ 托管余额」，差一分即冻结提现并告警。
closing 是 P1 的**结算收口**：两条记账路的统一触发点 + 幂等（task_id 为键）
+ 失败进待处理（绝不静默）+ 日切落库与报警。
"""
from . import closing
from .closing import (MODE_FREE, MODE_POINTS, STATE_FAILED, STATE_PENDING,
                      STATE_SETTLED)
from .service import BILLABLE_DIMS, Settlement, compute_amount, settlement
from .dims import (DIMS, UnknownDimension, exists, filter_billable, is_billable,
                   is_verifiable, list_dims, observed_only, register_dimension)
from .price import (DEFAULT_CURRENCY, entries_of, is_free, price_book, quote,
                    quote_card, snapshot, supported_currencies, unit_price_of)
# 注意：不在这里导出函数名 `reconcile` —— 那会把 `a2n_settlement.reconcile`
# （子模块，历史用法 `reconcile.reconcile()`）遮掉，同一个名字两处语义。
from .reconcile import is_frozen

__all__ = ["Settlement", "settlement", "compute_amount", "BILLABLE_DIMS",
           "DIMS", "register_dimension", "is_billable", "is_verifiable",
           "exists", "list_dims", "filter_billable", "observed_only",
           "UnknownDimension",
           "price_book", "supported_currencies", "entries_of", "snapshot",
           "quote", "quote_card", "unit_price_of", "is_free", "DEFAULT_CURRENCY",
           "closing", "is_frozen",
           "STATE_SETTLED", "STATE_PENDING", "STATE_FAILED",
           "MODE_POINTS", "MODE_FREE"]
