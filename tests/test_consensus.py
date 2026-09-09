"""a2n-consensus 测试：Merkle / epoch 三段生命周期 / 见证门槛 / 资金锚 / SPV。

共识层的测试标准只有一条：**该通过的正常通过，不该通过的绝不放行**。
"""
from __future__ import annotations

import pytest

from a2n_consensus import consensus
from a2n_consensus.anchor import Anchor, EscrowAttestation, three_keys_ok
from a2n_consensus.witness import Witness, WitnessSet
from a2n_kernel.merkle import EMPTY_ROOT, merkle_proof, merkle_root, verify_proof
from a2n_ledger import Ledger
from a2n_p2p import Signer, verifier_fn
from a2n_kernel.hashing import new_id


def _post_n(n: int, tag: str) -> None:
    led = Ledger()
    for i in range(n):
        led.post(f"acct:{tag}", 1, "test", f"{tag}-{i}")
        led.post(f"acct:{tag}-b", -1, "test", f"{tag}-{i}")


def _setup_witnesses(n: int = 3) -> list[Signer]:
    consensus.witnesses = WitnessSet()
    signers = [Signer.generate() for _ in range(n)]
    for s in signers:
        consensus.witnesses.add(Witness(did=s.did, pub=s.pub))
    consensus.set_verifier(verifier_fn())
    return signers


def _setup_anchor(signer: Signer) -> Anchor:
    """锚的签名与验签用同一把钥匙 —— 演示环境持牌方即本进程。"""
    a = Anchor(did="did:a2n:custodian", pub=signer.pub,
               signer=signer.signer_fn(), verifier=verifier_fn())
    consensus.anchor = a
    return a


@pytest.fixture(scope="module", autouse=True)
def _batch_wiring():
    """不依赖装配层：本模块自己把共识层接到账本上。"""
    led = Ledger()
    consensus.set_batch_provider(lambda a, b: led.batch_range(a, b))
    consensus.set_leaf(Ledger.leaf)
    yield


# ---------- Merkle 原语 ----------

def test_merkle_root_deterministic_and_order_sensitive():
    a = merkle_root(["h1", "h2", "h3"])
    assert a == merkle_root(["h1", "h2", "h3"])
    assert a != merkle_root(["h3", "h2", "h1"]), "换序必须换根，否则顺序无法被承诺"


def test_merkle_odd_leaves_self_pair():
    assert merkle_root(["x"]) != EMPTY_ROOT
    # 奇数叶子自我配对：2 个与 3 个（最后一个自我配对）根不同
    assert merkle_root(["a", "b", "b"]) != merkle_root(["a", "b"])


def test_merkle_proof_roundtrip_and_tamper():
    leaves = [f"leaf{i}" for i in range(7)]
    root = merkle_root(leaves)
    for i in (0, 3, 6):
        p = merkle_proof(leaves, i)
        assert verify_proof(leaves[i], i, p, root)
        assert not verify_proof("tampered", i, p, root), "改了内容必须验不过"
    # 别的叶子冒充同一下标：证明对不上
    p = merkle_proof(leaves, 2)
    assert not verify_proof(leaves[5], 2, p, root)
    with pytest.raises(IndexError):
        merkle_proof(leaves, 7)


def test_empty_root_is_public_constant():
    assert merkle_root([]) == EMPTY_ROOT


# ---------- epoch 生命周期 ----------

def test_epoch_open_seal_finalize_full_loop():
    tag = new_id("")[2:]
    signers = _setup_witnesses(3)
    _setup_anchor(signers[0])
    _post_n(4, tag)

    ep = consensus.open()
    ep = consensus.seal(ep.epoch_id)
    assert ep.state == "SEALED" and ep.entry_count >= 8  # 本测试 4 轮=8 条；共享库可能更多

    msg = ep.sign_msg()
    for s in signers:
        consensus.witness_sign(ep.epoch_id, s.did, s.sign(msg))
    att = consensus.anchor.attest(ep.epoch_id, balance_fen=12345)
    consensus.add_anchor(ep.epoch_id, consensus.anchor.sign(att))

    r = consensus.finalize(ep.epoch_id)
    assert r["passed"] and r["state"] == "ANCHORED"
    st = consensus.state_of(ep.epoch_id)
    assert st["witness_sigs"] == 3 and st["anchor"]


