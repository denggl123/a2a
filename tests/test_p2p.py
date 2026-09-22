"""a2n-p2p 网络层测试。

重点验证四件事：身份不可伪造、消息不可篡改、不会广播风暴、按需发现真的能发现。
"""
from __future__ import annotations

import socket
import time

import pytest
import a2n_p2p.peers as peer_module

from a2n_p2p import (CARD, HELLO, MSG_TYPES, OFFER, QUERY, Envelope, Identity,
                    P2PNode, PeerTable, parse_pub, hello_payload)


# ---------- 身份 ----------
def test_identity_is_stable_from_private_key(tmp_path):
    a = Identity.generate()
    b = Identity.load(a.save(tmp_path / "k.json"))     # 同一私钥 → 同一身份
    assert a.did == b.did
    assert a.did.startswith("did:a2n:ag_")
    assert len(a.pub_raw) == 32


def test_did_differs_per_identity():
    ids = {Identity.generate().did for _ in range(20)}
    assert len(ids) == 20, "DID 不得碰撞"


def test_signature_rejects_tampering():
    a = Identity.generate()
    payload = {"skill": "ocr", "amount": 100}
    sig = a.sign(payload)
    assert a.verify_with_pub(a.pub_raw, payload, sig)
    tampered = {"skill": "ocr", "amount": 999}
    assert not a.verify_with_pub(a.pub_raw, tampered, sig), "改了内容必须验签失败"


def test_identity_roundtrip_keystore(tmp_path):
    a = Identity.generate()
    p = a.save(tmp_path / "key.json")
    b = Identity.load(p)
    assert b.did == a.did
    payload = {"x": 1}
    assert a.verify_with_pub(b.pub_raw, payload, b.sign(payload))


def test_private_key_never_in_repr():
    a = Identity.generate()
    assert "sk" not in repr(a) and "private" not in repr(a).lower()


# ---------- 信封 ----------
def test_envelope_sign_and_verify():
    a, b = Identity.generate(), Identity.generate()
    env = Envelope(frm=a.did, type=CARD, payload={"card_hash": "abc"})
    env.sign(a)
    assert env.verify(lambda did: a.pub_raw if did == a.did else None)
    # 用错公钥验签必须失败
    assert not env.verify(lambda did: b.pub_raw)


def test_envelope_unknown_pubkey_is_not_trusted():
    """查不到公钥 = 无法验证 = 不采信。这是网络层的安全默认。"""
    a = Identity.generate()
    env = Envelope(frm=a.did, type=CARD, payload={})
    env.sign(a)
    assert not env.verify(lambda did: None)
    assert not env.verify(lambda did: b"")


def test_envelope_rejects_malformed():
    assert Envelope.from_bytes(b"not json") is None
    assert Envelope.from_bytes(b'{"broken":') is None
    assert Envelope.from_bytes(b'{"type":"CARD"}') is None   # 缺 frm
    malformed = Envelope(frm="x", type=CARD, payload={}).to_dict()
    malformed["payload"] = ["not", "an", "object"]
    assert Envelope.from_bytes(__import__("json").dumps(malformed).encode()) is None


def test_envelope_ttl_decays():
    env = Envelope(frm="x", type=CARD, payload={}, ttl=2)
    assert env.forwardable()
    env.decay()
    assert env.ttl == 1
    assert env.forwardable()
    env.decay()
    assert not env.forwardable(), "TTL 归零后不得再转发"


def test_hello_carries_pubkey_for_later_verification():
    a = Identity.generate()
    env = Envelope(frm=a.did, type=HELLO, payload=hello_payload(a, 9701, ["ocr"]))
    learned = parse_pub(env.payload["pub"])
    assert learned == a.pub_raw
    later = Envelope(frm=a.did, type=CARD, payload={"x": 1})
    later.sign(a)
    assert later.verify(lambda did: learned), "通过 HELLO 学到的公钥要能验后续消息"


