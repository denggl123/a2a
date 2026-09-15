"""每日对账：积分总量 ≡ 托管余额。差一分即冻结提现并告警。

口径：托管侧读 balance_fen —— 只算 CNY、只计充值/打款（deposit/payout）。
积分只随持牌方充值 1:1 增发、随打款销毁，所以只有这两类流水与积分总量
构成恒等式；渠道过账（payin，如 x402 即付）不增发积分，不入锚 ——
"托管多了钱、积分没多"是假差额，会误冻结提现。其他币种（如 x402 的
USDC 扣款）按币种单列也不入锚：混进来就是拿 USDC 的最小单位（10⁻⁶）
去比 CNY 的分，同样产生假差额。
"""
from __future__ import annotations

from typing import Any

from a2n_custodian import get_custodian
from a2n_store import conn
from a2n_ledger import Ledger
from a2n_kernel.events import publish
from a2n_kernel.hashing import now_iso

FROZEN_FLAG = {"frozen": False, "since": None, "diff": 0}


def reconcile() -> dict[str, Any]:
    points = Ledger().total_points()
    escrow_fen = get_custodian().balance_fen()
    diff = points - escrow_fen
    result = {
        "points_total": points,
        "escrow_balance_fen": escrow_fen,
        "diff": diff,
        "balanced": diff == 0,
        "checked_at": now_iso(),
        "withdrawals_frozen": FROZEN_FLAG["frozen"],
    }
    if diff != 0:
        FROZEN_FLAG.update({"frozen": True, "since": now_iso(), "diff": diff})
        publish("reconciliation.failed", result)
    else:
        FROZEN_FLAG.update({"frozen": False, "since": None, "diff": 0})
    return result


def is_frozen() -> bool:
    return FROZEN_FLAG["frozen"]


def ledger_chain_ok() -> tuple[bool, str]:
    return Ledger().verify_chain()


def wipe_demo_data() -> None:
    """仅用于本地演示重置。生产环境不存在此函数。"""
    c = conn()
    for t in ("tasks", "usage_reports", "settlement_orders", "withdrawals",
              "receipts", "outbox", "skills", "agents", "custodian_book",
              "ratings", "trial_offers", "settlements", "reconciliations"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
