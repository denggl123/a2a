"""第二轮整顿的回归护栏：事件同事务、任务终态口径、成交混币拒绝。

这些口径问题不会让测试报错，只会让数据悄悄失真（事件丢、成功率恒 0、
跨币种比大小）—— 所以必须钉死。
"""
from __future__ import annotations

import uuid

import pytest

from a2n_deal.deal import Deals
from a2n_market import stats
from a2n_registry import registry


def _card(skill: str) -> dict:
    return {"name": "t-" + skill, "url": "http://localhost:9000/a2a",
            "skills": [{"id": skill, "tags": []}], "x-a2n": {}}


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------- 1. 事件与业务同事务 ----------

def test_outbox_follows_business_transaction():
    """业务回滚 → 事件一起消失；独立事件自己提交。"""
    from a2n_store import conn, outbox

    c = conn()
    c.execute("BEGIN IMMEDIATE")
    ev_in_tx = outbox.append("test.in_tx", {"v": 1})   # 事务内：不自提交
    c.rollback()
    assert conn().execute("SELECT 1 FROM outbox WHERE id=?", (ev_in_tx,)).fetchone() is None

    ev_solo = outbox.append("test.solo", {"v": 2})     # 无业务写入：自己提交
    assert conn().execute("SELECT 1 FROM outbox WHERE id=?", (ev_solo,)).fetchone()


def test_register_event_committed_with_business():
    """注册成功 = agent 行与 node.registered 事件同时可见。"""
    from a2n_store import conn
    skill = _uniq("s-ev")
    a = registry.register("p-ev", _card(skill))
    n = conn().execute(
        "SELECT COUNT(*) n FROM outbox WHERE event_type='node.registered'"
        " AND payload LIKE ?", (f'%{a["agent_id"]}%',)).fetchone()["n"]
    assert n == 1


# ---------- 2. 行情统计的终态口径 ----------

def test_market_counts_accepted_as_done():
    """非积分模式（对等账户/直付/x402）任务停在 ACCEPTED：也算完成。

    以前只数 SETTLED，主流量模式的成功率与完成数恒为 0。
    """
    from a2n_kernel.hashing import new_id
    from a2n_store import conn
    tid = new_id("t")
    conn().execute(
        "INSERT INTO tasks (id, requester_id, skill_id, payload, budget, unit_prices,"
        " state, currency, amount_minor, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (tid, "req-mkt", _uniq("s-mkt"), "{}", 100, "{}", "ACCEPTED", "CNY", 100))
    conn().commit()
    s = stats()
    assert s["tasks_settled"] >= 1 and s["success_rate"] > 0


# ---------- 3. 成交换币拒绝 ----------

def test_deal_rejects_currency_mismatch():
    """条款是 CNY，却想以 USDC 成交：敞口与额度会跨量纲比较，必须拒绝。"""
    from a2n_account import accounts, peers
    a = registry.register("p-deal", _card(_uniq("s-d")))
    acc = accounts.create(_uniq("owner"), "测试账户")
    link = peers.propose(acc["account_id"], a["agent_id"],
                         terms={"currency": "CNY", "credit_limit_fen": 10000},
                         auto_accept=True)
    assert link["state"] == "ACTIVE"
    with pytest.raises(ValueError, match="拒绝混币"):
        Deals().open(link["link_id"], _uniq("s-deal"), currency="USDC")
