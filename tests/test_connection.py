"""连接与可达性：家宽 PC 没有公网 IP，凭什么接得到单。

核心断言：
1. 内网地址声明 direct 会被强制降级为 pull（防"派了单却送不进去"）
2. pull 模式只看心跳，新节点有宽限期
3. 心跳超时的节点不可派单（预算不能冻在永远不会执行的任务上）
4. NAT 判定：平台比对本机自报 IP 与心跳来源 IP
5. 长轮询：有任务立即返回，无任务不空转打爆 DB
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from a2n_store import conn
from a2n_dispatch import discovery
from a2n_registry import registry
from a2n_task import tasks
from a2n_server.routers.custodian import DepositIn, deposit
from a2n_kernel.hashing import new_id, now_iso
from a2n_registry.reachability import is_public_url, normalize_connection, reachable

from .test_flow import demo_card


def _aged(agent_id: str, hours: float = 1.0) -> None:
    """把节点注册时间拨回过去，模拟"从未心跳的陈旧节点"。"""
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = conn()
    c.execute("UPDATE agents SET registered_at=?, last_seen_at=NULL WHERE agent_id=?", (old, agent_id))
    c.commit()


def test_inbound_declaration_downgraded():
    """127.0.0.1 / 192.168.x 声明 direct 不认 —— 否则派单必送不进去。"""
    for bad in ("http://127.0.0.1:9000", "http://192.168.1.5:8080", "http://localhost:9000"):
        c = normalize_connection({"mode": "direct", "url": bad}, None)
        assert c["mode"] == "pull" and c["downgraded"], bad
    assert is_public_url("https://node.example.com")
    assert not is_public_url("http://10.0.0.3")
    assert not is_public_url(None)


def test_pull_needs_no_public_ip():
    s = new_id("")[2:]
    agent = registry.register(f"acct:n{s}", demo_card("n-" + s))
    ok, why = reachable(registry.get(agent["agent_id"]))
    assert ok and "宽限期" in why
    _aged(agent["agent_id"])
    ok, why = reachable(registry.get(agent["agent_id"]))
    assert not ok and "从未心跳" in why
    ok, why = discovery.assignable(agent["agent_id"])
    assert not ok and "不可达" in why


def test_heartbeat_timeout_blocks_dispatch():
    s = new_id("")[2:]
    user, provider = f"acct:hu{s}", f"acct:hp{s}"
    deposit(DepositIn(account_id=user, amount_fen=5000))
    agent = registry.register(provider, demo_card("hp-" + s))
    registry.heartbeat(agent["agent_id"])
    assert tasks.create(user, "ocr-pro", {"x": 1}, budget=10)["state"] == "ASSIGNED"

    # 心跳窗口过去之后，同一节点不再接收新任务
    stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn().execute("UPDATE agents SET last_seen_at=? WHERE agent_id=?", (stale, agent["agent_id"]))
    conn().commit()
    ok, why = discovery.assignable(agent["agent_id"])
    assert not ok and "心跳超时" in why


def test_nat_verdict_by_peer_ip():
    s = new_id("")[2:]
    agent = registry.register(f"acct:np{s}", demo_card("np-" + s))
    aid = agent["agent_id"]

    r = registry.heartbeat(aid, {"local_ips": ["192.168.1.7", "10.0.0.2"]}, peer_ip="203.0.113.9")
    assert r["nat"] == "natted" and r["peer_ip"] == "203.0.113.9"

    r = registry.heartbeat(aid, {"local_ips": ["203.0.113.9"]}, peer_ip="203.0.113.9")
    assert r["nat"] == "public"


def test_discovery_exposes_connection():
    s = new_id("")[2:]
    agent = registry.register(f"acct:dc{s}", demo_card("dc-" + s))
    registry.heartbeat(agent["agent_id"], {"local_ips": ["172.16.0.9"]}, peer_ip="198.51.100.4")
    found = [a for a in discovery.query({"skill": "ocr-pro"}, limit=50)
             if a["agent_id"] == agent["agent_id"]]
    assert found, "应能被发现"
    a = found[0]
    assert a["connection"]["mode"] == "pull"
    assert a["connection"]["nat"] == "natted"
    assert a["reachable"] is True
    only_ok = [x["agent_id"] for x in discovery.query(
        {"skill": "ocr-pro"}, filt={"reachable": True}, limit=50)]
    assert agent["agent_id"] in only_ok


def test_long_poll_returns_immediately_with_task():
    s = new_id("")[2:]
    user, provider = f"acct:lu{s}", f"acct:lp{s}"
    deposit(DepositIn(account_id=user, amount_fen=5000))
    agent = registry.register(provider, demo_card("lp-" + s))
    registry.heartbeat(agent["agent_id"])

    def late_create():
        time.sleep(0.5)
        tasks.create(user, "ocr-pro", {"x": 1}, budget=10, preferred_agents=[agent["agent_id"]])

    threading.Thread(target=late_create, daemon=True).start()
    started = time.time()
    got = tasks.pending_for(agent["agent_id"], wait=10)
    assert got and got[0]["skill_id"] == "ocr-pro"
    assert time.time() - started < 3, "长轮询应在任务出现后立即返回"

    # 取走并提交后，不应再重复吐出同一任务
    tasks.submit(got[0]["id"], agent["agent_id"], {"o": 1},
                 {"call_count": 1, "wall_time_ms": 300, "output_tokens": 4})
    empty_started = time.time()
    assert tasks.pending_for(agent["agent_id"], wait=0) == []
    assert time.time() - empty_started < 0.5, "wait=0 应立即返回"


def test_heartbeat_carries_self_reported_metrics():
    """TTFT 等自报指标随心跳入库；且心跳不把平台探测的 rtt_ms 冲成 None。"""
    s = new_id("")[2:]
    agent = registry.register(f"acct:m{s}", demo_card("m-" + s))
    aid = agent["agent_id"]
    # 预置一次平台探测结果（direct 入站探测的 rtt），心跳后必须保留
    c = conn()
    c.execute("UPDATE agents SET connection=? WHERE agent_id=?",
              (json.dumps({"mode": "pull", "rtt_ms": 88}), aid))
    c.commit()
    registry.heartbeat(aid, {"mode": "pull",
                             "metrics": {"ttft_avg_ms": 120, "ttft_samples": 5}})
    cd = registry.get(aid)["connection"]
    assert cd["metrics"]["ttft_avg_ms"] == 120 and cd["metrics"]["ttft_samples"] == 5
    assert cd["rtt_ms"] == 88                       # 心跳不冲掉平台探测值
    registry.heartbeat(aid, {"mode": "pull",
                             "metrics": {"ttft_avg_ms": 90, "ttft_samples": 9}})
    assert registry.get(aid)["connection"]["metrics"]["ttft_avg_ms"] == 90  # 新指标覆盖旧指标


def test_runner_ttft_window():
    """服务端 SDK 的 TTFT 滑动窗口（最近 20 次）→ _metrics 自报口径。"""
    from a2n_sdk.runner import Node
    n = Node({"name": "x", "url": "http://localhost:1/a2a", "skills": [{"id": "s"}]},
             handlers={}, principal="acct:ttft", base_url="http://127.0.0.1:1")
    assert n.stats["ttft"] == []
    n.stats["ttft"] = [100, 200, 300]
    m = n._metrics()
    assert m["ttft_avg_ms"] == 200 and m["ttft_samples"] == 3
    assert m["avg_ms"] is None                      # 还没完成任务，总耗时口径为空
    n.stats["ttft"] = list(range(1, 26))            # 只留最近 20 次是提交侧的责任
    assert len(n.stats["ttft"]) == 25               # runner 提交处 [:20]，此处验证 _metrics 不截断
    assert n._metrics()["ttft_avg_ms"] == int(sum(range(1, 26)) / 25)


def test_caller_observation_rules():
    """使用端实测回传：绑任务校验两端、无绑定进窗口、滑窗 20 截断。

    数据归属：端到端往返只有使用端测得到，回传给平台聚合 —— 谁测的谁自报。
    """
    import pytest
    from a2n_kernel.errors import ConflictError

    s = new_id("")[2:]
    user = f"acct:ou{s}"
    agent = registry.register(f"acct:op{s}", demo_card("o-" + s))
    aid = agent["agent_id"]

    # ① 绑了不存在的任务 → 拒（防刷第一道）
    with pytest.raises(ConflictError, match="对不上"):
        registry.observe(aid, user, {"task_id": "t-fake", "total_ms": 100})

    # ② 无任务绑定（relay 直调场景）：进窗口聚合
    for ms in (100, 200, 300):
        registry.observe(aid, user, {"total_ms": ms})
    ob = registry.get(aid)["connection"]["observed"]
    assert ob["total"] == [100, 200, 300] and ob["samples"] == 3

    # ③ 真任务绑定：requester/node 两端对上才收
    t = tasks.create(user, "ocr-pro", {}, 10, hold_budget=False)
    conn().execute("UPDATE tasks SET node_id=? WHERE id=?", (aid, t["id"]))
    conn().commit()
    registry.observe(aid, user, {"task_id": t["id"], "rtt_ms": 40, "total_ms": 150})
    ob = registry.get(aid)["connection"]["observed"]
    assert ob["rtt"] == [40] and ob["total"][-1] == 150
    # 别的主体冒充同一任务的使用方 → 拒
    with pytest.raises(ConflictError):
        registry.observe(aid, f"acct:other{s}", {"task_id": t["id"], "total_ms": 1})

    # ④ 滑动窗口 20：只留最近 20 次
    for i in range(25):
        registry.observe(aid, user, {"total_ms": i})
    ob = registry.get(aid)["connection"]["observed"]
    assert len(ob["total"]) == 20 and ob["total"][-1] == 24 and ob["samples"] == 20
