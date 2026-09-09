"""Outbox：领域事件的持久化落点（内核只负责分发，落库在这里）。"""
from __future__ import annotations

import json
from typing import Any

from a2n_kernel.hashing import new_id, now_iso
from .db import conn


def append(event_type: str, payload: dict[str, Any]) -> str:
    ev = new_id("ev")
    c = conn()
    c.execute(
        "INSERT INTO outbox (id, event_type, payload, created_at) VALUES (?,?,?,?)",
        (ev, event_type, json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    c.commit()
    return ev


def recent(limit: int = 50) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM outbox ORDER BY rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
