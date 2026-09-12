"""使用端聚合：把多个候选 agent 聚合成一条稳定调用路径。

定位：这是**使用端**的便利，与供给方无关 ——
- 不注册任何卡、不接平台任务、不产生中间商账本；
- 每一次成功调用都是"使用方 ↔ 真实 agent"的一对一交易：
  结算、刻章、观测全走既有链路（call_agent 自动计时+回传），
  平台刻的全是真实交易，平台对这个聚合器完全无感知；
- 解决的痛点只有一个：单个 agent 不稳定（挂了/超时/抽风），
  客户端按策略路由、失败换人重试、冷却退避 —— client-side load balancing。

候选健康是使用端实测（docs/CALL-PARAMS.md 口径），本地私账：
平台看不到、也不需要看到 —— 它只属于"我怎么调得稳"这个使用方的私事。

策略注册制（与 settle.register_settler 同一模式）：新策略 = register_strategy，
分派主体零改动。内置 round_robin（轮询）与 weighted（平滑加权轮询，
高权重先用 —— 谁稳用谁）。

候选前提：使用方对每个候选**有可用支付能力**即可（免费、或已配对的对等账户、
或已绑定该候选 accepts 里的直付渠道 —— 由 `call_agent` 的服务端门禁统一判定）。
补能力本来就是使用方自己的事，聚合器不代劳（不注册卡、不建关系、不记账）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .client import Client

# 半开探测：全员冷却时宁可试一个冷却最早到期的，也不直接放弃
# 冷却时长 = cooldown_ms/1000 × min(连续失败次数, 5) —— 连挂连加长，到期自动回
MAX_COOLDOWN_MULT = 5
RTT_WINDOW = 20


class Candidate:
    """一个候选 agent：权重 + 使用端实测的健康状态（本地私账）。"""

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


STRATEGIES: dict[str, Callable[[list[Candidate], dict], list[Candidate]]] = {}


def register_strategy(name: str, fn: Callable[[list[Candidate], dict], list[Candidate]]) -> None:
    """注册聚合策略。fn(可用候选, 共享状态) -> 尝试顺序（第一个最优先）。"""
    STRATEGIES[name.lower()] = fn


def _round_robin(candidates: list[Candidate], state: dict) -> list[Candidate]:
    """轮询：游标依次起头，失败顺延。"""
    state["rr"] = state.get("rr", -1) + 1
    n = len(candidates)
    return [candidates[(state["rr"] + i) % n] for i in range(n)]


def _weighted(candidates: list[Candidate], state: dict) -> list[Candidate]:
    """平滑加权轮询（SWRR）：高权重先被选且占比≈权重比，低权重也保证轮到。

    "高权重可以用就先用高权重的"：高权重挂了进冷却自然落到低权重 ——
    稳定性由 failover 兜底，不由权重许诺。
    """
    cur = state.setdefault("swrr", {})
    for c in candidates:
        cur.setdefault(c.agent_id, 0)
    total = sum(c.weight for c in candidates)
    for c in candidates:
        cur[c.agent_id] += c.weight
    pick = max(candidates, key=lambda c: cur[c.agent_id])
    cur[pick.agent_id] -= total
    rest = sorted((c for c in candidates if c is not pick),
                  key=lambda c: -cur[c.agent_id])
    return [pick, *rest]


register_strategy("round_robin", _round_robin)
register_strategy("weighted", _weighted)


class AggregationFailed(RuntimeError):
    """全部候选都调不动。errors: {agent_id: 最后一次错误}。"""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        super().__init__(f"全部候选失败（{len(errors)} 个）：{detail}")


class Aggregator:
    """使用端聚合器：挂在 Client 上，call() 替代单目标 call_agent。

    用法：
        cli = Client(base_url, principal="acct:me")
        agg = Aggregator(cli, candidates=[
            {"agent_id": "ag_big",  "weight": 5},   # 主用：又稳又快
            {"agent_id": "ag_back", "weight": 1}])  # 兜底
        result = agg.call("fin-strategy", {"ticket": "..."})

    成功的那一次调用就是使用方与真实 agent 的交易，聚合器不留任何账。
    """

    def __init__(self, client: Client, candidates: list,
                 strategy: str = "weighted", cooldown_ms: int = 30_000,
                 max_tries: int | None = None) -> None:
        if not candidates:
            raise ValueError("聚合至少要有一个候选 agent")
        self.client = client
        self.candidates = [c if isinstance(c, Candidate) else Candidate(c["agent_id"], c.get("weight", 1))
                           for c in candidates]
        if strategy not in STRATEGIES:
            raise ValueError(f"未知聚合策略：{strategy}（已注册：{sorted(STRATEGIES)}）")
        self.strategy_name = strategy
        self._strategy = STRATEGIES[strategy]
        self._state: dict = {}
        self.cooldown_ms = cooldown_ms
        self.max_tries = max_tries or len(self.candidates)

    # ---- 候选健康：使用端实测，谁测的谁自报（报给自己，本地私账） ----
    def _reward(self, c: Candidate, ms: int) -> None:
        c.ok += 1
        c.fail_streak = 0
        c.rtt = ([ms] + c.rtt)[:RTT_WINDOW]

    def _penalize(self, c: Candidate) -> None:
        """失败进冷却：连续失败越多冷却越长（定时刷新状态=到期自动可再选）。"""
        c.fail += 1
        c.fail_streak += 1
        c.cooldown_until = time.time() + (self.cooldown_ms / 1000) * min(c.fail_streak, MAX_COOLDOWN_MULT)

    def _order(self) -> list[Candidate]:
        """策略给顺序，冷却过滤在先；全员冷却时半开探测冷却最早到期的。"""
        now = time.time()
        alive = [c for c in self.candidates if c.healthy(now)]
        if not alive:
            alive = [min(self.candidates, key=lambda c: c.cooldown_until)]
        return self._strategy(alive, self._state)

    def call(self, skill: str, payload: Any = None, path: str | None = None) -> Any:
        """按策略选候选调用，失败换下一个重试；全员失败抛 AggregationFailed。

        调用走治理链（`call_agent` → `/v1/invoke`），按 **skill** 定位能力；
        `path` 是旧的中继路径口径，仅为兼容保留（缺省时当 skill 用），
        实际不再按路径打节点。成功调用本身自动计时并回传观测（使用端口径）。
        """
        skill = skill or (path or "")
        errors: dict[str, str] = {}
        for c in self._order()[: self.max_tries]:
            try:
                t0 = time.time()
                out = self.client.call_agent(c.agent_id, skill=skill, payload=payload)
                self._reward(c, int((time.time() - t0) * 1000))
                return out
            except Exception as e:  # noqa: BLE001 - 单候选失败绝不拖死调用
                errors[c.agent_id] = f"{type(e).__name__}: {e}"[:160]
                self._penalize(c)
        raise AggregationFailed(errors)

    def health(self) -> list[dict]:
        """候选健康快照（本地私账：使用端实测，平台无此数据也无须有）。"""
        return [{"agent_id": c.agent_id, "weight": c.weight,
                 "ok": c.ok, "fail": c.fail, "fail_streak": c.fail_streak,
                 "cooling": not c.healthy(),
                 "rtt_avg_ms": int(sum(c.rtt) / len(c.rtt)) if c.rtt else None}
                for c in self.candidates]