def test_finalize_refuses_without_quorum():
    tag = new_id("")[2:]
    signers = _setup_witnesses(4)
    _post_n(2, tag)
    ep = consensus.seal(consensus.open().epoch_id)
    # 4 个见证人只来 2 个（一半 < 2/3）
    for s in signers[:2]:
        consensus.witness_sign(ep.epoch_id, s.did, s.sign(ep.sign_msg()))
    r = consensus.finalize(ep.epoch_id)
    assert not r["passed"]
    assert "不足" in r["why"]


def test_finalize_refuses_without_anchor():
    tag = new_id("")[2:]
    signers = _setup_witnesses(3)
    _post_n(2, tag)
    ep = consensus.seal(consensus.open().epoch_id)
    for s in signers:
        consensus.witness_sign(ep.epoch_id, s.did, s.sign(ep.sign_msg()))
    r = consensus.finalize(ep.epoch_id)
    assert not r["passed"] and "资金锚" in r["anchor"]


def test_finalize_detects_tampered_ledger():
    """账本被文件级篡改后，重算根对不上 → 不通过。"""
    tag = new_id("")[2:]
    signers = _setup_witnesses(3)
    _setup_anchor(signers[0])
    _post_n(2, tag)
    ep = consensus.seal(consensus.open().epoch_id)
    for s in signers:
        consensus.witness_sign(ep.epoch_id, s.did, s.sign(ep.sign_msg()))
    consensus.add_anchor(ep.epoch_id,
                         consensus.anchor.sign(consensus.anchor.attest(ep.epoch_id, 12345)))

    # 绕过触发器篡改（与 test_invariants 同一手法；测完必须恢复，共享库不能被弄脏）
    from a2n_store import conn
    c = conn()
    row = c.execute(
        "SELECT rowid, delta FROM ledger_entries WHERE ref_id LIKE ? LIMIT 1", (tag + "-%",)
    ).fetchone()
    c.execute("DROP TRIGGER IF EXISTS ledger_no_update")
    c.execute("UPDATE ledger_entries SET delta=? WHERE rowid=?", (row["delta"] + 5, row["rowid"]))
    c.commit()
    try:
        r = consensus.finalize(ep.epoch_id)
        assert not r["passed"] and "篡改" in r["why"]
    finally:
        c.execute("UPDATE ledger_entries SET delta=? WHERE rowid=?", (row["delta"], row["rowid"]))
        c.commit()
        c.execute("""CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries
                     BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE forbidden'); END;""")
        c.commit()
        # 恢复后同一 epoch 可以重新封定案吗？不能 —— state 仍是 SEALED，
        # 重新 finalize 应当通过（这正是"篡改会被抓住、修正后可恢复"的语义）
        r2 = consensus.finalize(ep.epoch_id)
        assert r2["passed"]


def test_epoch_without_witnesses_never_passes():
    """没有见证人 = 没有共识，宁可停摆也不降级放行。"""
    consensus.witnesses = WitnessSet()
    tag = new_id("")[2:]
    _post_n(1, tag)
    ep = consensus.seal(consensus.open().epoch_id)
    r = consensus.finalize(ep.epoch_id)
    assert not r["passed"]


def test_seal_twice_is_rejected():
    tag = new_id("")[2:]
    _setup_witnesses(1)
    _post_n(1, tag)
    ep = consensus.open()
    consensus.seal(ep.epoch_id)
    with pytest.raises(ValueError):
        consensus.seal(ep.epoch_id)


def test_epoch_chain_links_and_verifies():
    tag = new_id("")[2:]
    _setup_witnesses(1)
    _post_n(1, tag)
    e1 = consensus.seal(consensus.open().epoch_id)
    _post_n(1, tag)
    e2 = consensus.seal(consensus.open().epoch_id)
    assert e2.from_rowid == e1.to_rowid, "新批次必须从上一个批次的终点开始"
    assert e2.prev_hash == e1.epoch_hash
    ok, msg = consensus.verify_chain()
    assert ok


