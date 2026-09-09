"""M8 信誉：事件驱动聚合，默认加权模型。

信誉是唯一的排序键 —— 因为它是唯一由网络验证过的事实。
"""
from __future__ import annotations

from a2n_store import conn


class DefaultWeightedModel:
    key = "reputation.weighted"
    version = "2026.09.01"

    def apply(self, current: float, event: str) -> float:
        delta = {
            "acceptance.passed": 0.02,
            "acceptance.failed": -0.08,
            "settlement.settled": 0.01,
            "arbitration.lost": -0.15,
            "node.suspended": -0.10,
        }.get(event, 0.0)
        return max(0.0, min(1.0, round(current + delta, 4)))


_model = DefaultWeightedModel()


def apply_event(agent_id: str, event: str) -> float:
    r = conn().execute("SELECT reputation FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not r:
        return 0.5
    new_score = _model.apply(r["reputation"], event)
    conn().execute("UPDATE agents SET reputation=? WHERE agent_id=?", (new_score, agent_id))
    conn().commit()
    return new_score
