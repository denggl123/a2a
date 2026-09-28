"""「同一份供给装两次」的硬口径 —— 重启幂等，且商品身份不跟地址走。

2026-09-26 在公网机上真踩到：第一次起节点是好的（节点目录是新的），
**同一目录重启就崩** —— `RuntimeManagement.restore()` 启动时把上次的挂载从库里
重新挂上了（`management.py:94`），启动脚本再照旧打一次 `/v1/bindings/http`
就撞 `BindingTable.add` 的既有性检查（`upstream.py:421`），
`ValueError: service_id 已存在`，节点没起来。

容器的 `docker/node_entry.py` 是同一个形状（`A2N_HOME` 落在挂卷 `/app/state` 上），
只是此前每次都用新卷，所以没被照出来。

2026-09-29（R0-2）起，供给的 service_id **只由"节点身份 + 逻辑商品标识(uid)"决定**，
与上游地址/端口/协议无关。于是 `mount_supply` 的三种情形：
* 不在挂载表 → 挂上（新建）；
* 已在、且上游/协议一致 → 跳过（**重启幂等**）；
* 已在、但上游/协议变了 → **原地更新**这一份供给（同一商品换地址，service_id 不变，
  不重置试用/样品/信誉）。这不是"改绑到别人家的服务"：service_id 里带着本节点身份，
  节点没换、商品没换，只是它现在监听的地址变了（容器绿灯上游用随机端口，重启必换）。
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

spec = importlib.util.spec_from_file_location(
    "market_demo_agents_supply",
    Path(__file__).resolve().parents[1] / "scripts/run_market_demo_agents.py")
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)

PROFILES = [p for p in market.MARKET if p[0] in ("video-short", "finance-report")]
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
        ids = [supply_id(first, market.build_card(p, first.identity))
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


def test_mount_supply_updates_endpoint_for_same_product(tmp_path, protector):
    """R0-2：同一逻辑商品换上游地址 → **原地更新**，service_id 不变、不新增挂载。

    容器绿灯上游用随机端口，重启必换地址。商品身份若跟着地址走，目录里就会
    新旧两张卡（一张死、一张活）。改法：身份只由"节点身份 + 逻辑商品(uid)"决定
    （`supply_id`），换地址＝更新同一份供给，因此按 service_id 累积的试用/样品/
    信誉不被打断。
    """
    daemon = _start(tmp_path / "node", protector, _free_udp_port())
    try:
        profile = PROFILES[0]
        card = market.build_card(profile, daemon.identity)
        sid = supply_id(daemon, card)

        # 先挂在一个"旧地址"上
        mount_supply(daemon, daemon.identity, [profile], build_card=market.build_card,
                     endpoint="http://127.0.0.1:1/old", protocol="a2a", tag="t")
        before = daemon.runtime.bindings.get(sid)
        assert before is not None
        assert (before.metadata or {})["endpoint"] == "http://127.0.0.1:1/old"
        count_before = len(daemon.runtime.bindings.list())

        # 同一商品换到一个"新地址"（模拟重启换端口）→ 更新，而不是新增
        added = mount_supply(daemon, daemon.identity, [profile], build_card=market.build_card,
                             endpoint="http://127.0.0.1:2/new", protocol="a2a", tag="t")
        assert added == 1, "换地址应算作更新（计数 1），不是跳过、也不是新增一条"
        after = daemon.runtime.bindings.get(sid)
        assert after is not None, "service_id 应不变，仍是同一份供给"
        assert (after.metadata or {})["endpoint"] == "http://127.0.0.1:2/new"
        assert len(daemon.runtime.bindings.list()) == count_before, "不该多出挂载"
    finally:
        daemon.stop()


def test_supply_id_is_independent_of_upstream(tmp_path, protector):
    """同一商品的 service_id 与上游地址/协议无关 —— 这正是"重启不多卡"的前提。"""
    daemon = _start(tmp_path / "node", protector, _free_udp_port())
    try:
        card = market.build_card(PROFILES[0], daemon.identity)
        assert supply_id(daemon, card) == supply_id(daemon, card)
        # 换协议不该改身份（协议只是传输）
        assert supply_id(daemon, card) == supply_id(daemon, card, product=None)
        # 不同商品身份必须不同
        other = market.build_card(market.MARKET[1], daemon.identity)
        assert supply_id(daemon, card) != supply_id(daemon, other)
    finally:
        daemon.stop()


def test_mount_supply_uses_declared_protocol_green_upstream_really_works(
        tmp_path, protector):
    """R0-1：绿灯上游收**普通 JSON**，必须按 `json` 挂 —— 按 `a2a` 挂会协议错配。

    现象（2026-09-29 实测）：卡是活的、节点是活的、请求也送到了，断在协议翻译这一跳 ——
    上游 `body.get("skill")` 在 A2A JSON-RPC 包里取到 None，如实回"这台上游没有技能 None"。
    这个测试把"按声明协议挂 → 真的拿到成品"钉住。
    """
    from a2n_sdk.greenlight import serve_http
    from a2n_sdk.ports import CallRequest

    server, endpoint = serve_http(["video-short"])
    daemon = _start(tmp_path / "node", protector, _free_udp_port())
    try:
        profile = [p for p in market.MARKET if p[0] == "video-short"][0]
        mount_supply(daemon, daemon.identity, [profile],
                     build_card=market.build_card, endpoint=endpoint,
                     protocol="json", tag="t")
        card = market.build_card(profile, daemon.identity)
        sid = supply_id(daemon, card)
        binding = daemon.runtime.bindings.get(sid)
        assert binding is not None and (binding.metadata or {})["protocol"] == "json"

        resp = daemon.runtime.invoke_binding(
            sid, CallRequest(skill="video-short", payload="做一个 30 秒产品种草短视频"))
        assert resp.ok, f"按 json 挂后应真返回成品，却失败：{resp.error!r}"
        assert resp.result, "绿灯上游应返回非空成品"
    finally:
        daemon.stop()
        server.shutdown()


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