def test_hello_cannot_bind_someone_elses_did_to_attacker_key():
    victim, attacker = Identity.generate(), Identity.generate()
    receiver = P2PNode(Identity.generate(), port=9899, beacon=False)
    forged = Envelope(frm=victim.did, type=HELLO,
                      payload=hello_payload(attacker, 9701, ["evil"])).sign(attacker)
    receiver._handle(forged.to_bytes(), ("127.0.0.1", 45678))
    assert receiver.table.pub_lookup(victim.did) is None
    assert receiver.table.get(victim.did) is None
    assert receiver.stats["dropped_bad_identity"] == 1


def test_query_response_uses_observed_reverse_path_not_claimed_address():
    provider, caller = P2PNode(Identity.generate(), port=9898, beacon=False), Identity.generate()
    provider.skills = ["ocr"]
    provider.table.upsert(caller.did, "192.0.2.44", 45678, caller.pub_raw)
    sent = []
    provider.send = lambda address, envelope: sent.append((address, envelope.type)) or True
    query = Envelope(frm=caller.did, type=QUERY, ttl=1, payload={
        "skill": "ocr", "reply_addr": ["198.51.100.99", 9999],
        "pub": __import__("base64").urlsafe_b64encode(caller.pub_raw).decode().rstrip("="),
    }).sign(caller)
    provider._handle(query.to_bytes(), ("192.0.2.44", 45678))
    assert sent == [(('192.0.2.44', 45678), OFFER)]


def test_stale_signed_discovery_packet_cannot_rewrite_peer_address():
    sender = Identity.generate()
    receiver = P2PNode(Identity.generate(), port=9897, beacon=False)
    stale = Envelope(frm=sender.did, type=HELLO,
                     payload=hello_payload(sender, 9701),
                     ts=time.time() - 3600).sign(sender)
    receiver._handle(stale.to_bytes(), ("192.0.2.55", 45678))
    assert receiver.table.get(sender.did) is None
    assert receiver.stats["dropped_stale"] == 1


def test_hello_rejects_invalid_udp_port_and_send_never_raises_for_it():
    sender = Identity.generate()
    receiver = P2PNode(Identity.generate(), port=9895, beacon=False)
    invalid = hello_payload(sender, 9701)
    invalid["port"] = 65_536
    envelope = Envelope(frm=sender.did, type=HELLO, payload=invalid).sign(sender)
    receiver._handle(envelope.to_bytes(), ("192.0.2.56", 45678))
    assert receiver.table.get(sender.did) is None
    receiver._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        assert receiver.send(("127.0.0.1", 65_536), Envelope(
            frm=receiver.identity.did, type=CARD, payload={})) is False
    finally:
        receiver._sock.close()
        receiver._sock = None


def test_forged_packet_cannot_poison_dedup_slot_for_later_valid_packet():
    victim, attacker = Identity.generate(), Identity.generate()
    receiver = P2PNode(Identity.generate(), port=9896, beacon=False)
    receiver.table.learn_pub(victim.did, victim.pub_raw)
    msg_id = "msg_same"
    forged = Envelope(frm=victim.did, type=HELLO,
                      payload=hello_payload(victim, 9701),
                      msg_id=msg_id).sign(attacker)
    valid = Envelope(frm=victim.did, type=HELLO,
                     payload=hello_payload(victim, 9701),
                     msg_id=msg_id).sign(victim)
    receiver._handle(forged.to_bytes(), ("192.0.2.60", 45678))
    receiver._handle(valid.to_bytes(), ("192.0.2.61", 45679))
    peer = receiver.table.get(victim.did)
    assert peer is not None and peer.host == "192.0.2.61"
    assert receiver.stats["dropped_badsig"] == 1
    assert receiver.stats["dropped_dup"] == 0


# ---------- 对等表 ----------
def test_peer_table_dedup_cache_is_bounded():
    t = PeerTable()
    for i in range(5000):
        t.mark_seen(f"m{i}")
    assert len(t._seen) <= 4096, "去重缓存必须有界，否则是内存泄漏"
    assert t.already_seen("m4999")
    assert not t.already_seen("m0"), "最老的记录应被淘汰"


