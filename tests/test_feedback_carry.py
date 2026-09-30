"""R2 捎带交换（F3，`docs/FEEDBACK-API.md` §4）—— 反馈随**收据回执**往返，验签后原样留存。

R2 不做按需拉取/摘要服务（那是 R3）。最小路径只有一条：反馈签名对象随任务终结消息捎带。
反馈通常写在调用之后，终结消息早已发过，所以「补交」复用**同一份回执**当载体
（供给方 `acknowledge` 幂等），由节点侧 `_deliver_feedback` 重发一次。

命门三条：
1. **只捎带本机自己写的**那一份 —— 绝不凭空造，也绝不把收到的对方反馈原样回传。
2. **验签通过才采信**（`verified=true`）；被篡改的只留痕 `verified=false`，不混入统计。
3. **没送到就如实说没送到** —— 没有回执 / 没写反馈 / 对方不在线，都回 `delivered=False`。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

import pytest

from a2n_node.daemon import Daemon
from a2n_node.feedback_identity import (
    absorb_feedback, offer_feedback, signer_for, verifier_for)
from a2n_node.protection import EnvironmentProtector
from a2n_p2p import Identity
from a2n_sdk.feedback import FeedbackBook
from a2n_sdk.storage import LocalStore


def http(base, path, body=None):
    req = urllib.request.Request(base + path, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def card(url="http://127.0.0.1:9999/a2a/echo"):
    return {"name": "Echo", "url": url, "skills": [{"id": "echo", "name": "Echo"}],
            "version": "1.0.0"}


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def _book(identity, scope="sc", task_id="t1", state="COMPLETED"):
    """一个跑在本机 LocalStore 上、用真实 ed25519 身份签名的反馈账本 + 一条已交付任务。"""
    store = LocalStore()
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}")
    store.finish(scope, task_id, {"state": state, "metadata": {}})
    return FeedbackBook(store, signer=signer_for(identity), verifier=verifier_for())


# ---------------- 纯粹口径：只捎带自己写的 ----------------

def test_offer_returns_only_own_current_feedback():
    me = Identity.generate()
    book = _book(me)
    written = book.open(scope="sc", task_id="t1", direction="buyer_to_seller",
                        dimensions={"quality": 5}, author_did=me.did)
    offered = offer_feedback(book, task_id="t1", direction="buyer_to_seller")
    assert offered and offered["feedback_id"] == written["feedback_id"]
    assert offered["source"] == "self"
    # 没写过的方向 / 没写过的任务 / 没有账本 → 一律回 None，不凭空造一份。
    assert offer_feedback(book, task_id="t1", direction="seller_to_buyer") is None
    assert offer_feedback(book, task_id="t9", direction="buyer_to_seller") is None
    assert offer_feedback(None, task_id="t1", direction="buyer_to_seller") is None


def test_offer_never_echoes_received_counterparty_feedback():
    author, peer = Identity.generate(), Identity.generate()
    sender = _book(author)
    signed = sender.open(scope="sc", task_id="t1", direction="buyer_to_seller",
                         dimensions={"quality": 5}, author_did=author.did,
                         counterparty_did=peer.did)
    receiver = _book(peer)
    assert absorb_feedback(receiver, {"feedback": signed})["verified"] is True
    # 收到的那一份不是"我写的"：offer 必须回 None，绝不二次传播别人的评价。
    assert offer_feedback(receiver, task_id="t1", direction="buyer_to_seller") is None


# ---------------- 验签采信：改一个字节就不采信 ----------------

def test_absorb_verifies_and_keeps_original():
    author, peer = Identity.generate(), Identity.generate()
    signed = _book(author).open(scope="sc", task_id="t1", direction="buyer_to_seller",
                                dimensions={"quality": 4}, note="交付可以用",
                                author_did=author.did, counterparty_did=peer.did)
    receiver = _book(peer)
    got = absorb_feedback(receiver, {"feedback": signed})
    assert got["verified"] is True and got["source"] == "counterparty"
    kept = receiver.list(source="counterparty")
    assert kept and kept[0]["feedback_id"] == signed["feedback_id"]
    assert kept[0]["note"] == "交付可以用"  # 原样留存，不代签不转写


def test_absorb_tampered_feedback_is_kept_but_not_adopted():
    author, peer = Identity.generate(), Identity.generate()
    signed = _book(author).open(scope="sc", task_id="t1", direction="buyer_to_seller",
                                dimensions={"quality": 5}, author_did=author.did,
                                counterparty_did=peer.did)
    signed = {**signed, "dimensions": {"quality": 1}}  # 签名之后被改
    receiver = _book(peer)
    got = absorb_feedback(receiver, signed)
    assert got["verified"] is False  # 留痕但不采信
    assert receiver.list(source="counterparty")[0]["verified"] is False


def test_absorb_rejects_junk_without_raising():
    receiver = _book(Identity.generate())
    assert absorb_feedback(receiver, None) is None
    assert absorb_feedback(receiver, "not-a-dict") is None
    assert absorb_feedback(receiver, {}) is None
    assert absorb_feedback(receiver, {"feedback": {}}) is None
    assert absorb_feedback(None, {"feedback": {"feedback_id": "x"}}) is None


# ---------------- 端到端：两个真节点，反馈随回执往返 ----------------

def test_two_nodes_piggyback_feedback_over_receipt_reciprocal(tmp_path, protector):
    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    caller = Daemon(tmp_path / "caller", port=0, protector=protector).start()
    try:
        provider.runtime.mount_callable(card(), lambda payload: {"echo": payload},
                                        service_id="peer_echo")
        projected = provider.runtime.project_binding(
            "peer_echo", public_base=provider.runtime.local_base_url)
        imported = caller.runtime.import_agent(projected)
        task_id = "feedback-carry"
        status, rpc = http(caller.runtime.local_base_url,
                           "/a2a/" + imported.projection_id, {
                               "jsonrpc": "2.0", "id": "test", "method": "message/send",
                               "params": {"message": {"messageId": task_id,
                                                      "parts": [{"kind": "data", "data": {"x": 1}}]},
                                          "metadata": {"skill": "echo"},
                                          "configuration": {"blocking": True}}})
        assert status == 200 and "error" not in rpc, rpc

        # 两边各写一份反馈（调用之后才写 —— 正是 R2 要"补交"的场景）。
        _, mine = caller.management.command("/v1/feedback/open", {
            "scope": imported.projection_id, "task_id": task_id,
            "dimensions": {"quality": 5}, "note": "交付对得上"})
        _, theirs = provider.management.command("/v1/feedback/open", {
            "scope": "peer_echo", "task_id": task_id, "dimensions": {"on_spec": 5}})
        assert mine["direction"] == "buyer_to_seller"
        assert theirs["direction"] == "seller_to_buyer"

        # 调用方补交：把买方反馈随同一份回执再捎一次，同时收下供给方随响应捎回的反馈。
        rc, out = caller.management.command("/v1/feedback/deliver", {
            "scope": imported.projection_id, "task_id": task_id})
        assert rc == 200 and out["delivered"] is True, out
        assert out["received"] is True, out

        got_by_provider = provider.feedback.list(source="counterparty", task_id=task_id)
        got_by_caller = caller.feedback.list(source="counterparty", task_id=task_id)
        assert [r["feedback_id"] for r in got_by_provider] == [mine["feedback_id"]]
        assert got_by_provider[0]["verified"] is True
        assert [r["feedback_id"] for r in got_by_caller] == [theirs["feedback_id"]]
        assert got_by_caller[0]["verified"] is True
        # 双方各持有对方那一份，自己也留着自己那一份 —— 一式两份，互不覆盖。
        assert provider.feedback.list(source="self", task_id=task_id)[0]["feedback_id"] \
            == theirs["feedback_id"]
        assert caller.feedback.list(source="self", task_id=task_id)[0]["feedback_id"] \
            == mine["feedback_id"]

        # 重放同一份回执按幂等处理：不重复入库、不改动有效版本。
        rc, again = caller.management.command("/v1/feedback/deliver", {
            "scope": imported.projection_id, "task_id": task_id})
        assert rc == 200 and again["delivered"] is True
        assert len(provider.feedback.list(source="counterparty", task_id=task_id)) == 1
    finally:
        caller.stop()
        provider.stop()


def test_deliver_without_receipt_or_feedback_reports_honestly(tmp_path, protector):
    node = Daemon(tmp_path / "solo", port=0, protector=protector).start()
    try:
        # 本机根本没有这笔任务 → 没有回执可补交，如实说。
        rc, out = node.management.command("/v1/feedback/deliver",
                                          {"scope": "nope", "task_id": "missing"})
        assert rc == 200 and out["delivered"] is False
        assert "回执" in out["reason"]
    finally:
        node.stop()
