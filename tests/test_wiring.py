"""装配层测试：模块之间的线，一条条验。"""
from __future__ import annotations

import json

import pytest

from a2n_consensus import consensus
from a2n_kernel import events
from a2n_kernel.hashing import new_id
from a2n_ledger import Ledger
from a2n_server import wiring
from a2n_store import conn


@pytest.fixture(scope="module", autouse=True)
def _wired():
    wiring.wire()


def test_wire_is_idempotent():
    s1 = wiring.status()
    wiring.wire()
    s2 = wiring.status()
    assert s1 == s2
    assert s1["wired"] and s1["anchor_ready"] and s1["witnesses"] >= 1


def test_consensus_wired_to_ledger_and_custodian():
    suffix = new_id("")[2:]
    led = Ledger()
    led.post(f"acct:w{suffix}", 3, "test", suffix)
    led.post(f"acct:w{suffix}-b", -3, "test", suffix)
    ep = wiring.maybe_seal_epoch(threshold=1)
    assert ep is not None and ep.entry_count >= 2
    assert ep.escrow_balance_fen > 0, "资金锚读数必须来自持牌方"


def test_maybe_seal_respects_threshold():
    head = Ledger().head_rowid()
    assert wiring.maybe_seal_epoch(threshold=10_000_000) is None


def test_epoch_anchored_broadcasts_over_p2p():
    """接线：锚定 → 向 gossip 网络广播 ANCHOR（p2p 节点由 attach_p2p 挂上）。"""
    class FakeNet:
        def __init__(self):
            self.sent = []

        def gossip(self, msg_type, payload, ttl=5):
            self.sent.append({"type": msg_type, "payload": payload})
            return 1

    net = FakeNet()
    wiring.attach_p2p(net)

    suffix = new_id("")[2:]
    led = Ledger()
    led.post(f"acct:b{suffix}", 2, "test", suffix)
    led.post(f"acct:b{suffix}-b", -2, "test", suffix)
    ep = wiring.maybe_seal_epoch(threshold=1)
    for did in wiring.witness_dids():
        sig = wiring.witness_sign_epoch(ep.epoch_id, did)
        consensus.witness_sign(ep.epoch_id, sig["signer"], sig["sig"])
    consensus.add_anchor(ep.epoch_id, wiring.anchor_sign_epoch(ep.epoch_id))
    r = consensus.finalize(ep.epoch_id)
    assert r["passed"]

    anchors = [x for x in net.sent if x["type"] == "ANCHOR"
               and x["payload"].get("epoch_id") == ep.epoch_id]
    assert anchors, "锚定事件必须向网络广播 ANCHOR"
    p = anchors[-1]["payload"]
    assert p["root"] == ep.merkle_root and p["count"] == ep.entry_count
    assert p["witness_weight"] >= p["witness_total"] * 2 // 3 + 1


def test_p2p_offers_land_in_local_roster():
    """接线：P2P 发现结果 → 本地市场列表。发现是一次网络行为，结果沉淀为本机的一张表。"""
    did_a = "did:a2n:ag_aaa"
    did_b = "did:a2n:ag_bbb"

    class FakeNet:
        def query(self, skill, timeout=2.0):
            return [
                {"did": did_a, "skills": [skill], "pub": "p1"},
                {"did": did_b, "skills": [skill, "extra"], "pub": "p2"},
            ]

    principal = "acct:roster-" + new_id("")[2:]
    roster = wiring.p2p_discover_to_roster(FakeNet(), principal, "ocr-pro")
    assert {x["did"] for x in roster} == {did_a, did_b}
    # 第二次发现：已有的保留（合并不覆盖），新的追加
    did_c = "did:a2n:ag_ccc"

    class Net2:
        def query(self, skill, timeout=2.0):
            return [{"did": did_c, "skills": [skill], "pub": "p3"}]

    roster2 = wiring.p2p_discover_to_roster(Net2(), principal, "ocr-pro")
    assert {x["did"] for x in roster2} == {did_a, did_b, did_c}

    row = conn().execute("SELECT roster FROM rosters WHERE principal_id=?", (principal,)).fetchone()
    stored = json.loads(row["roster"])
    assert len(stored) == 3
    assert all(x["via"] == "p2p" for x in stored)


def test_refund_wiring_distributed_recovery():
    """仲裁改判 → 结算层按份额追回；节点余额不足时 shortfall 如实入账，不伪造平衡。"""
    from a2n_settlement import settlement as svc
    from a2n_ledger import ensure_account

    task_id = "t-wire-" + new_id("")[2:]
    ensure_account("acct:rich", "node", "x")
    Ledger().post("acct:rich", 10, "test", task_id)
    out = svc.refund(task_id, "acct:user-x", 4, from_accounts=["acct:rich", "acct:empty-xyz"])
    assert out["refunded"] == 4 and out["shortfall"] == 0
    assert Ledger().balance("acct:user-x") == 4

    out2 = svc.refund(task_id, "acct:user-y", 99, from_accounts=["acct:empty-xyz"])
    assert out2["refunded"] == 0 and out2["shortfall"] == 99, "追不回就如实记 shortfall"
