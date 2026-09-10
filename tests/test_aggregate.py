"""聚合 agent 护栏：策略注册制、轮询/加权、失败换人、冷却与半开、全败兜底。

聚合器在平台看来就是一个普通节点 —— 这些测试全部打桩，不起网络、不碰平台。
"""
from __future__ import annotations

import time

from a2n_sdk.aggregate import STRATEGIES, AggregateNode, Member, register_strategy


class FakeClient:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.metered: list[dict] = []

    def meter(self, started: float, **dims) -> dict:
        self.metered.append(dims)
        return dims

    def submit(self, task_id: str, result: object, usage: dict) -> dict:
        self.submitted.append(task_id)
        return {"passed": True, "amount": 1}

    def cancel_task(self, task_id: str, reason: str = "") -> dict:
        self.cancelled.append((task_id, reason))
        return {}


def _node(members: list[dict], strategy: str = "round_robin",
          cooldown_ms: int = 30_000) -> AggregateNode:
    card = {"name": "v-fin", "url": "http://localhost:1/a2a",
            "skills": [{"id": "fin"}]}
    node = AggregateNode(card, members, principal="acct:agg",
                         strategy=strategy, cooldown_ms=cooldown_ms,
                         base_url="http://127.0.0.1:1")
    node.client = FakeClient()
    return node


def _task(tid: str) -> dict:
    return {"id": tid, "skill_id": "fin", "payload": {"x": 1}}


# ---------- 1. 策略注册制：新策略 = 注册，分派主体零改动 ----------

def test_register_custom_strategy():
    def pick_last(members, state):
        return list(members)[::-1]
    register_strategy("test-reverse", pick_last)
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}], strategy="test-reverse")
    assert [m.agent_id for m in node._order()] == ["a2", "a1"]
    del STRATEGIES["test-reverse"]          # 测试自清理


def test_unknown_strategy_rejected():
    try:
        _node([{"agent_id": "a1"}], strategy="no-such")
        assert False, "应拒绝未注册策略"
    except ValueError as e:
        assert "未知聚合策略" in str(e)


# ---------- 2. 内置策略：轮询与加权 ----------

def test_round_robin_rotates():
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}, {"agent_id": "a3"}])
    seq = [[m.agent_id for m in node._order()] for _ in range(3)]
    assert seq[0] == ["a1", "a2", "a3"]
    assert seq[1] == ["a2", "a3", "a1"]      # 游标推进，起头轮换
    assert seq[2] == ["a3", "a1", "a2"]


def test_weighted_prefers_high_weight():
    node = _node([{"agent_id": "a1", "weight": 5}, {"agent_id": "a2", "weight": 1}],
                 strategy="weighted")
    picks = [node._order()[0].agent_id for _ in range(12)]
    assert picks.count("a1") > picks.count("a2")   # 高权重显著更常先被选
    assert "a2" in picks                            # SWRR 保证低权重也轮到


# ---------- 3. failover：失败换人重试；成功即返回 ----------

def test_failover_switches_member():
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}])
    tried: list[str] = []

    def fwd(m: Member, task: dict):
        tried.append(m.agent_id)
        if m.agent_id == "a1":
            raise RuntimeError("成员挂了")
        return {"ok": True}

    node._forward = fwd
    node._handle(_task("t1"))
    assert tried == ["a1", "a2"]                       # 失败换下一个
    assert node.client.submitted == ["t1"]             # 任务仍成功提交
    assert node.stats["tasks_ok"] == 1


def test_cooldown_skips_then_recovers():
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}])
    tried: list[str] = []

    def fwd(m: Member, task: dict):
        tried.append(m.agent_id)
        if m.agent_id == "a1" and len(tried) <= 2:     # 前两次 a1 都挂
            raise RuntimeError("挂")
        return {"ok": True}

    node._forward = fwd
    node._handle(_task("t1"))                          # a1 挂 -> a2 成
    node._handle(_task("t2"))                          # a1 在冷却，直接 a2
    assert tried == ["a1", "a2", "a2"]
    m1 = next(m for m in node.members if m.agent_id == "a1")
    assert not m1.healthy()                            # 冷却中
    m1.cooldown_until = 0.0                            # 冷却到期（定时刷新状态）
    node._handle(_task("t3"))
    assert tried[-1] == "a1"                           # 恢复后重新可用


def test_all_cooling_half_open_probe():
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}])
    for m in node.members:
        node._penalize(m)                              # 全员冷却
    tried: list[str] = []
    node._forward = lambda m, t: (tried.append(m.agent_id), {"ok": True})[1]
    node._handle(_task("t1"))
    assert tried == [min(node.members, key=lambda m: m.cooldown_until).agent_id]
    assert node.client.submitted == ["t1"]             # 半开探测成功也交付


# ---------- 4. 全员失败：任务取消，不悬空 ----------

def test_all_fail_cancels_task():
    node = _node([{"agent_id": "a1"}, {"agent_id": "a2"}])

    def fwd(m: Member, task: dict):
        raise RuntimeError("全挂")

    node._forward = fwd
    node._handle(_task("t1"))
    assert node.client.cancelled and node.client.cancelled[0][0] == "t1"
    assert "聚合成员全部失败" in node.client.cancelled[0][1]
    assert node.stats["tasks_failed"] == 1 and node.client.submitted == []


# ---------- 5. 心跳自报带成员健康；快照本地可见 ----------

def test_metrics_report_member_health():
    node = _node([{"agent_id": "a1", "weight": 3}], strategy="weighted")
    node.members[0].ok = 7
    node.members[0].rtt = [100, 200]
    m = node._metrics()
    agg = m["aggregate"]
    assert agg["strategy"] == "weighted"
    assert agg["members"][0]["agent_id"] == "a1"
    assert agg["members"][0]["weight"] == 3
    assert agg["members"][0]["ok"] == 7
    assert agg["members"][0]["rtt_avg_ms"] == 150
    assert agg["members"][0]["cooling"] is False
    assert m["ttft_avg_ms"] is None                    # 虚拟 agent 自身还没接过单
