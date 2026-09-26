"""「同一份供给装两次」的硬口径 —— 重启幂等，且绝不静默改绑。

2026-09-26 在公网机上真踩到：第一次起节点是好的（节点目录是新的），
**同一目录重启就崩** —— `RuntimeManagement.restore()` 启动时把上次的挂载从库里
重新挂上了（`management.py:94`），启动脚本再照旧打一次 `/v1/bindings/http`
就撞 `BindingTable.add` 的既有性检查（`upstream.py:421`），
`ValueError: service_id 已存在`，节点没起来。

容器的 `docker/node_entry.py` 是同一个形状（`A2N_HOME` 落在挂卷 `/app/state` 上），
只是此前每次都用新卷，所以没被照出来。修法在 `a2n_node.supply.mount_supply`：
* 已在、上游一致 → 跳过（幂等）；
* 已在、上游不同 → 响亮报错（`BindingTable.add` 拒绝覆盖是对的，静默改绑等于
  让人以为服务的还是原来那台）；
* 不在 → 走控制台那条命令挂上。
"""
from __future__ import annotations

import base64
import importlib.util
import os
import socket
from pathlib import Path

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.supply import mount_supply, open_public_service, supply_id
from a2n_sdk.upstream import A2AUpstream, AgentBinding

spec = importlib.util.spec_from_file_location(
    "market_demo_agents_supply",
    Path(__file__).resolve().parents[1] / "scripts/run_market_demo_agents.py")
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)

PROFILES = [p for p in market.MARKET if p[0] in ("video-short", "finance-brief")]
DEAD = "http://127.0.0.1:9/invoke"


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _start(home: Path, protector, p2p_port: int) -> Daemon:
    return Daemon(home, port=0, protector=protector, p2p_port=p2p_port,
                  beacon=False, advertise_host="127.0.0.1").start()


def test_mount_supply_is_idempotent_across_restart(tmp_path, protector):
    """同一节点目录重启：供给不重复挂，也不报错，挂载 id 与上游都不变。"""
    home = tmp_path / "node"
    first = _start(home, protector, _free_udp_port())
    try:
        added = mount_supply(first, first.identity, PROFILES,
                             build_card=market.build_card, endpoint=DEAD, tag="t")
        assert added == len(PROFILES), "首启应把每份供给都挂上"
        ids = [supply_id(first, market.build_card(p, first.identity), DEAD)
               for p in PROFILES]
        for sid in ids:
            assert first.runtime.bindings.get(sid) is not None
    finally:
        first.stop()

    # 重启：同一个 home（= 容器挂卷/公网机持久目录的真实形状）
    second = _start(home, protector, _free_udp_port())
    try:
        # 前提：restore() 已经把上次的挂载恢复回来 —— 这正是"崩"的来源
        for sid in ids:
            restored = second.runtime.bindings.get(sid)
            assert restored is not None, "重启后供给应从库里恢复"
            assert (restored.metadata or {}).get("endpoint") == DEAD

        # 再挂一次必须**安静地什么都不做**，而不是抛"service_id 已存在"
        added_again = mount_supply(second, second.identity, PROFILES,
                                   build_card=market.build_card, endpoint=DEAD, tag="t")
        assert added_again == 0, "重启后不该新增挂载"
        assert len(second.runtime.bindings.list()) == len(PROFILES), "挂载数不该翻倍"
    finally:
        second.stop()


def test_mount_supply_refuses_to_rebind_a_different_upstream(tmp_path, protector):
    """挂载表里已有同 id 但上游不同的条目时必须响亮拒绝，不悄悄改绑。"""
    daemon = _start(tmp_path / "node", protector, _free_udp_port())
    try:
        profile = PROFILES[0]
        card = market.build_card(profile, daemon.identity)
        sid = supply_id(daemon, card, DEAD)
        # 手工塞一条"同 id、另一个上游"的挂载（模拟历史上曾挂到别处）
        daemon.runtime.bindings.add(AgentBinding(
            service_id=sid, source_card=card, upstream=A2AUpstream("http://127.0.0.1:1/x"),
            source_kind="remote", metadata={"endpoint": "http://127.0.0.1:1/x"}))

        with pytest.raises(SystemExit) as err:
            mount_supply(daemon, daemon.identity, [profile],
                         build_card=market.build_card, endpoint=DEAD, tag="t")
        assert "拒绝静默改绑" in str(err.value)
        # 拒绝之后那条旧挂载保持原样，没被顺手覆盖
        assert (daemon.runtime.bindings.get(sid).metadata or {})["endpoint"] \
            == "http://127.0.0.1:1/x"
    finally:
        daemon.stop()


def test_open_public_service_reports_when_it_cannot_open(tmp_path, protector):
    """没有公开入口时如实返回"没开"，不抛错、也不假装开过。"""
    daemon = _start(tmp_path / "node", protector, _free_udp_port())
    try:
        assert open_public_service(daemon, None, enabled=True, tag="t") is False
        assert daemon.management.public_service_enabled is False
        # 显式关闭同理
        assert open_public_service(daemon, "http://127.0.0.1:9",
                                   enabled=False, tag="t") is False
        assert daemon.management.public_service_enabled is False
    finally:
        daemon.stop()
