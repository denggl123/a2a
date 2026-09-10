"""Outbox：领域事件的持久化落点（内核只负责分发，落库在这里）。

事务语义：**调用方已有未提交的业务写入时，事件随同一个事务提交** ——
业务回滚事件也回滚，中间崩溃两者同生共死。以前这里无条件自提交，
且调用方都是"先 commit 再 publish"，中间一崩就是"业务成了、事件丢了"。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_kernel.hashing import new_id, now_iso
from .db import conn


def append(event_type: str, payload: dict[str, Any]) -> str:
    ev = new_id("ev")
    c = conn()
    in_tx = c.in_transaction   # 进入时是否已背着未提交的业务写入
    c.execute(
        "INSERT INTO outbox (id, event_type, payload, created_at) VALUES (?,?,?,?)",
        (ev, event_type, json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    if not in_tx:
        c.commit()             # 独立事件（无业务写库）：自己提交
    return ev


def recent(limit: int = 50) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM outbox ORDER BY rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