def test_peer_pubkey_is_not_overwritten():
    """公钥一旦学到就固定，防止后来者冒名顶替。"""
    t = PeerTable()
    a = Identity.generate()
    t.upsert(a.did, "127.0.0.1", 9701, pub_raw=a.pub_raw)
    fake = Identity.generate().pub_raw
    t.upsert(a.did, "127.0.0.1", 9702, pub_raw=fake)
    assert t.pub_lookup(a.did) == a.pub_raw


def test_peer_prune_removes_dead():
    t = PeerTable()
    p = t.upsert("did:a2n:ag_x", "127.0.0.1", 9701)
    p.last_seen = time.time() - 999
    assert t.prune() == 1
    assert len(t) == 0


def test_peer_by_skill():
    t = PeerTable()
    t.upsert("did:a2n:ag_a", "127.0.0.1", 9701, skills=["ocr"])
    t.upsert("did:a2n:ag_b", "127.0.0.1", 9702, skills=["tts"])
    assert [p.did for p in t.by_skill("ocr")] == ["did:a2n:ag_a"]


# ---------- 端到端：真实 UDP ----------
@pytest.fixture
def net2():
    """两个真实节点，走**真实 bootstrap 握手**建立互识（不预置对等式作弊）。

    B 先起，A 带 bootstrap 指向 B：A 发 HELLO → B 学到 A 的公钥并回礼 → 双方互识。
    这正是没有中心目录时新节点入网的全过程。
    """
    b = P2PNode(Identity.generate(), port=9802, beacon=False)
    b.start()
    a = P2PNode(Identity.generate(), port=9801, beacon=False,
                bootstrap=[("127.0.0.1", 9802)])
    a.start()
    time.sleep(0.4)          # 等握手完成
    yield a, b
    a.stop()
    b.stop()


def test_bootstrap_handshake_establishes_mutual_knowledge(net2):
    a, b = net2
    assert b.table.get(a.identity.did) is not None, "B 应通过 bootstrap 学到 A"
    assert b.table.pub_lookup(a.identity.did) == a.identity.pub_raw, "必须学到公钥"
    assert a.table.get(b.identity.did) is not None, "A 应收到 B 的回礼 HELLO"
    assert a.table.pub_lookup(b.identity.did) == b.identity.pub_raw


def test_signed_peer_ping_measures_udp_round_trip_without_business_payload(net2):
    a, b = net2
    rtt = a.ping(b.identity.did, timeout=0.8)
    assert rtt is not None and rtt >= 0
    assert a.ping("did:a2n:ag_unknown", timeout=0.01) is None


def test_bootstrap_keepalive_survives_when_lan_beacon_is_disabled(monkeypatch):
    monkeypatch.setattr(peer_module, "PEER_TTL", 0.16)
    b = P2PNode(Identity.generate(), port=9812, beacon=False,
                beacon_interval=0.03)
    a = P2PNode(Identity.generate(), port=9811, beacon=False,
                beacon_interval=0.03, bootstrap=[("127.0.0.1", 9812)])
    b.start()
    a.start()
    try:
        time.sleep(0.4)
        assert a.table.get(b.identity.did) is not None
        assert b.table.get(a.identity.did) is not None
        assert a.table.alive() and b.table.alive()
    finally:
        a.stop()
        b.stop()


def test_gossip_message_reaches_peer_and_verified(net2):
    a, b = net2
    got = []
    b.on(CARD, lambda env, addr: got.append(env))
    a.announce(["ocr-pro"])
    time.sleep(0.4)
    assert len(got) == 1, "CARD 应送达且只送达一次"
    assert got[0].payload["skills"] == ["ocr-pro"]
    assert got[0].verify(b.table.pub_lookup), "收到的消息必须通过验签"


