"""M8 信誉：事件驱动聚合，默认加权模型。

信誉是唯一的排序键 —— 因为它是唯一由网络验证过的事实。

职责边界：本包只决定"一个事件折算多少分"（模型），
落库一律走 registry（agents 表的 owner）—— 表结构变了只改一处。
"""
from __future__ import annotations

from a2n_registry import registry


class DefaultWeightedModel:
    key = "reputation.weighted"
    version = "2026.09.01"

    WEIGHTS = {
        "acceptance.passed": 0.02,
        "acceptance.failed": -0.08,
        "settlement.settled": 0.01,
        "arbitration.lost": -0.15,
        "node.suspended": -0.10,
    }

    def delta(self, event: str) -> float:
        """事件 → 分数增减。模型的可替换点就在这里。"""
        return self.WEIGHTS.get(event, 0.0)

    def apply(self, current: float, event: str) -> float:
        """纯函数：给定当前分与事件，给出新分（落库不在这里发生）。"""
        return max(0.0, min(1.0, round(float(current) + self.delta(event), 4)))


_model = DefaultWeightedModel()


def apply_event(agent_id: str, event: str) -> float:
    """事件 → 分数增减 → registry 落库（事务串行化，并发事件不丢）。

    未知事件 delta=0，落库返回的就是当前分（不存在的 agent 返回默认 0.5）。
    """
    return registry.bump_reputation(agent_id, _model.delta(event))
