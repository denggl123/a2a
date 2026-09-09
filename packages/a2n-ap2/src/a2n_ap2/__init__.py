"""a2n-ap2（L4）：AP2 授权世界 与 A2N 结算世界 之间的翻译层。

先说清楚分工，否则两个协议会被混为一谈：

  AP2 解决的是 **Agent 替人买东西**（点对点消费）：
    人授权 → agent 下单 → 支付网络扣款。它关心"这笔钱我同意花吗"。
  A2N 解决的是 **Agent 替人干活**（任务结算）：
    调用 → 验收 → 多方分账。它关心"活干完了吗、该给谁分多少"。

两者必须集成，因为真实链路是连着的：
  用户授权一笔预算（AP2 Intent） → agent 拿它去雇别的 agent 干活（A2N 任务） →
  干完要把结算证据回填给用户和支付网络（AP2 Payment 问责）。

**A2N 在这里的三条边界**：
  1. 只翻译语义，不碰钱 —— 授权凭证进来，结算凭证出去，中间不动一分钱；
  2. 授权必须变成冻结 —— AP2 说"最多花 100"，A2N 里必须是真冻住 100，
     "承诺"和"冻结"的差别就是纠纷的差别；
  3. 结算凭证必须自带可验证证据 —— result_hash / 双向计量 / 公证章，
     否则 AP2 的问责链到了 A2N 这一环就断成"我说干完了"。
"""
from .bridge import (AccountabilityPack, BudgetTranslator, ReceiptBuilder,
                     new_cart, new_payment)
from .mandates import (CART, INTENT, PAYMENT, Mandate, MandateError,
                       validate_chain)

__all__ = ["Mandate", "MandateError", "validate_chain",
           "INTENT", "CART", "PAYMENT",
           "BudgetTranslator", "ReceiptBuilder", "AccountabilityPack",
           "new_cart", "new_payment"]