def test_duplicate_message_is_dropped(net2):
    a, b = net2
    got = []
    b.on(CARD, lambda env, addr: got.append(env))
    env = Envelope(frm=a.identity.did, type=CARD, payload={"skills": ["ocr"]})
    env.sign(a.identity)
    a.send(("127.0.0.1", 9802), env)
    a.send(("127.0.0.1", 9802), env)   # 同一 msg_id 发两次
    time.sleep(0.4)
    assert len(got) == 1, "重复消息必须被去重拦下"
    assert b.stats["dropped_dup"] == 1


def test_forged_message_is_rejected(net2):
    """伪造：谎称自己是 A，但用别人的私钥签。"""
    a, b = net2
    attacker = Identity.generate()
    got = []
    b.on(CARD, lambda env, addr: got.append(env))
    env = Envelope(frm=a.identity.did, type=CARD, payload={"skills": ["evil"]})
    env.sign(attacker)                  # 用攻击者的私钥签，却自称是 A
    raw = env.to_bytes()
    with __import__("socket").socket(__import__("socket").AF_INET,
                                     __import__("socket").SOCK_DGRAM) as s:
        s.sendto(raw, ("127.0.0.1", 9802))
    time.sleep(0.4)
    assert got == [], "签名与 did 不匹配的消息必须被丢弃"
    assert b.stats["dropped_badsig"] == 1


def test_ondemand_discovery_returns_offers(net2):
    a, b = net2
    b.skills = ["ocr-pro"]
    a.table.upsert(b.identity.did, "127.0.0.1", 9802,
                   b.identity.pub_raw, via="bootstrap")
    offers = a.query("ocr-pro", timeout=1.0)
    assert any(o["did"] == b.identity.did for o in offers), "会该技能的邻居应应答"


def test_query_does_not_disturb_non_providers(net2):
    a, b = net2
    b.skills = ["tts"]
    offers = a.query("ocr-pro", timeout=0.6)
    assert offers == [], "不会该技能的不该应答"


def test_oversized_payload_refused_by_gossip(net2):
    """层次边界：大包属于传输层隧道，gossip 必须拒绝。"""
    a, b = net2
    env = Envelope(frm=a.identity.did, type=CARD, payload={"blob": "x" * 80_000})
    assert a.send(("127.0.0.1", 9802), env) is False


# ---------- 多跳：A — B — C ----------
def test_multihop_discovery_reaches_a_node_i_never_met():
    """A 只认识 B，C 只认识 B。A 仍能发现 C —— 这才是真正的按需发现。"""
    c = P2PNode(Identity.generate(), port=9803, beacon=False)
    c.skills = ["ocr-pro"]
    c.start()
    b = P2PNode(Identity.generate(), port=9802, beacon=False,
                bootstrap=[("127.0.0.1", 9803)])
    b.start()
    a = P2PNode(Identity.generate(), port=9801, beacon=False,
                bootstrap=[("127.0.0.1", 9802)])
    a.start()
    try:
        time.sleep(0.5)
        assert a.table.get(c.identity.did) is None, "A 不应直接认识 C"
        offers = a.query("ocr-pro", timeout=1.5)
        assert any(o["did"] == c.identity.did for o in offers), \
            "多跳查询必须能发现我没直接连过的 provider"
        assert a.table.pub_lookup(c.identity.did) == c.identity.pub_raw, "应 TOFU 学到公钥"
        assert not a.table.is_endorsed(c.identity.did), \
            "TOFU 学到的公钥不等于被担保 —— 信任归上层信誉裁定"
    finally:
        for n in (a, b, c):
            n.stop()


def test_tofu_pubkey_cannot_be_overwritten():
    """先学到的公钥锁定，后来者无法冒名顶替。"""
    t = PeerTable()
    a = Identity.generate()
    t.learn_pub(a.did, a.pub_raw)
    t.learn_pub(a.did, Identity.generate().pub_raw)
    assert t.pub_lookup(a.did) == a.pub_raw
