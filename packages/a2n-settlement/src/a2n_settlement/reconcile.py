"""每日对账：积分总量 ≡ 托管余额。差一分即冻结提现并告警。"""
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
              "receipts", "outbox", "skills", "agents", "custodian_book"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
