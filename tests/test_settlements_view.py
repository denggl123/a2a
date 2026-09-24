"""控制台「收据与对账」的数据源：LocalStore.recent_settlements。

口径（用户拍板的模型）：每台电脑 = 普通节点 + 全功能控制台，只控制自己。
结算事实也一样 —— 自持模式没有全网总账，凭证就是本地留存的这份互签收据；
控制台呈现的是"我名下的成交与凭证"，不是别人的账。
"""
from __future__ import annotations

import json
import sqlite3

from a2n_sdk.storage import LocalStore


def _record(store: LocalStore, scope: str, task_id: str, outcome: dict | None) -> None:
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}")
    if outcome is not None:
        store.finish(scope, task_id, outcome)


def test_settlements_lists_rows_with_voucher_facts():
    store = LocalStore()
    _record(store, "svc_ocr", "task-r", {
        "state": "SETTLED", "receipt": {"task_id": "task-r", "sig": "x"},
        "settlement": {"amount": 500}, "verdict": {"passed": True},
        "metadata": {"skill": "ocr"}})
    _record(store, "svc_free", "task-free", {"state": "COMPLETED", "metadata": {"skill": "echo"}})
    _record(store, "svc_run", "task-running", None)

    rows = store.recent_settlements()
    assert [r["task_id"] for r in rows] == ["task-r"], "免费与未完成的不进对账名单"
    row = rows[0]
    assert row["has_receipt"] is True
    assert row["state"] == "SETTLED"
    assert row["settlement"] == {"amount": 500}
    assert row["verdict"] == {"passed": True}


def test_settlements_accepts_verdict_only_and_survives_a_corrupt_row():
    store = LocalStore()
    _record(store, "svc_a", "task-a", {"state": "ACCEPTED", "verdict": {"passed": False}})
    _record(store, "svc_b", "task-b", {"state": "FAILED"})

    # 手工塞一行损坏的 outcome：对账视图跳过它，绝不整体崩
    store._db.execute(
        "UPDATE calls SET outcome=? WHERE task_id='task-b'", (b"\xff\xfe not json",))
    store._db.commit()

    rows = store.recent_settlements()
    assert [r["task_id"] for r in rows] == ["task-a"], "损坏行如实跳过"
    assert rows[0]["has_receipt"] is False, "只有验收、没有互签收据也要如实标出"


def test_settlements_respects_limit_and_empty_store():
    store = LocalStore()
    assert store.recent_settlements() == []
    for i in range(5):
        _record(store, "svc", f"task-{i}", {
            "state": "SETTLED", "receipt": {"task_id": f"task-{i}"}})
    assert len(store.recent_settlements(limit=3)) == 3
