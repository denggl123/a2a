"""a2n-deal（L3）：一期交易 —— 达成、双向计量、对账、出账。

一期不碰钱：这一层只产出"条款快照 + 双方事实 + 对账结果"，金额单位是**分**。
二期托管商发行积分后，账单直接变成分账指令的输入，本层接口不用改。

典型链路：

    accounts.create(owner, "对公-研发线")        # ① 维护多个账户
      → peers.propose(account, agent, terms)     # ② 与 agent 谈条款
      → peers.accept(link)                      # ③ 配对生效
      → deals.open(link, "ocr-pro")             # ④ 达成交易（条款当场冻结）
      → deals.report(...) × 2 方                # ⑤ 双方各自上报计量
      → deals.reconcile(deal)                   # ⑥ 对账：一致则认定，分歧转争议
      → statements.issue(link, "2026-09")       # ⑦ 出账
"""
from .deal import (NEXT_STATE, OPEN_STATES, RECON_ABS_TOL_FEN, RECON_REL_TOL,
                   Deals, compute_amount_fen, deals)
from .statement import (STATE_ISSUED, STATE_OPEN, STATE_SETTLED, Statements,
                        current_period, statements)

__all__ = [
    "Deals", "deals", "compute_amount_fen", "OPEN_STATES", "NEXT_STATE",
    "RECON_REL_TOL", "RECON_ABS_TOL_FEN",
    "Statements", "statements", "current_period",
    "STATE_OPEN", "STATE_ISSUED", "STATE_SETTLED",
]
