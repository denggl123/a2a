"""仲裁工作台测试：开单 / 裁定 / 装配层自动执行退款。"""
from __future__ import annotations

import pytest

from a2n_acceptance.dispute import disputes
from a2n_kernel.events import subscribe
from a2n_kernel.hashing import new_id
from a2n_ledger import Ledger
from a2n_registry import registry
from a2n_server import wiring
from a2n_server.routers.custodian import DepositIn, deposit
from a2n_task import tasks
from tests.test_flow import demo_card


@pytest.fixture(scope="module", autouse=True)
def _wired():
    wiring.wire()


def _mk_task(suffix: str, budget: int = 100):
    user, provider = f"acct:arb-u{suffix}", f"acct:arb-p{suffix}"
    deposit(DepositIn(account_id=user, amount_fen=budget))
    agent = registry.register(provider, demo_card("arb-" + suffix))
    registry.heartbeat(agent["agent_id"])
    t = tasks.create(user, "ocr-pro", {"x": suffix}, budget=budget,
                     preferred_agents=[agent["agent_id"]])
    return user, agent, t


def test_open_and_get_dispute():
    suffix = new_id("")[2:]
    d = disputes.open("t-fake-" + suffix, "node", "时长双向对账差异过大", opened_by="system",
                      evidence={"reasons": ["超容差"]})
    assert d["state"] == "OPEN" and d["side"] == "node"
    assert disputes.get(d["id"])["evidence"] == {"reasons": ["超容差"]}


def test_invalid_side_rejected():
    with pytest.raises(ValueError):
        disputes.open("t-x", "audience", "不是申诉方")


def test_resolve_uphold_keeps_no_refund():
    suffix = new_id("")[2:]
    d = disputes.open("t-" + suffix, "node", "申诉")
    r = disputes.resolve(d["id"], "uphold_reject", arbitrator="arb-zhang")
    assert r["state"] == "RESOLVED" and r["ruling"] == "uphold_reject"
    assert r["refund_fen"] == 0


def test_resolve_overturn_requires_refund_amount():
    d = disputes.open("t-" + new_id("")[2:], "node", "申诉")
    with pytest.raises(ValueError, match="退款额"):
        disputes.resolve(d["id"], "overturn_pay", arbitrator="a")


def test_double_resolve_rejected():
    d = disputes.open("t-" + new_id("")[2:], "node", "申诉")
    disputes.resolve(d["id"], "uphold_reject", arbitrator="a")
    with pytest.raises(ValueError, match="已裁定"):
        disputes.resolve(d["id"], "uphold_reject", arbitrator="a")


def test_unknown_ruling_rejected():
    d = disputes.open("t-" + new_id("")[2:], "node", "申诉")
    with pytest.raises(ValueError, match="未知裁定"):
        disputes.resolve(d["id"], "give_me_money", arbitrator="a")


def test_acceptance_failure_auto_opens_dispute():
    """装配线：验收不通过 → 自动开争议单，节点可申诉。"""
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=50)
    # 无可计费计量维度 → 验收必不通过（不会被兜底成零元单）
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                       {"gpu_seconds": 1.0, "wall_time_ms": 20})
    assert not res["passed"]
    ds = disputes.list(task_id=t["id"])
    assert ds, "验收失败应自动开单"
    assert ds[0]["opened_by"] == "system"


def test_arbitration_overturn_pays_back_via_wiring():
    """装配线：改判 → 结算层按份额追回 → 退回使用方。全程总量守恒。"""
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=80)
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                       {"call_count": 1, "wall_time_ms": 30})
    assert res["passed"]
    node_got = res["amount"]

    d = disputes.open(t["id"], "node", "平台观测有偏差，任务确实达标", opened_by="node")
    r = disputes.resolve(d["id"], "overturn_pay", arbitrator="arb-li",
                         refund_points=node_got, resolution="观测时长误差属网络抖动")
    assert r["state"] == "RESOLVED"

    # 装配线已自动执行退款（订阅 arbitration.resolved → settlement.refund）
    from a2n_settlement.reconcile import reconcile
    assert Ledger().balance(f"hold:{t['id']}") == 0
    assert reconcile()["balanced"], "仲裁退款后总量必须仍等于托管"


def test_arbitration_partial_refund():
    suffix = new_id("")[2:]
    user, agent, t = _mk_task(suffix, budget=60)
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "X"},
                       {"call_count": 1, "wall_time_ms": 30})
    assert res["passed"]
    d = disputes.open(t["id"], "requester", "结果质量不满", opened_by="requester")
    r = disputes.resolve(d["id"], "partial", arbitrator="arb-wang",
                         refund_points=1, resolution="部分补偿")
    assert r["ruling"] == "partial"
    from a2n_settlement.reconcile import reconcile
    assert reconcile()["balanced"]
