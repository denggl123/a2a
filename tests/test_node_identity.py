"""自持网络（a2n-node）测试：没有服务器、没有托管时，两个人也能互相发现调用。

分两段：

  · **单元**：卡片自证、双边互签、请求签名与反重放。纯函数，不碰库、不出网。
  · **集成**：真的起节点进程（每个进程一个库），走完"发现 → 取卡 → 直连调用 →
    双向互证"，以及"对方在干活之前先挡掉该挡的"。

为什么不在这一个进程里起两个节点：conftest 已经 import 了 a2n-store（全量套件
共用一个临时库），而"一个节点 = 一个进程 = 一个库"是硬约束。在同一进程里拼两个
节点，测的就不再是这套东西了 —— 集成段因此走**真进程**，顺带把 use_home 的
顺序纪律放到真实进程模型下验证。

本文件刻意不 import a2n_node.node / a2n_server / a2n_custodian：
自持模式的价值主张就是"这些东西不在场"，测试自己先做到。
"""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from a2n_kernel.hashing import new_id, now_iso
from a2n_p2p import DID_PREFIX, Identity

from a2n_node import card as cardmod
from a2n_node import peer as peermod
from a2n_node import receipt as rcpt
from a2n_node.home import same_path, use_home

ROOT = Path(__file__).resolve().parents[1]
NODE_SCRIPT = ROOT / "scripts" / "sovereign_node.py"
PACKAGE_SRC = [str(p) for p in sorted((ROOT / "packages").glob("*/src"))]


