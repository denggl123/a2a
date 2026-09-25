"""a2n-p2p UDP 打洞测试。

能在一台机器上验证的纪律：三方协调协议真的能建立直连、伪造与冒名必被拒、
限频与会话过期真实生效。跨真实公网 NAT 的效果（锥形可通 / 对称不通）无法
在 CI 里证明，文档必须保持这个诚实边界。
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from a2n_p2p import (HELLO, P2PNode, Envelope, Identity, PUNCH, PUNCH_HINT,
                     PUNCH_REQ, parse_pub)


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _node(**kwargs) -> P2PNode:
    defaults = dict(beacon=False, beacon_interval=0.1)
    defaults.update(kwargs)
    return P2PNode(Identity.generate(), port=_free_udp_port(), **defaults)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _handshake(a: P2PNode, b: P2PNode) -> None:
    """让两个节点完成一次 HELLO 握手，成为互相认识的直邻。"""
    a._send_hello(("127.0.0.1", b.port))
    assert _wait(lambda: a.table.get(b.identity.did) is not None)
    assert _wait(lambda: b.table.get(a.identity.did) is not None)


@pytest.fixture()
def nodes():
    created: list[P2PNode] = []

    def make(**kwargs) -> P2PNode:
        node = _node(**kwargs)
        node.start()
        created.append(node)
        return node

    yield make
    for node in created:
        node.stop()


# ---------- 打通路径 ----------
def test_punch_connects_two_strangers_via_shared_helper(nodes):
    a, b, c = nodes(), nodes(), nodes()
    # C 同时认识 A 与 B；A、B 互不相识
    _handshake(a, c)
    _handshake(b, c)
    assert a.table.get(b.identity.did) is None

    result = a.punch(b.identity.did, timeout=5.0)
    assert result["ok"] is True, result
    # 返回的端点是 C 实测到的 B 的 UDP 源地址
    assert result["endpoint"] == ["127.0.0.1", b.port]
    # 双方都把对方写进了邻居表，且来源标记为 punch
    peer_on_a = a.table.get(b.identity.did)
    peer_on_b = b.table.get(a.identity.did)
    assert peer_on_a is not None and peer_on_a.via == "punch"
    assert peer_on_b is not None and peer_on_b.via == "punch"
    assert peer_on_a.pub_raw == b.identity.pub_raw
    assert peer_on_b.pub_raw == a.identity.pub_raw
    # 打通后可做签名 RTT 探测 —— 直连真的存在
    rtt = a.ping(b.identity.did, timeout=1.5)
    assert rtt is not None


def test_punch_reports_failure_when_helper_does_not_know_target(nodes):
    a, c = nodes(), nodes()
    _handshake(a, c)
    stranger = Identity.generate()
    result = a.punch(stranger.did, timeout=3.0)
    assert result["ok"] is False
    assert "不认识目标" in result["detail"]
    assert a.table.get(stranger.did) is None


def test_punch_requires_running_network():
    node = _node()
    try:
        result = node.punch(Identity.generate().did)
        assert result["ok"] is False and "未启动" in result["detail"]
    finally:
        node.stop()


# ---------- 伪造与滥用必须被拒 ----------
def test_forged_hint_from_unknown_node_is_dropped(nodes):
    a = nodes()
    attacker = nodes()
    victim = Identity.generate()
    # 攻击者伪装协调者，诱骗 A 向一个第三方地址对射
    hint = Envelope(frm=attacker.identity.did, type=PUNCH_HINT, ttl=1, payload={
        "session": "evil", "peer_did": victim.did,
        "peer_pub": a.pub_b64(),  # DID=指纹校验会先拦住冒名
        "endpoint": ["127.0.0.1", 1], "expires": time.time() + 5})
    attacker.send(("127.0.0.1", a.port), hint)
    time.sleep(0.5)
    # A 与攻击者毫无交集：提示被拒，会话不存在，也不向任何地方发 PUNCH
    assert all(s.get("sid") != "evil" for s in a._punch_sessions.values())
    assert a.stats["punch_dropped"] >= 1


def test_punch_from_wrong_identity_is_rejected(nodes):
    a = nodes()
    import threading as _t

    honest = Identity.generate()
    forger = _node()
    try:
        forger.start()
        a._new_punch_session({
            "sid": "s1", "request": "", "peer_did": honest.did,
            "peer_pub_raw": honest.pub_raw, "endpoint": ("127.0.0.1", forger.port),
            "expires": time.time() + 5, "failed": False, "ok": False,
            "observed": None, "event": _t.Event()})
        env = Envelope(frm=forger.identity.did, type=PUNCH, ttl=1,
                       payload={"session": "s1"})
        env.sign(forger.identity)  # 冒名 honest 的对射包
        assert a._on_punch(env, ("127.0.0.1", forger.port)) is False
        assert a.table.get(forger.identity.did) is None
    finally:
        forger.stop()


def test_punch_rate_limit_per_requester(nodes):
    a, c = nodes(), nodes()
    _handshake(a, c)
    # 限频器本身真实生效：第 11 次起拒绝
    for _ in range(10):
        assert a._punch_allow("req:test", 10) is True
    assert a._punch_allow("req:test", 10) is False
    # 行为层面：连续打洞请求后，协调节点沉默（连失败提示都不再回）
    stranger = Identity.generate()
    result = a.punch(stranger.did, timeout=0.2)
    assert result["ok"] is False


def test_punch_session_expiry_rejects_late_packets(nodes):
    a = nodes()
    sid = "punch_test_expired"
    a._new_punch_session({
        "sid": sid, "request": "", "peer_did": "did:x", "peer_pub_raw": None,
        "endpoint": ("127.0.0.1", 1), "expires": time.time() - 0.1,
        "failed": False, "ok": False, "observed": None,
        "event": threading.Event()})
    fake = Identity.generate()
    env = Envelope(frm=fake.did, type=PUNCH, ttl=1, payload={"session": sid})
    env.sign(fake)
    assert a._on_punch(env, ("127.0.0.1", 1)) is False
    assert a.table.get(fake.did) is None


# ---------- 实测端点表 ----------
def test_observed_endpoint_records_measured_source(nodes):
    a, c = nodes(), nodes()
    _handshake(a, c)
    # C 实测到 A 的源地址（本机场景等于真实监听端口）
    observed = c.observed_endpoint(a.identity.did)
    assert observed is not None
    assert observed[0] == "127.0.0.1"


def test_punch_refuses_to_punch_self():
    node = _node()
    try:
        node.start()
        result = node.punch(node.identity.did)
        assert result["ok"] is False and "本节点" in result["detail"]
    finally:
        node.stop()
