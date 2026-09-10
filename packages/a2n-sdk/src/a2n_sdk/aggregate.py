"""聚合 agent：把多个成员 agent 聚合成一个稳定的虚拟服务。

对外：虚拟 agent 是一张**普通卡** —— 注册、发现、门禁、计量、结算全走既有链路，
平台不感知聚合的存在。聚合是供给方的自由（我怎么提供我的能力是我的事），
不是平台的新事实；表归属、三铁律一行都不用动。

对内：收到任务后按策略转发给成员，失败换人重试，直到成功或全员失败。
成员健康是聚合器作为**使用端**的实测（docs/CALL-PARAMS.md 口径），
谁测的谁自报 —— 心跳 metrics 带成员健康，标注还是"自报"。

账目闭环：使用方 → 虚拟 agent（平台结算，虚拟 agent 是节点身份）；
虚拟 agent → 成员（对等账户/直付，聚合器是成员的使用端）。差额就是聚合毛利，
平台只刻章不碰钱，两边账各自成立、互不混。

策略注册制（与 settle.register_settler 同一模式）：新策略 = register_strategy，
分派主体零改动。内置 round_robin（轮询）与 weighted（平滑加权轮询，
高权重先被选、挂了进冷却自动落到低权重 —— "谁稳用谁"）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .runner import Node

# 半开探测：全员冷却时宁可试一个冷却最早到期的，也不直接判死
# 冷却时长 = cooldown_ms/1000 × min(连续失败次数, 5) —— 连挂连加长，恢复自动回
MAX_COOLDOWN_MULT = 5
RTT_WINDOW = 20


class Member:
    """一个成员 agent：权重 + 聚合器实测的健康状态（内部真相，聚合器自持）。"""

    def __init__(self, agent_id: str, weight: int = 1) -> None:
        self.agent_id = agent_id
        self.weight = max(1, int(weight))
        self.ok = 0
        self.fail = 0
        self.fail_streak = 0
        self.cooldown_until = 0.0
        self.rtt: list[int] = []

    def healthy(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.cooldown_until


STRATEGIES: dict[str, Callable[[list[Member], dict], list[Member]]] = {}


def register_strategy(name: str, fn: Callable[[list[Member], dict], list[Member]]) -> None:
    """注册聚合策略。fn(可用成员, 共享状态) -> 尝试顺序（第一个最优先）。"""
    STRATEGIES[name.lower()] = fn


def _round_robin(members: list[Member], state: dict) -> list[Member]:
    """轮询：游标依次起头，失败顺延。"""
    state["rr"] = state.get("rr", -1) + 1
    n = len(members)
    return [members[(state["rr"] + i) % n] for i in range(n)]


def _weighted(members: list[Member], state: dict) -> list[Member]:
    """平滑加权轮询（SWRR）：高权重先被选且占比≈权重比，低权重也保证轮到。

    "高权重可以用就先用高权重的"：权重是聚合器的配置（声明），
    高权重挂了进冷却自然落到低权重 —— 稳定性由 failover 兜底，不由权重许诺。
    """
    cur = state.setdefault("swrr", {})
    for m in members:
        cur.setdefault(m.agent_id, 0)
    total = sum(m.weight for m in members)
    for m in members:
        cur[m.agent_id] += m.weight
    pick = max(members, key=lambda m: cur[m.agent_id])
    cur[pick.agent_id] -= total
    rest = sorted((m for m in members if m is not pick),
                  key=lambda m: -cur[m.agent_id])
    return [pick, *rest]


register_strategy("round_robin", _round_robin)
register_strategy("weighted", _weighted)


class AggregateNode(Node):
    """虚拟 agent 节点：一张卡对外，N 个成员对内。

    用法：
        node = AggregateNode(
            card=finance_card,                      # 虚拟卡：自己的名字/价目/技能
            members=[{"agent_id": "ag_x", "weight": 5},
                     {"agent_id": "ag_y", "weight": 1}],
            principal="acct:me", strategy="weighted")
        node.serve(tunnel=True)                     # 与普通节点一样常驻接单

    成员前提：可被中继调用（relay/tunnel 模式）且与聚合器 ACTIVE 配对。
    """

    def __init__(self, card: dict, members: list, principal: str,
                 strategy: str = "weighted", cooldown_ms: int = 30_000,
                 base_url: str = "http://127.0.0.1:8000",
                 heartbeat_interval: int = 30, max_tries: int | None = None) -> None:
        super().__init__(card, handlers={}, principal=principal,
                         base_url=base_url, heartbeat_interval=heartbeat_interval)
        if not members:
            raise ValueError("聚合至少要有一个成员 agent")
        self.members = [m if isinstance(m, Member) else Member(m["agent_id"], m.get("weight", 1))
                        for m in members]
        if strategy not in STRATEGIES:
            raise ValueError(f"未知聚合策略：{strategy}（已注册：{sorted(STRATEGIES)}）")
        self.strategy_name = strategy
        self._strategy = STRATEGIES[strategy]
        self._state: dict = {}
        self.cooldown_ms = cooldown_ms
        self.max_tries = max_tries or len(self.members)

    # ---- 成员健康：聚合器实测，谁测的谁自报 ----
    def _reward(self, m: Member, ms: int) -> None:
        m.ok += 1
        m.fail_streak = 0
        m.rtt = ([ms] + m.rtt)[:RTT_WINDOW]

    def _penalize(self, m: Member) -> None:
        """失败进冷却：连续失败次数越多冷却越长（定时刷新状态=到期自动可再选）。"""
        m.fail += 1
        m.fail_streak += 1
        m.cooldown_until = time.time() + (self.cooldown_ms / 1000) * min(m.fail_streak, MAX_COOLDOWN_MULT)

    def _order(self) -> list[Member]:
        """策略给顺序，冷却过滤在先；全员冷却时半开探测冷却最早到期的。"""
        now = time.time()
        alive = [m for m in self.members if m.healthy(now)]
        if not alive:
            alive = [min(self.members, key=lambda m: m.cooldown_until)]
        return self._strategy(alive, self._state)

    def _forward(self, member: Member, task: dict) -> Any:
        """转发成员：使用端调用（call_agent 自动计时+观测回传给平台）。"""
        return self.client.call_agent(member.agent_id, task["skill_id"],
                                      task.get("payload") or {})

    # ---- 执行：与 Node._handle 同一条链，只是"干活"变成了"转发" ----
    def _handle(self, task: dict) -> None:
        started = time.time()
        last_err: Exception | None = None
        for m in self._order()[: self.max_tries]:
            try:
                t0 = time.time()
                result = self._forward(m, task)
                ms = int((time.time() - t0) * 1000)
                self._reward(m, ms)
                usage = self.client.meter(started, call_count=1,
                                          output_tokens=len(str(result)),
                                          gpu_seconds=round(time.time() - started, 3))
                r = self.client.submit(task["id"], result, usage)
                self.stats["tasks_ok" if r.get("passed") else "tasks_failed"] += 1
                self.stats["calls"] += 1
                self.stats["total_ms"] += ms
                self.stats["ttft"] = ([ms] + self.stats["ttft"])[:RTT_WINDOW]
                self.stats["recent"] = ([{"task_id": task["id"], "skill": task["skill_id"],
                                          "via": m.agent_id, "ms": ms,
                                          "passed": bool(r.get("passed")),
                                          "amount": r.get("amount"),
                                          "at": time.strftime("%H:%M:%S")}]
                                        + self.stats["recent"])[:RTT_WINDOW]
                return
            except Exception as e:  # noqa: BLE001 - 单成员失败绝不拖死任务
                last_err = e
                self._penalize(m)
                print(f"[a2n] 成员 {m.agent_id} 失败，换下一个：{e}")
        self.stats["tasks_failed"] += 1
        print(f"[a2n] 全部成员失败 {task['id']}: {last_err}")
        try:
            self.client.cancel_task(task["id"], reason=f"聚合成员全部失败: {last_err}")
        except Exception:  # noqa: BLE001
            pass

    def _metrics(self) -> dict:
        """心跳自报：平台通用指标 + 成员健康（口径=聚合器实测，标注仍是自报）。"""
        m = super()._metrics()
        m["aggregate"] = {
            "strategy": self.strategy_name,
            "members": [{"agent_id": x.agent_id, "weight": x.weight,
                         "ok": x.ok, "fail": x.fail,
                         "cooling": not x.healthy(),
                         "rtt_avg_ms": int(sum(x.rtt) / len(x.rtt)) if x.rtt else None}
                        for x in self.members],
        }
        return m

    def snapshot(self) -> dict:
        """本地管理台全貌（成员健康附在 local 里）。"""
        s = super().snapshot()
        s["local"]["members"] = [{"agent_id": x.agent_id, "weight": x.weight,
                                  "ok": x.ok, "fail": x.fail,
                                  "fail_streak": x.fail_streak,
                                  "cooling": not x.healthy(),
                                  "rtt_avg_ms": int(sum(x.rtt) / len(x.rtt)) if x.rtt else None}
                                 for x in self.members]
        return s
