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


def test_settlements_excludes_verdict_only_and_survives_a_corrupt_row():
    store = LocalStore()
    _record(store, "svc_a", "task-a", {"state": "ACCEPTED", "verdict": {"passed": False}})
    _record(store, "svc_b", "task-b", {"state": "FAILED"})
    _record(store, "svc_c", "task-c", {"state": "ACCEPTED",
             "verdict": {"passed": True},
             "settlement": {"state": "NOT_CONFIGURED", "mode": "none", "amount_minor": 0}})
    _record(store, "svc_d", "task-d", {"state": "SETTLED",
             "settlement": {"state": "SETTLED", "mode": "direct", "amount_minor": 100}})

    # 手工塞一行损坏的 outcome：对账视图跳过它，绝不整体崩
    store._db.execute(
        "UPDATE calls SET outcome=? WHERE task_id='task-b'", (b"\xff\xfe not json",))
    store._db.commit()

    rows = store.recent_settlements()
    assert [r["task_id"] for r in rows] == ["task-d"], "验收和未配置结算不能冒充账"
    assert rows[0]["evidence_type"] == "settlement_record"


def test_settlements_respects_limit_and_empty_store():
    store = LocalStore()
    assert store.recent_settlements() == []
    for i in range(5):
        _record(store, "svc", f"task-{i}", {
            "state": "SETTLED", "receipt": {"task_id": f"task-{i}"}})
    assert len(store.recent_settlements(limit=3)) == 3


def test_settlements_scans_past_recent_free_calls():
    store = LocalStore()
    _record(store, "svc", "evidence", {"state": "ACCEPTED",
             "receipt": {"task_id": "evidence"}})
    for i in range(250):
        _record(store, "free", f"free-{i}", {"state": "ACCEPTED",
                "verdict": {"passed": True},
                "settlement": {"state": "NOT_CONFIGURED", "mode": "none"}})
    assert [row["task_id"] for row in store.recent_settlements(limit=1)] == ["evidence"]


def test_existing_encrypted_call_history_builds_index_once(tmp_path):
    class PlainProtector:
        def seal(self, raw):
            return raw
        def open(self, sealed):
            return sealed

    path = tmp_path / "old-runtime.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE calls (scope TEXT NOT NULL, task_id TEXT NOT NULL, "
               "fingerprint TEXT NOT NULL, state TEXT NOT NULL, request BLOB, "
               "outcome BLOB, created REAL NOT NULL, updated REAL NOT NULL, "
               "PRIMARY KEY(scope,task_id))")
    db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)", (
        "svc", "old-receipt", "f", "ACCEPTED", None,
        json.dumps({"state": "ACCEPTED", "receipt": {"task_id": "old-receipt"}}).encode(),
        1.0, 2.0))
    db.commit()
    db.close()
    store = LocalStore(path, PlainProtector())
    try:
        assert [row["task_id"] for row in store.recent_settlements()] == ["old-receipt"]
    finally:
        store.close()
