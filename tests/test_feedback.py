"""双方反馈账本（R2，`a2n_sdk.feedback`）—— 逐条对 `docs/FEEDBACK-RULES.md` §9 验收。

三条是这个功能的命门，缺一条就会变成谎言：
1. **没有调用关系不能评** —— 对查无此单的任务开评 = 凭空造一份评价。
2. **未交付不能评成品质量** —— 网络失败不许被读成"质量差"。
3. **R2 不是 R3** —— 不产生任何分数、不改排序、不动账本；未评价不阻塞任何事。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from a2n_sdk.feedback import CORE_KEYS, KIND, FeedbackBook
from a2n_sdk.storage import LocalStore

SCOPE = "sc_1"
TASK = "t1"


def _canon(core: dict) -> bytes:
    return json.dumps(core, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _signer(core: dict) -> dict:
    return {"pub": "PUB_TEST", "sig": hashlib.sha256(_canon(core)).hexdigest()}


def _verifier(proof, core: dict) -> bool:
    if not isinstance(proof, dict):
        return False
    return proof.get("sig") == hashlib.sha256(_canon(core)).hexdigest()


def _store_with_task(scope=SCOPE, task_id=TASK, state="COMPLETED"):
    store = LocalStore()
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}")
    store.finish(scope, task_id, {"state": state, "metadata": {}})
    return store


def _add_task(store, task_id, state="COMPLETED", scope=SCOPE):
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}")
    store.finish(scope, task_id, {"state": state, "metadata": {}})


def _book(store):
    return FeedbackBook(store, signer=_signer, verifier=_verifier)


def _buyer_open(book, **kw):
    base = dict(scope=SCOPE, task_id=TASK, direction="buyer_to_seller",
                dimensions={"quality": 5}, author_did="did:a2n:ag_buyer",
                counterparty_did="did:a2n:ag_seller", provider_did="did:a2n:ag_seller",
                service_id="svc_a")
    base.update(kw)
    return book.open(**base)


def _signed(book, **fields):
    """造一份"对方发来的"签名反馈（用同一套确定性签名替身）。"""
    core = {k: fields.get(k) for k in CORE_KEYS}
    return {**core, "proof": _signer(core)}


# ---------------- 1. 只能对真有的任务评 ----------------

def test_open_requires_a_real_local_call():
    book = _book(_store_with_task())
    rec = _buyer_open(book)
    assert rec["v"] == "a2n-feedback/1" and rec["source"] == "self"
    assert rec["revision"] == 1 and book.verify_record(rec) is True
    with pytest.raises(ValueError):
        _buyer_open(book, task_id="never-called")


def test_open_validates_direction_dimensions_and_emptiness():
    book = _book(_store_with_task())
    with pytest.raises(ValueError):
        _buyer_open(book, direction="sideways")
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={"mood": 5})          # 方向不支持这个维度
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={"quality": 9})       # 超出 1–5
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={"quality": True})    # 布尔不是分数
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={}, note="")          # 空反馈没有意义
    with pytest.raises(ValueError):
        _buyer_open(book, note="x" * 501)


# ---------------- 2. 未交付不能评成品质量 ----------------

def test_quality_is_blocked_until_delivered():
    book = _book(_store_with_task(state="FAILED"))
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={"quality": 4})
    # 失败单仍可评可达性与沟通体验（这些不是成品质量）
    rec = _buyer_open(book, dimensions={"communication": 4}, note="对端没有回应")
    assert rec["dimensions"] == {"communication": 4}
    assert book.verify_record(rec) is True


def test_delivered_states_allow_quality():
    for state in ("COMPLETED", "SETTLED"):
        book = _book(_store_with_task(state=state))
        assert _buyer_open(book)["dimensions"] == {"quality": 5}


# ---------------- 3. 每方向一份有效反馈 + 幂等 ----------------

def test_one_effective_feedback_per_direction_with_idempotent_replay():
    book = _book(_store_with_task())
    first = _buyer_open(book)
    replay = _buyer_open(book)                      # 同内容重复提交
    assert replay.get("idempotent") is True
    assert replay["feedback_id"] == first["feedback_id"]
    assert replay["revision"] == 1                  # 幂等不改版本
    assert len(book.versions(first["feedback_id"])) == 1
    # 同方向不同内容 → 必须走"修改"，不许堆第二份
    with pytest.raises(ValueError):
        _buyer_open(book, dimensions={"quality": 2})
    # 另一个方向是另一份（卖方评买方），互不覆盖
    seller = book.open(scope=SCOPE, task_id=TASK, direction="seller_to_buyer",
                       dimensions={"on_spec": 4}, author_did="did:a2n:ag_seller",
                       counterparty_did="did:a2n:ag_buyer",
                       provider_did="did:a2n:ag_seller", service_id="svc_a")
    assert seller["direction"] == "seller_to_buyer"
    assert seller["feedback_id"] != first["feedback_id"]
    assert book.counts()["total"] == 2
    assert book.counts()["buyer_to_seller"] == 1
    assert book.counts()["seller_to_buyer"] == 1


# ---------------- 4. 修改保留版本（链） ----------------

def test_revise_keeps_version_chain_and_old_version_readonly():
    book = _book(_store_with_task())
    v1 = _buyer_open(book)
    v2 = book.revise(feedback_id=v1["feedback_id"], dimensions={"quality": 3},
                     note="后来自查发现交付有缺项")
    assert v2["revision"] == 2
    assert v2["prev_hash"] == v1["digest"]
    assert v2["created_at"] == v1["created_at"]      # 首次时间不变
    chain = book.versions(v1["feedback_id"])
    assert [r["revision"] for r in chain] == [1, 2]
    assert chain[0]["digest"] == v1["digest"]        # 旧版原样
    assert chain[0]["dimensions"] == {"quality": 5}
    assert book.verify_record(v2) is True
    # 改成同一内容 → 不产生新版本
    same = book.revise(feedback_id=v1["feedback_id"], dimensions={"quality": 3},
                       note="后来自查发现交付有缺项")
    assert same.get("unchanged") is True
    assert len(book.versions(v1["feedback_id"])) == 2


def test_revise_rejects_unknown_and_respects_quality_gate():
    book = _book(_store_with_task())
    with pytest.raises(ValueError):
        book.revise(feedback_id="fb_nope", dimensions={"quality": 1})
    failed = _book(_store_with_task(state="CANCELED"))
    rec = _buyer_open(failed, dimensions={"punctual": 2})
    with pytest.raises(ValueError):
        failed.revise(feedback_id=rec["feedback_id"], dimensions={"quality": 2})


# ---------------- 5. 脱敏（自由文本里也不漏密钥/隐私） ----------------

def test_note_is_scrubbed_and_redactions_recorded():
    book = _book(_store_with_task())
    rec = _buyer_open(book, dimensions={"communication": 3},
                      note="有问题发 a@b.com 或 13800138000，密钥 sk-abcdefghijkl")
    assert "a@b.com" not in rec["note"]
    assert "13800138000" not in rec["note"]
    assert "sk-abcdefghijkl" not in rec["note"]
    assert set(rec["redactions"]) & {"邮箱", "手机号", "API 密钥"}
    assert book.verify_record(rec) is True


# ---------------- 6. 收到对方反馈：验签才采信，伪造只留痕 ----------------

def test_ingest_verifies_and_keeps_forgery_as_evidence_only():
    book = _book(_store_with_task())
    good = _signed(book, v="a2n-feedback/1", feedback_id="fb_theirs", task_id=TASK,
                   direction="seller_to_buyer", author_did="did:a2n:ag_seller",
                   counterparty_did="did:a2n:ag_buyer", provider_did="did:a2n:ag_seller",
                   service_id="svc_a", task_state="COMPLETED",
                   dimensions={"on_spec": 5}, note="", redactions=[],
                   source="self", created_at="2026-09-30T00:00:00+00:00",
                   at=1759190400.0, revision=1, prev_hash="")
    kept = book.ingest(good)
    assert kept["verified"] is True and kept["source"] == "counterparty"
    assert book.counts()["received"] == 1 and book.counts()["unverified"] == 0

    # 改了内容却沿用旧签名的"反馈"：验签不过 → 不采信，但留痕（不静默丢弃）
    forged = _signed(book, v="a2n-feedback/1", feedback_id="fb_forged", task_id=TASK,
                     direction="seller_to_buyer", author_did="did:a2n:ag_seller",
                     counterparty_did="did:a2n:ag_buyer",
                     provider_did="did:a2n:ag_seller", service_id="svc_a",
                     task_state="COMPLETED", dimensions={"on_spec": 5}, note="",
                     redactions=[], source="self",
                     created_at="2026-09-30T00:00:00+00:00",
                     at=1759190400.0, revision=1, prev_hash="")
    forged["dimensions"] = {"on_spec": 1}            # 签名覆盖的是 on_spec=5
    rec = book.ingest(forged)
    assert rec["verified"] is False
    assert book.counts()["unverified"] == 1
    assert any(r.get("verified") is False for r in book.received())
    assert book.summary()["unverified"] == 1


def test_ingest_marks_self_source():
    book = _book(_store_with_task())
    own = _signed(book, v="a2n-feedback/1", feedback_id="fb_self", task_id=TASK,
                  direction="buyer_to_seller", author_did="did:a2n:ag_x",
                  counterparty_did="did:a2n:ag_x", provider_did="did:a2n:ag_x",
                  service_id="svc_a", task_state="COMPLETED",
                  dimensions={"quality": 5}, note="", redactions=[], source="self",
                  created_at="2026-09-30T00:00:00+00:00", at=1759190400.0,
                  revision=1, prev_hash="")
    assert book.ingest(own)["self_source"] is True
    assert book.summary()["self_source"] == 1


# ---------------- 7. summary 只给事实，不给分 ----------------

def test_summary_is_factual_counts_not_a_score():
    store = _store_with_task()
    _add_task(store, "t2")
    book = _book(store)
    _buyer_open(book, dimensions={"quality": 4, "punctual": 2})
    book.open(scope=SCOPE, task_id="t2", direction="buyer_to_seller",
              dimensions={"quality": 5}, author_did="did:a2n:ag_buyer",
              counterparty_did="did:a2n:ag_seller", provider_did="did:a2n:ag_seller",
              service_id="svc_b")
    s = book.summary(counterparty="did:a2n:ag_seller")
    assert s["kind"] == KIND
    assert s["total"] == 2
    assert s["dimensions"]["quality"] == {"count": 2, "avg": 4.5}
    assert s["dimensions"]["punctual"] == {"count": 1, "avg": 2.0}
    assert "score" not in s and "reputation" not in s and "tier" not in s
    assert s["notice"] and "不是信誉分" in s["notice"]


# ---------------- 8. 未评价不阻塞、反馈不动账本 ----------------

def test_feedback_does_not_touch_ledger_and_unrated_is_not_blocking():
    store = _store_with_task()
    book = _book(store)
    calls_before = len(store.recent())
    settlements_before = len(store.recent_settlements())
    assert book.has_for(TASK, "buyer_to_seller", "did:a2n:ag_buyer") is None
    _buyer_open(book)
    assert len(store.recent()) == calls_before
    assert len(store.recent_settlements()) == settlements_before
    assert store.task(SCOPE, TASK)["state"] == "COMPLETED"
    # 未评价不是错误：另一个方向查无反馈，也不妨碍任何事
    assert book.has_for(TASK, "seller_to_buyer", "did:a2n:ag_seller") is None
    assert book.by_task(TASK)["directions"]["seller_to_buyer"] == {"mine": None,
                                                                  "theirs": None}


def test_by_task_exposes_both_directions():
    book = _book(_store_with_task())
    mine = _buyer_open(book)
    view = book.by_task(TASK)
    d = view["directions"]["buyer_to_seller"]
    assert d["mine"]["feedback_id"] == mine["feedback_id"]
    assert d["theirs"] is None
    assert book.list(task_id=TASK)[0]["feedback_id"] == mine["feedback_id"]
