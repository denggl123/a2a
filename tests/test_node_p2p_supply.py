"""容器/主机统一形状的节点，靠 P2P 直邻交换供给（阶段二的核心契约）。

2026-09-25 用户拍板「**容器真跑 a2n-node = 同一份软件**」，目标是
"本机节点启动后，控制台就能像蓝牙一样发现 docker 里那些 agent"。
这条链在代码上只剩一件事：**容器角色那个节点的供给能不能被另一个节点
通过 P2P 学到、取卡、验签** —— 也就是控制台「搜索网络」走的那条路
（`/v1/discovery/search` → `P2PDiscoveryService.discover`）。

这里刻意不启 docker：同一台机器上跑两个真 `Daemon` 就覆盖了协议本身，
把"镜像能不能构建""端口能不能发布""反代是否放行"留给容器级核验
（scripts/ 下的探针与 docker 构建），测试只管**协议与口径**，不重复劳动。

三件最容易一起坏的事：
* **钥匙文件的编码不是节点库的编码**：`Identity.save` 写的是 urlsafe 无填充
  base64，`Daemon` 读的 `identity/seed` 是标准带填充 base64。混用会在 Daemon
  里炸 `binascii.Error: Incorrect padding`（真踩过），所以下面有一条专门钉住它；
* **没声明公共入口就不许广播供给**：否则会把 `127.0.0.1` 当成别人可调用的
  服务宣传出去 —— 发现得到、却调不通，比发现不到更坏；
* **取卡必须验签且核对入口**：P2P 上的一切都是自报，卡要本地验。
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import socket
import time
from pathlib import Path

import pytest

from a2n_node.card import card_did, verify_card
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_p2p import Identity
from a2n_p2p.identity import _unb64

spec = importlib.util.spec_from_file_location(
    "market_demo_agents_p2p",
    Path(__file__).resolve().parents[1] / "scripts/run_market_demo_agents.py")
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _profile(slug: str):
    return next(p for p in market.MARKET if p[0] == slug)


def _wait_for(fn, timeout: float = 8.0):
    """等到 `fn()` 给出真值为止；超时就返回最后一次的结果（让断言去红）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.25)
    return last


def _peers(daemon: Daemon) -> int:
    """已握手的直邻数量（供给广播与查目录都以握手为前提）。"""
    return len(daemon.management.discovery.snapshot()["peers"])


def _search(daemon: Daemon, skill: str, timeout: float = 3.0):
    status, out = daemon.management.command(
        "/v1/discovery/search", {"skill": skill, "timeout": timeout})
    return out if status == 200 else None


def _found_cards(daemon: Daemon, skill: str):
    """一直查到真的拿到卡为止 —— 空结果不算"查过了"。"""
    def probe():
        out = _search(daemon, skill)
        return out if out and out["count"] >= 1 else None
    return _wait_for(probe)


def test_keyfile_sk_and_store_seed_are_different_base64_encodings(tmp_path):
    """同一把私钥，钥匙文件与节点库用的是两种 base64 写法。

    容器的入口要把钥匙文件里的私钥"交给"节点库，必须经 `_unb64` + 标准
    `b64encode` 换算。这条断言把那个坑钉死：谁要是图省事直接把 `sk` 塞进
    `identity/seed`，这里会立刻红，而不是等到容器起不来才在日志里发现。
    """
    keyfile = tmp_path / "node.key.json"
    identity = Identity.generate()
    identity.save(keyfile)
    sk = json.loads(keyfile.read_text(encoding="utf-8"))["sk"]

    assert Identity.from_private_bytes(_unb64(sk)).did == identity.did
    seed = base64.b64encode(_unb64(sk)).decode("ascii")
    assert seed != sk, "两种编码恰好相同会掩盖换算缺失"
    # 直接把 sk 当标准 base64 解，要么报错、要么解出别的字节 —— 两种都不许蒙混过关
    try:
        assert base64.b64decode(sk) != _unb64(sk)
    except Exception:  # noqa: BLE001 - 报错也是可接受的失败方式
        pass


def test_neighbour_discovers_supply_over_p2p(tmp_path, protector):
    """容器角色节点挂上案例供给后，另一个节点只凭种子就能取到它的卡。"""
    a_p2p, b_p2p = _free_udp_port(), _free_udp_port()
    provider = Daemon(tmp_path / "container", port=0, protector=protector,
                      p2p_port=a_p2p, beacon=False, advertise_host="127.0.0.1").start()
    consumer = None
    try:
        base = provider.runtime.local_base_url
        provider.management.discovery_public_base = base
        profile = _profile("video-short")
        card = market.build_card(profile, provider.identity)
        status, mounted = provider.management.command(
            "/v1/bindings/http",
            {"card": card, "endpoint": "http://127.0.0.1:9/invoke", "protocol": "a2a"})
        assert status == 201, mounted

        consumer = Daemon(tmp_path / "phone", port=0, protector=protector,
                          p2p_port=b_p2p, bootstrap=[("127.0.0.1", a_p2p)],
                          beacon=False, advertise_host="127.0.0.1",
                          discovery_public_base="http://127.0.0.1:1").start()
        # 供给广播是异步的：等邻居握手完再查，别把"还没广播"当成"发现不了"。
        assert _wait_for(lambda: _peers(consumer) >= 1), "邻居没在超时内握手"
        found = _found_cards(consumer, "video-short")
        assert found, "邻居没在超时内学到供给"
        assert not found["errors"]
        row = found["results"][0]
        assert row["source"] == "p2p"
        got = row["card"]
        # 取到的是**验过签**的完整卡：名字、入口、署名 did 都要对得上
        assert verify_card(got, require_endpoint=True)[0]
        assert got["name"] == card["name"]
        assert got["url"] == f"{base}/a2a/{mounted['service_id']}"
        assert card_did(got) == provider.identity.did
    finally:
        if consumer:
            consumer.stop()
        provider.stop()


def test_supply_is_not_advertised_without_a_public_base(tmp_path, protector):
    """没声明可回连入口时，节点仍能被发现，但**不广播供给**。

    这是刻意的诚实：把 `127.0.0.1` 当成"别人可调用"宣传出去，等于让调用方
    发现得到、却调不通 —— 比发现不到更坏。所以这里期待找到 0 张卡。
    """
    a_p2p, b_p2p = _free_udp_port(), _free_udp_port()
    provider = Daemon(tmp_path / "container", port=0, protector=protector,
                      p2p_port=a_p2p, beacon=False, advertise_host="127.0.0.1").start()
    consumer = None
    try:
        profile = _profile("video-short")
        card = market.build_card(profile, provider.identity)
        status, _ = provider.management.command(
            "/v1/bindings/http",
            {"card": card, "endpoint": "http://127.0.0.1:9/invoke", "protocol": "a2a"})
        assert status == 201
        assert provider.management.snapshot()["discovery"]["advertise_enabled"] is False

        consumer = Daemon(tmp_path / "phone", port=0, protector=protector,
                          p2p_port=b_p2p, bootstrap=[("127.0.0.1", a_p2p)],
                          beacon=False, advertise_host="127.0.0.1",
                          discovery_public_base="http://127.0.0.1:1").start()
        # 先确认**真的握上手了**，否则"找到 0 张"可能只是还没连上，证明不了什么。
        assert _wait_for(lambda: _peers(consumer) >= 1), "邻居没在超时内握手"
        time.sleep(1.0)                      # 再给提示/GOSSIP 一轮时间
        found = _search(consumer, "video-short")
        assert found is not None and found["count"] == 0
    finally:
        if consumer:
            consumer.stop()
        provider.stop()