def _opener():
    """本机回环必须绕开系统代理（被代理拦下的症状是 502，极难联想）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ============================ 单元：卡片自证 ============================

def _a_card(**kw):
    ident = Identity.generate()
    card = cardmod.build_card(ident, name=kw.pop("name", "alice"),
                              skills=kw.pop("skills", ["ocr-pro"]),
                              host="127.0.0.1", port=9661, p2p_port=9761, **kw)
    return ident, card


def test_did_is_the_fingerprint_of_the_public_key():
    """身份 = 公钥指纹。没有谁在分配 id，也就没有谁能在身份上卡人。"""
    ident = Identity.generate()
    assert cardmod.did_from_pub(ident.pub_raw) == ident.did
    assert ident.did.startswith(DID_PREFIX)


def test_card_proves_itself_without_any_authority():
    _, card = _a_card()
    ok, why = cardmod.verify_card(card, require_endpoint=True)
    assert ok, why
    assert cardmod.card_did(card) == cardmod.did_from_pub(
        cardmod.card_pub_raw(card))


def test_card_hash_is_the_registry_hash_not_a_second_one():
    """同一张卡在平台模式与自持模式下必须是同一个哈希，否则就是两套事实。"""
    from a2n_sdk.projection import card_hash as registry_hash

    _, card = _a_card()
    assert cardmod.card_hash(card) == registry_hash(card)


@pytest.mark.parametrize("field,new_value", [
    ("name", "被改过的名字"),
    ("url", "http://127.0.0.1:9999"),          # 形状合法，但仍然得改不动
    ("description", "被改过的描述"),
])
def test_card_rejects_a_changed_field(field, new_value):
    """签名域是整张卡：少签一个字段，那个字段就能被改而验签照样过。"""
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad[field] = new_value
    ok, why = cardmod.verify_card(bad)
    assert not ok and "签名" in why


def test_card_rejects_a_changed_capability():
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad["skills"][0]["id"] = "免费白嫖别人的名字"
    assert cardmod.verify_card(bad)[0] is False


def test_card_rejects_a_self_declared_identity():
    """用自己的钥匙签一张写着别人 did 的卡 —— 自证的第一道坎就在这。"""
    victim = Identity.generate()
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad["x-a2n"]["sovereign"]["did"] = victim.did
    ok, why = cardmod.verify_card(bad)
    assert not ok and "公钥指纹" in why


def test_card_rejects_another_keys_signature():
    """把签名换成别人签的：签名域是别人的卡，签不到我这张上。"""
    _, card = _a_card()
    other = Identity.generate()
    bad = copy.deepcopy(card)
    bad["x-a2n"]["sovereign"]["pub"] = cardmod.pub_b64(other.pub_raw)
    bad["x-a2n"]["sovereign"]["sig"] = other.sign(cardmod.card_body(card))
    ok, why = cardmod.verify_card(bad)
    assert not ok and "公钥指纹" in why


def test_card_without_a_direct_endpoint_is_not_callable():
    """没有中继可退：url 为空 = 这张卡在这个模式下不可用。"""
    ident = Identity.generate()
    card = cardmod.build_card(ident, name="a", skills=["echo"], port=0)
    assert cardmod.verify_card(card)[0] is True
    ok, why = cardmod.verify_card(card, require_endpoint=True)
    assert not ok and "直连地址" in why


def test_card_rejects_a_missing_self_proof_block():
    _, card = _a_card()
    bad = copy.deepcopy(card)
    del bad["x-a2n"]["sovereign"]
    ok, why = cardmod.verify_card(bad)
    assert not ok and "自证" in why


@pytest.mark.parametrize("field", ["did", "pub", "sig"])
def test_card_needs_all_three_parts_of_its_self_proof(field):
    """自证缺一块就不成立：只有 did 是自称，只有 pub 是没人认领的钥匙。"""
    _, card = _a_card()
    bad = copy.deepcopy(card)
    del bad["x-a2n"]["sovereign"][field]
    ok, why = cardmod.verify_card(bad)
    assert not ok and "自证" in why


# ============================ 单元：双边互签 ============================

def _pair_receipt(**over):
    prov, caller = Identity.generate(), Identity.generate()
    body = rcpt.make_body(task_id=new_id("t"), caller_did=caller.did,
                          provider_did=prov.did, skill="echo",
                          input_hash=rcpt.hash_payload({"text": "hi"}),
                          output_hash=rcpt.hash_payload({"text": "HI"}),
                          ts=now_iso())
    body.update(over)
    return prov, caller, rcpt.sign(prov, body)


def test_provider_receipt_verifies_on_the_callers_side():
    _, _, r = _pair_receipt()
    ok, why = rcpt.verify(r)
    assert ok, why
    assert rcpt.is_from(r, r["provider_did"])


@pytest.mark.parametrize("field", ["output_hash", "task_id", "input_hash", "skill"])
def test_receipt_rejects_a_changed_field(field):
    _, _, r = _pair_receipt()
    bad = dict(r)
    bad[field] = "改过了"
    ok, why = rcpt.verify(bad)
    assert not ok and "签名" in why


def test_receipt_rejects_a_swapped_signer():
    _, _, r = _pair_receipt()
    bad = dict(r)
    bad["by"] = Identity.generate().did          # 冒充另一个签名者
    ok, why = rcpt.verify(bad)
    assert not ok and "公钥指纹" in why


def test_receipt_rejects_a_third_party_signature():
    """签名者必须是这一单的两方之一 —— 否则任何路人都能替他们刻章。"""
    prov, caller, r = _pair_receipt()
    body = rcpt.body_of(r)
    onlooker = Identity.generate()
    ok, why = rcpt.verify(rcpt.sign(onlooker, body))
    assert not ok and "第三方" in why


def test_ack_points_at_exactly_one_receipt():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    ok, why = rcpt.verify_ack(a, r)
    assert ok, why
    assert a["of"] == r["sig"]                    # 引用，不是复述


def test_ack_does_not_transfer_to_another_receipt():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    _, _, other = _pair_receipt()
    ok, why = rcpt.verify_ack(a, other)
    assert not ok and "引用" in why


def test_ack_rejects_a_forged_signer():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    bad = dict(a)
    bad["by"] = Identity.generate().did
    assert rcpt.verify_ack(bad, r)[0] is False


def test_ack_rejects_a_valid_signature_from_unrelated_third_party():
    _, _, r = _pair_receipt()
    outsider = Identity.generate()
    forged = rcpt.ack(outsider, r)
    ok, why = rcpt.verify_ack(forged, r)
    assert not ok and "原调用方" in why


def test_receipt_fingerprint_is_stable_and_content_sensitive():
    _, _, r = _pair_receipt()
    assert rcpt.fingerprint(r) == rcpt.fingerprint(copy.deepcopy(r))
    bad = dict(r)
    bad["output_hash"] = "0" * 64
    assert rcpt.fingerprint(bad) != rcpt.fingerprint(r)


# ============================ 单元：直连请求 ============================

def _req(provider_did: str = "", payload=None):
    """造一份已签名的调用请求。返回 (应答方的身份, 请求) —— 应答方身份用来签应答。"""
    provider = Identity.generate()
    caller = Identity.generate()
    req = peermod.sign_request(caller, provider_did=provider_did or provider.did,
                               skill="echo",
                               payload=payload if payload is not None else {"n": 1})
    return provider, req


def test_request_signature_and_payload_are_both_checked():
    _, req = _req()
    ok, why = peermod.verify_request(req)
    assert ok, why
    # 签名只覆盖载荷指纹 —— 改动载荷后签名仍然"有效"，必须靠指纹比对拦下
    bad = dict(req)
    bad["payload"] = {"n": 999}
    ok, why = peermod.verify_request(bad)
    assert not ok and "载荷" in why


def test_request_rejects_a_self_declared_caller():
    victim = Identity.generate()
    attacker = Identity.generate()
    req = peermod.sign_request(attacker, provider_did=Identity.generate().did,
                               skill="echo", payload={})
    bad = dict(req)
    bad["caller_did"] = victim.did
    assert peermod.verify_request(bad)[0] is False


def test_request_rejects_replay_and_stale_clocks():
    guard = peermod.ReplayGuard()
    _, req = _req()
    assert peermod.verify_request(req, guard=guard)[0] is True
    ok, why = peermod.verify_request(req, guard=guard)
    assert not ok and "nonce" in why           # 同一份请求原样重发
    _, fresh = _req()
    ok, why = peermod.verify_request(fresh, guard=guard, now=time.time() + 9999)
    assert not ok and "越窗" in why


def test_request_is_bound_to_provider_and_tampering_cannot_burn_nonce():
    provider, req = _req()
    other_provider = Identity.generate()
    ok, why = peermod.verify_request(
        req, expected_provider=other_provider.did)
    assert not ok and "不是本节点" in why

    guard = peermod.ReplayGuard()
    tampered = dict(req, payload={"n": 999})
    assert peermod.verify_request(
        tampered, guard=guard, expected_provider=provider.did)[0] is False
    # Invalid content must not consume the legitimate signed request's nonce.
    assert peermod.verify_request(
        req, guard=guard, expected_provider=provider.did)[0] is True


def test_response_is_bound_to_the_request_it_answers():
    prov, req = _req()
    resp = peermod.sign_response(prov, req=req, state="accepted", result={"n": 2})
    assert peermod.verify_response(resp, req=req)[0] is True

    # 第一道闸：签名覆盖整个应答体，改动任何字段先在这里被拦下
    bad = dict(resp, msg_id="别的请求")
    ok, why = peermod.verify_response(bad, req=req)
    assert not ok and "签名" in why

    # 第二道闸：签名有效也不行 —— 答的必须是我这一份，否则是拿别人的应答顶包
    _, other_req = _req(provider_did=prov.did)
    resp2 = peermod.sign_response(prov, req=other_req, state="accepted", result={"n": 2})
    ok, why = peermod.verify_response(resp2, req=req)
    assert not ok and "请求" in why


def test_response_rejects_a_tampered_result():
    prov, req = _req()
    resp = peermod.sign_response(prov, req=req, state="accepted", result={"n": 2})
    bad = dict(resp)
    bad["result"] = {"n": 999999}
    assert peermod.verify_response(bad, req=req)[0] is False


# ============================ 单元：库的归属 ============================