def test_epoch_batch_provider_required():
    tag = new_id("")[2:]
    _setup_witnesses(1)
    old = consensus._batch
    consensus._batch = None
    _post_n(1, tag)
    ep = consensus.open()
    with pytest.raises(RuntimeError):
        consensus.seal(ep.epoch_id)
    consensus._batch = old


# ---------- SPV ----------

def test_spv_proof_from_real_epoch():
    tag = new_id("")[2:]
    _setup_witnesses(1)
    _post_n(5, tag)
    ep = consensus.seal(consensus.open().epoch_id)
    entries = Ledger().batch_range(ep.from_rowid, ep.to_rowid)
    for idx in (0, 4):
        p = consensus.proof(ep.epoch_id, idx)
        assert p["entry_id"] == entries[idx]["id"]
        assert consensus.verify_inclusion(p["root"], p["leaf"], p["index"], p["proof"])
        assert not consensus.verify_inclusion("0" * 64, p["leaf"], p["index"], p["proof"])


# ---------- 见证集合语义 ----------

def test_witness_weights_and_threshold():
    ws = WitnessSet()
    ws.add(Witness(did="a", pub="pa", weight=1))
    ws.add(Witness(did="b", pub="pb", weight=1))
    ws.add(Witness(did="c", pub="pc", weight=1))
    assert ws.threshold() == 3            # 2*3//3+1
    assert not ws.reached([{"signer": "a"}])
    assert ws.reached([{"signer": "a"}, {"signer": "b"}, {"signer": "c"}])


def test_outsider_signature_does_not_count():
    ws = WitnessSet()
    ws.add(Witness(did="a", pub="pa"))
    assert not ws.reached([{"signer": "stranger"}]), "名单外的人签名不算"


def test_tofu_pub_cannot_be_overwritten():
    ws = WitnessSet()
    ws.add(Witness(did="a", pub="pub-original"))
    ws.add(Witness(did="a", pub="pub-evil"))
    assert ws.witnesses["a"].pub == "pub-original"


def test_verify_rejects_invalid_signature():
    ws = WitnessSet()
    s1, s2 = Signer.generate(), Signer.generate()
    ws.add(Witness(did=s1.did, pub=s1.pub))
    ok, why = ws.verify([{"signer": s1.did, "sig": s2.sign("msg")}], "msg", verifier_fn())
    assert not ok and "无效" in why


# ---------- 资金锚 ----------

def test_anchor_sign_and_verify():
    s = Signer.generate()
    a = Anchor(did="did:a2n:custodian", pub=s.pub, signer=s.signer_fn(), verifier=verifier_fn())
    att = a.attest("ep_x", 999)
    sig = a.sign(att)
    ok, why = a.verify(sig, "ep_x")
    assert ok and "999" in why
    ok2, why2 = a.verify(sig, "ep_other")
    assert not ok2 and "不匹配" in why2


def test_anchor_without_key_cannot_sign():
    a = Anchor(did="x", pub="p")
    with pytest.raises(PermissionError):
        a.sign(a.attest("ep", 1))


def test_three_keys_rule():
    assert three_keys_ok(True, True, True)
    assert not three_keys_ok(True, True, False)
    assert not three_keys_ok(False, True, True)
    assert not three_keys_ok(True, False, False)


def test_attestation_signature_domain_is_stable():
    a1 = EscrowAttestation(epoch_id="e", balance_fen=100, as_of="T", custodian_ref="r")
    a2 = EscrowAttestation(epoch_id="e", balance_fen=100, as_of="T", custodian_ref="r")
    assert a1.msg() == a2.msg() and a1.digest() == a2.digest()
    a3 = EscrowAttestation(epoch_id="e", balance_fen=101, as_of="T", custodian_ref="r")
    assert a1.digest() != a3.digest()
