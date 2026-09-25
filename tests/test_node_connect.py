"""节点互联：控制台「连接节点」的两条语义（P2P 种子 / 目录源）。

要验的三件事，缺一件这个功能就不成立：

1. **热加真的当场生效** —— 不是"存了设置等重启"。加进去就要立刻握手、邻居表里
   看得到对方；删掉只停止重试，不把已建立的邻居关系单方面抹掉。
2. **重启后还在** —— 控制台连过的节点不能重启一次就悄悄消失（那是最难查的
   一类不一致：界面上写着连过，实际什么都没发生）。
3. **两种输入不许混为一谈** —— `host:port` 是"我主动去认识它"，
   `http(s)://` 是"搜索时向它查目录"。前者当场生效，后者只影响搜索。
   明文 HTTP 只放行不离开本机的地址（回环 / localhost / host.docker.internal）。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.request

import pytest

from a2n_node.daemon import Daemon
from a2n_node.p2p_service import P2PDiscoveryService
from a2n_node.protection import EnvironmentProtector
from a2n_node.public_directory import PublicDirectoryClient, normalize_base
from a2n_p2p import Identity
from a2n_sdk.management import parse_seed


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def http(base, path, body=None, token=""):
    req = urllib.request.Request(
        base + path, headers={"Content-Type": "application/json",
                              "X-A2N-Local-Token": token},
        data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


# ---------------- 输入解析：两条语义的分界线 ----------------

@pytest.mark.parametrize("text,expected", [
    ("127.0.0.1:9701", ("127.0.0.1", 9701)),
    ("  a2n.example:65535  ", ("a2n.example", 65535)),
    ("[::1]:9701", ("::1", 9701)),
])
def test_seed_address_is_parsed_strictly(text, expected):
    assert parse_seed(text) == expected


@pytest.mark.parametrize("text", [
    "", "127.0.0.1", "host:", "host:abc", "host:0", "host:65536", ":9701", "[::1",
])
def test_seed_address_refuses_to_guess(text):
    """没带端口一律拒 —— 猜一个默认端口只会变成"连了没反应"。"""
    with pytest.raises(ValueError):
        parse_seed(text)


@pytest.mark.parametrize("value", [
    "http://127.0.0.1:8771", "http://localhost:8771",
    "http://host.docker.internal:8771", "https://nodes.example.com",
])
def test_plain_http_is_allowed_for_same_machine_only(value):
    assert normalize_base(value) == value


@pytest.mark.parametrize("value", ["http://nodes.example.com", "http://10.0.0.9:8771"])
def test_plain_http_to_another_machine_is_refused(value):
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_base(value)


def test_directory_client_hot_add_remove_and_limit():
    client = PublicDirectoryClient([])
    assert client.bases == ()
    assert client.add("http://127.0.0.1:8771") == ("http://127.0.0.1:8771", True)
    assert client.add("http://127.0.0.1:8771/") == ("http://127.0.0.1:8771", False)
    assert client.bases == ("http://127.0.0.1:8771",)
    assert client.remove("http://127.0.0.1:8771") is True
    assert client.remove("http://127.0.0.1:8771") is False
    assert client.bases == ()


# ---------------- 热加种子：当场生效，不是等重启 ----------------

def test_hot_added_seed_becomes_a_neighbour_and_labels_why():
    local = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    remote = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    local.start()
    remote.start()
    try:
        assert local.seeds() == []
        assert local.add_seed("127.0.0.1", remote.p2p.port) is True
        target = remote.identity.did
        assert _wait(lambda: any(p["did"] == target
                                 for p in local.snapshot()["peers"]))
        peer = next(p for p in local.snapshot()["peers"] if p["did"] == target)
        # via 是"第一次怎么认识的"这个事实：种子连上来的就该标 bootstrap。
        assert peer["via"] == "bootstrap"
        assert local.seeds() == [["127.0.0.1", remote.p2p.port]]
    finally:
        local.stop()
        remote.stop()


def test_adding_the_same_seed_twice_is_idempotent():
    local = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    remote = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    local.start()
    remote.start()
    try:
        assert local.add_seed("127.0.0.1", remote.p2p.port) is True
        assert local.add_seed("127.0.0.1", remote.p2p.port) is False
        assert local.seeds() == [["127.0.0.1", remote.p2p.port]]
    finally:
        local.stop()
        remote.stop()


def test_removing_a_seed_stops_retrying_but_does_not_evict_the_neighbour():
    """删种子是"我不再主动去找你"，不是"我们从此不认识" —— 邻居关系是双向事实。"""
    local = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    remote = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    local.start()
    remote.start()
    try:
        local.add_seed("127.0.0.1", remote.p2p.port)
        target = remote.identity.did
        assert _wait(lambda: any(p["did"] == target
                                 for p in local.snapshot()["peers"]))
        assert local.remove_seed("127.0.0.1", remote.p2p.port) is True
        assert local.seeds() == []
        assert any(p["did"] == target for p in local.snapshot()["peers"])
        assert local.remove_seed("127.0.0.1", remote.p2p.port) is False
    finally:
        local.stop()
        remote.stop()


def test_hot_seed_needs_a_running_node_to_be_live():
    """节点没起（未 start）时加种子不报错，但也不该假装连上了。"""
    service = P2PDiscoveryService(Identity.generate(), port=_free_udp_port(), beacon=False)
    assert service.add_seed("127.0.0.1", 9) is True
    assert service.seeds() == [["127.0.0.1", 9]]
    assert service.snapshot()["peers"] == []


# ---------------- 控制台入口：落库 + 重启仍在 ----------------

def test_connect_node_applies_live_and_survives_restart(tmp_path, protector):
    p2p_port = _free_udp_port()
    daemon = Daemon(tmp_path, port=0, protector=protector, p2p_port=p2p_port,
                    beacon=False).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        status, body = http(base, "/v1/peers/connect",
                            {"address": "127.0.0.1:9799"}, token)
        assert status == 200
        assert body["kind"] == "seed"
        assert body["applied"] is True and body["added"] is True
        # 当场生效：P2P 服务的种子表里就有了它（不是只写了设置）
        assert ["127.0.0.1", 9799] in daemon.discovery.seeds()
        snapshot = daemon.management.snapshot()
        assert "127.0.0.1:9799" in snapshot["connections"]["seeds"]
        assert snapshot["connections"]["seeds_live"] is True
        # 重复连同一条：不算新加，也不报错
        _, again = http(base, "/v1/peers/connect", {"address": "127.0.0.1:9799"}, token)
        assert again["added"] is False
    finally:
        daemon.stop()

    restarted = Daemon(tmp_path, port=0, protector=protector,
                       p2p_port=_free_udp_port(), beacon=False).start()
    try:
        # 重启后必须还在：否则控制台"连过"就是一句空话
        assert ["127.0.0.1", 9799] in restarted.discovery.seeds()
        assert ("127.0.0.1", 9799) in restarted._merge_seeds(None)
    finally:
        restarted.stop()


def test_connect_node_merges_cli_and_saved_seeds(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector,
                    p2p_port=_free_udp_port(), beacon=False).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        http(base, "/v1/peers/connect", {"address": "127.0.0.1:9799"}, token)
    finally:
        daemon.stop()
    restarted = Daemon(tmp_path, port=0, protector=protector,
                       p2p_port=_free_udp_port(), beacon=False,
                       bootstrap=[("127.0.0.1", 9701)]).start()
    try:
        seeds = restarted.discovery.seeds()
        assert ["127.0.0.1", 9701] in seeds      # CLI 这一次额外加的
        assert ["127.0.0.1", 9799] in seeds      # 控制台保存的
    finally:
        restarted.stop()


def test_connect_node_url_is_a_directory_source_not_a_seed(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector,
                    p2p_port=_free_udp_port(), beacon=False).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        status, body = http(base, "/v1/peers/connect",
                            {"address": "http://127.0.0.1:9999/"}, token)
        assert status == 200
        assert body["kind"] == "public-node"
        assert body["address"] == "http://127.0.0.1:9999"
        snapshot = daemon.management.snapshot()
        assert snapshot["connections"]["public_nodes"] == ["http://127.0.0.1:9999"]
        # 目录源不进种子表：两种输入不许混为一谈
        assert snapshot["connections"]["seeds"] == []
        assert snapshot["channels"]["public_nodes"]["count"] == 1
    finally:
        daemon.stop()

    restarted = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        assert restarted.public_directories.bases == ("http://127.0.0.1:9999",)
    finally:
        restarted.stop()


def test_disconnect_removes_both_live_and_saved(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector,
                    p2p_port=_free_udp_port(), beacon=False).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        http(base, "/v1/peers/connect", {"address": "127.0.0.1:9799"}, token)
        http(base, "/v1/peers/connect", {"address": "http://127.0.0.1:9999"}, token)
        status, body = http(base, "/v1/peers/disconnect",
                            {"address": "127.0.0.1:9799"}, token)
        assert status == 200 and body["removed"] is True
        _, body2 = http(base, "/v1/peers/disconnect",
                        {"address": "http://127.0.0.1:9999"}, token)
        assert body2["removed"] is True
        snapshot = daemon.management.snapshot()
        assert snapshot["connections"]["seeds"] == []
        assert snapshot["connections"]["public_nodes"] == []
        assert daemon.management.saved_seeds() == []
        assert daemon.management.saved_public_nodes() == []
    finally:
        daemon.stop()


def test_connect_node_without_p2p_saves_but_says_it_did_not_apply(tmp_path, protector):
    """没开 P2P 时不许把"已保存"画成"已连上"。"""
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        status, body = http(base, "/v1/peers/connect",
                            {"address": "127.0.0.1:9799"}, token)
        assert status == 200
        assert body["applied"] is False and body["persisted"] is True
        assert "未启用 P2P" in body["note"]
        snapshot = daemon.management.snapshot()
        assert snapshot["connections"]["seeds_live"] is False
        assert snapshot["connections"]["seeds"] == ["127.0.0.1:9799"]
    finally:
        daemon.stop()


@pytest.mark.parametrize("address", ["", "127.0.0.1", "127.0.0.1:abc", "127.0.0.1:0"])
def test_connect_node_rejects_addresses_it_cannot_use(tmp_path, protector, address):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        status, body = http(base, "/v1/peers/connect", {"address": address}, token)
        assert status == 400
        assert "error" in body
        assert daemon.management.saved_seeds() == []
    finally:
        daemon.stop()


def test_remote_plaintext_directory_is_refused_over_http(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
    try:
        status, _ = http(base, "/v1/peers/connect",
                         {"address": "http://nodes.example.com"}, token)
        assert status == 400
        assert daemon.management.saved_public_nodes() == []
    finally:
        daemon.stop()
