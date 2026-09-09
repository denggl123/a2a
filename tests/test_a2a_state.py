"""a2n-task 状态机测试：非法迁移拒绝 / A2A v1.0 映射 / cancel-fail。"""
from __future__ import annotations

import pytest

from a2n_kernel.hashing import new_id
from a2n_task import tasks
from a2n_task.a2a import (A2A_STATES, TRANSITIONS, can_transition, is_terminal,
                          to_a2a, a2a_view)
from tests.test_flow import demo_card
from a2n_registry import registry
from a2n_ledger import Ledger
from a2n_server.routers.custodian import DepositIn, deposit


def _mk_task(suffix: str, budget: int = 100):
    user, provider = f"acct:sm-u{suffix}", f"acct:sm-p{suffix}"
    deposit(DepositIn(account_id=user, amount_fen=budget))
    agent = registry.register(provider, demo_card("sm-" + suffix))
    registry.heartbeat(agent["agent_id"])
    t = tasks.create(user, "ocr-pro", {"x": suffix}, budget=budget,
                     preferred_agents=[agent["agent_id"]])
    return user, agent, t


# ---------- 迁移表 ----------

def test_happy_path_transitions_are_legal():
    for a, b in [("CREATED", "ASSIGNED"), ("ASSIGNED", "SUBMITTED"),
                 ("SUBMITTED", "ACCEPTED"), ("ACCEPTED", "SETTLED")]:
        assert can_transition(a, b)


def test_illegal_transitions_rejected():
    assert not can_transition("SETTLED", "REJECTED"), "结算完不能再打回"
    assert not can_transition("REJECTED", "SETTLED"), "打回后不能直接结算"
    assert not can_transition("SUBMITTED", "SETTLED"), "必须先验收再结算"
    assert not can_transition("CANCELED", "ASSIGNED")


def test_terminal_states():
    for s in ("SETTLED", "REJECTED", "FAILED", "CANCELED"):
        assert is_terminal(s)
        assert not TRANSITIONS[s]
    assert not is_terminal("ASSIGNED")


# ---------- A2A v1.0 映射 ----------

def test_a2a_state_mapping():
    assert to_a2a("CREATED") == "submitted"
    assert to_a2a("ASSIGNED") == "working"
    assert to_a2a("SUBMITTED") == "completed"      # 产物已交付
    assert to_a2a("SETTLED") == "completed"
    assert to_a2a("REJECTED") == "failed"          # 验收不通过 ≠ agent 拒单
    assert to_a2a("CANCELED") == "canceled"
    assert to_a2a("NO_SUCH") == "unknown"


def test_a2a_view_shape():
    task = {"id": "t9", "skill_id": "ocr-pro", "state": "SETTLED", "node_id": "ag:n",
            "amount": 5, "budget": 100, "result_hash": "rh", "card_hash": "ch",
            "result": {"text": "HELLO"}, "created_at": "T0", "updated_at": "T1"}
    v = a2a_view(task)
    assert v["id"] == "t9"
    assert v["status"]["state"] == "completed"
    assert v["artifacts"][0]["parts"][0]["data"] == {"text": "HELLO"}
    inner = v["metadata"]["x-a2n"]
    assert inner["state"] == "SETTLED" and inner["amount_points"] == 5


def test_a2a_view_string_result_becomes_text_part():
    task = {"id": "t8", "skill_id": "s", "state": "SUBMITTED", "result": "plain",
            "result_hash": "h"}
    v = a2a_view(task)
    assert v["artifacts"][0]["parts"][0] == {"type": "text", "text": "plain"}


# ---------- 端到端行为 ----------

def test_create_goes_created_then_assigned():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix)
    assert t["state"] == "ASSIGNED"
    raw = tasks.get(t["id"])
    assert raw["state"] in ("ASSIGNED", "SUBMITTED")


def test_full_settle_via_state_machine():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix)
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                       {"call_count": 2, "output_tokens": 10, "wall_time_ms": 30})
    assert res["passed"]
    after = tasks.get(t["id"])
    assert after["state"] == "SETTLED"
    v = tasks.a2a(t["id"])
    assert v["status"]["state"] == "completed"


def test_cancel_refunds_and_terminal():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=77)
    out = tasks.cancel(t["id"], user, reason="不要了")
    assert out["state"] == "CANCELED" and out["refunded"] == 77
    assert Ledger().balance(user) == 77
    assert Ledger().balance(f"hold:{t['id']}") == 0


def test_cancel_by_other_party_rejected():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix)
    with pytest.raises(ValueError, match="发起方"):
        tasks.cancel(t["id"], "acct:not-the-requester")


def test_fail_marks_terminal_and_refunds():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=55)
    out = tasks.fail(t["id"], reason="节点超时")
    assert out["state"] == "FAILED" and out["refunded"] == 55
    assert tasks.a2a(t["id"])["status"]["state"] == "failed"


def test_settled_task_cannot_be_canceled():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix)
    tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                 {"call_count": 1, "wall_time_ms": 20})
    with pytest.raises(ValueError, match="非法状态迁移"):
        tasks.cancel(t["id"], user)


def test_zero_billable_metering_is_rejected_not_charged():
    """无可计费计量 = 计量缺失，打回退款；绝不兜底成 1 积分。"""
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=50)
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                       {"gpu_seconds": 1.0, "wall_time_ms": 20})   # gpu_seconds 不可计费
    assert not res["passed"]
    assert any("可计费" in r for r in res["reasons"])
    assert Ledger().balance(f"hold:{t['id']}") == 0
    assert tasks.get(t["id"])["state"] == "REJECTED"


def test_a2a_states_are_closed_set():
    known = {"submitted", "working", "completed", "failed", "canceled",
             "rejected", "input-required", "auth-required", "unknown"}
    assert A2A_STATES == known
