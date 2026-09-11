"""事件总线的事务语义：落库随事务原子、订阅者通知推迟到提交后、回滚丢弃。

为什么这组断言重要：publish 如果背着事务就地通知订阅者，订阅者自己
的写库动作会 commit —— 把发布方的事务提前提交，原子性当场失效。
正确语义 = 落库随事务（业务回滚事件也回滚）+ 通知在提交后才送达。
"""
from __future__ import annotations

import sqlite3

from a2n_kernel import events
from a2n_store import conn, tx


def test_standalone_publish_notifies_immediately_and_persists():
    seen = []
    events.subscribe("t.standalone", lambda e: seen.append(e["n"]))
    events.publish("t.standalone", {"n": 1})
    assert seen == [1]
    row = conn().execute(
        "SELECT 1 FROM outbox WHERE event_type='t.standalone' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row, "独立事件必须落 outbox"


def test_publish_inside_tx_defers_notification_until_commit():
    seen = []
    events.subscribe("t.deferred", lambda e: seen.append(conn().in_transaction))
    with tx():
        events.publish("t.deferred", {"n": 1})
        assert seen == [], "提交前不许通知订阅者（否则订阅者的写库会提前提交事务）"
    assert seen == [False], "提交后送达，且此刻已不在事务里"


def test_rollback_discards_event_and_notification():
    seen = []
    events.subscribe("t.rollback", lambda e: seen.append(1))
    try:
        with tx():
            events.publish("t.rollback", {"n": 1})
            raise RuntimeError("business failed")
    except RuntimeError:
        pass
    assert seen == [], "回滚了就不该有通知"
    row = conn().execute("SELECT 1 FROM outbox WHERE event_type='t.rollback'").fetchone()
    assert not row, "事件落库随业务回滚"


def test_event_and_business_commit_together():
    """业务写入与事件 outbox 同事务：提交后两者都可查（回归保护）。"""
    marker = "biz-" + str(id(object()))
    with tx():
        conn().execute(
            "INSERT INTO accounts (id, kind, name, kyc_status, created_at)"
            " VALUES (?, 'user', 'x', 'pending', '2026-09-10')", (marker,))
        events.publish("t.atomic", {"marker": marker})
    assert conn().execute("SELECT 1 FROM accounts WHERE id=?", (marker,)).fetchone()
    ev = conn().execute(
        "SELECT payload FROM outbox WHERE event_type='t.atomic' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert ev and marker in ev["payload"]


def test_subscriber_own_transaction_after_commit():
    """订阅者在提交后执行时拥有干净的连接状态（可自己开事务写库）。"""
    results = []

    def handler(e):
        try:
            with tx():
                assert conn().in_transaction
            results.append("ok")
        except sqlite3.OperationalError as exc:      # 不允许"嵌套事务"这类错误
            results.append(f"err:{exc}")

    events.subscribe("t.subtx", handler)
    with tx():
        events.publish("t.subtx", {})
    assert results == ["ok"]
