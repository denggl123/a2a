"""使用端聚合护栏：策略注册制、轮询/加权、失败换人、冷却与半开、全失败抛错。

使用端聚合不注册卡、不接任务、不记账 —— 每次成功调用都是
"使用方 ↔ 真实 agent"的一对一交易。全部打桩，不起网络、不碰平台。
"""
from __future__ import annotations

from typing import Any

import pytest

from a2n_sdk.aggregate import (STRATEGIES, AggregationFailed, Aggregator,
                               Candidate, register_strategy)


class FakeClient:
    """call_agent 可按 agent_id 注入失败；真实 SDK 里它自动计时+回传观测。"""

    def __init__(self, fail: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail or {}          # agent_id -> 剩余失败次数

    def call_agent(self, agent_id: str, path: str = "", body: Any = None,
                   token: str | None = None) -> dict:
        self.calls.append((agent_id, path))
        if self.fail.get(agent_id, 0) > 0:
            self.fail[agent_id] -= 1
            raise RuntimeError(f"{agent_id} 挂了")
        return {"ok": True, "via": agent_id}


def _agg(candidates: list[dict], strategy: str = "round_robin",
         cooldown_ms: int = 30_000, fail: dict[str, int] | None = None):
    cli = FakeClient(fail=fail)
    return Aggregator(cli, candidates, strategy=strategy,
                      cooldown_ms=cooldown_ms), cli


# ---------- 1. 策略注册制：新策略 = 注册，分派主体零改动 ----------

def test_register_custom_strategy():
    def pick_last(candidates, state):
        return list(candidates)[::-1]
    register_strategy("test-reverse", pick_last)
    agg, _ = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}], strategy="test-reverse")
    assert [c.agent_id for c in agg._order()] == ["a2", "a1"]
    del STRATEGIES["test-reverse"]          # 测试自清理


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError, match="未知聚合策略"):
        _agg([{"agent_id": "a1"}], strategy="no-such")


def test_empty_candidates_rejected():
    with pytest.raises(ValueError, match="至少要有一个候选"):
        _agg([])


# ---------- 2. 内置策略：轮询与加权 ----------

def test_round_robin_rotates():
    agg, _ = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}, {"agent_id": "a3"}])
    seq = [[c.agent_id for c in agg._order()] for _ in range(3)]
    assert seq[0] == ["a1", "a2", "a3"]
    assert seq[1] == ["a2", "a3", "a1"]      # 游标推进，起头轮换
    assert seq[2] == ["a3", "a1", "a2"]


def test_weighted_prefers_high_weight():
    agg, _ = _agg([{"agent_id": "a1", "weight": 5}, {"agent_id": "a2", "weight": 1}],
                  strategy="weighted")
    picks = [agg._order()[0].agent_id for _ in range(12)]
    assert picks.count("a1") > picks.count("a2")   # 高权重显著更常先被选
    assert "a2" in picks                            # SWRR 保证低权重也轮到


# ---------- 3. failover：失败换人重试；成功即返回 ----------

def test_failover_switches_candidate():
    agg, cli = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}], fail={"a1": 1})
    out = agg.call("fin", {"x": 1})
    assert out["via"] == "a2"                          # a1 挂，a2 接住
    assert [c for c, _ in cli.calls] == ["a1", "a2"]   # 先试 a1 再换 a2
    assert [p for _, p in cli.calls] == ["fin", "fin"] # path 用 skill id


def test_cooldown_skips_then_recovers():
    agg, _ = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}], fail={"a1": 1})
    assert agg.call("fin")["via"] == "a2"              # a1 挂 → a2 接住
    assert agg.call("fin")["via"] == "a2"              # a1 冷却中被跳过
    a1 = next(c for c in agg.candidates if c.agent_id == "a1")
    assert not a1.healthy() and a1.fail_streak == 1    # 冷却中（30s×1）
    a1.cooldown_until = 0.0                            # 定时刷新状态：到期
    assert agg.call("fin")["via"] == "a1"              # 恢复后轮到它，重试成功


def test_all_cooling_half_open_probe():
    agg, _ = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}])
    for c in agg.candidates:
        agg._penalize(c)                               # 全员冷却
    assert agg.call("fin")["via"] == min(             # 半开：只试冷却最早到期的
        agg.candidates, key=lambda c: c.cooldown_until).agent_id


# ---------- 4. 全员失败：抛 AggregationFailed，每个候选都试过 ----------

def test_all_fail_raises_aggregation_error():
    agg, _ = _agg([{"agent_id": "a1"}, {"agent_id": "a2"}], fail={"a1": 9, "a2": 9})
    with pytest.raises(AggregationFailed) as ei:
        agg.call("fin")
    assert set(ei.value.errors) == {"a1", "a2"}        # 每个候选的最后错误都在
    assert "a1 挂了" in ei.value.errors["a1"]


# ---------- 5. 健康快照：使用端实测，本地私账 ----------

def test_health_snapshot():
    agg, _ = _agg([{"agent_id": "a1", "weight": 3}], strategy="weighted")
    agg.candidates[0].ok = 7
    agg.candidates[0].rtt = [100, 200]
    h = agg.health()[0]
    assert h["agent_id"] == "a1" and h["weight"] == 3
    assert h["ok"] == 7 and h["rtt_avg_ms"] == 150 and h["cooling"] is False
