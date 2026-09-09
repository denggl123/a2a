"""M10 凭证链：只订阅事件，不影响业务。

设计语义：公证层挂掉只影响"章还没刻"，不影响任何业务，恢复后补刻即可。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_store import conn
from a2n_kernel.events import subscribe
from a2n_kernel.hashing import GENESIS, chain_hash, new_id, now_iso

KEY_EVENTS = {
    "deposit.confirmed", "points.minted", "points.burned",
    "node.registered", "node.verified", "node.suspended", "node.blacklisted",
    "task.created", "task.assigned", "task.submitted",
    "task.canceled", "task.failed",
    "acceptance.passed", "acceptance.failed",
    "arbitration.opened", "arbitration.resolved",
    "settlement.ordered", "settlement.settled", "settlement.refunded",
    "epoch.sealed", "epoch.anchored", "epoch.rejected",
    "withdrawal.requested", "withdrawal.paid",
    "reconciliation.failed",
}


class Notary:
    def _last(self) -> tuple[int, str]:
        row = conn().execute("SELECT seq, hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["hash"]) if row else (0, GENESIS)

    def record(self, event_type: str, payload: dict[str, Any]) -> dict | None:
        if event_type not in KEY_EVENTS:
            return None
        c = conn()
        _, prev = self._last()
        rid = new_id("rc")
        ts = now_iso()
        h = chain_hash(prev, {"rid": rid, "event_type": event_type, "payload": payload, "ts": ts})
        c.execute(
            "INSERT INTO receipts (rid, event_type, payload, prev_hash, hash, created_at) VALUES (?,?,?,?,?,?)",
            (rid, event_type, json.dumps(payload, ensure_ascii=False), prev, h, ts),
        )
        c.commit()
        return {"rid": rid, "event_type": event_type, "hash": h, "created_at": ts}

    def verify(self) -> tuple[bool, str]:
        rows = conn().execute("SELECT * FROM receipts ORDER BY seq ASC").fetchall()
        prev = GENESIS
        for r in rows:
            payload = json.loads(r["payload"])
            expect = chain_hash(
                prev,
                {"rid": r["rid"], "event_type": r["event_type"], "payload": payload, "ts": r["created_at"]},
            )
            if r["prev_hash"] != prev:
                return False, f"凭证链断裂于 {r['rid']}"
            if r["hash"] != expect:
                return False, f"凭证被篡改于 {r['rid']}"
            prev = r["hash"]
        return True, f"完整，共 {len(rows)} 枚章"

    def recent(self, limit: int = 50) -> list[dict]:
        rows = conn().execute("SELECT * FROM receipts ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


notary = Notary()
subscribe("*", lambda e: notary.record(e.get("type", "?"), {k: v for k, v in e.items() if k != "type"}))
