"""策略注册表与规则版本化。

规则必须版本化：两年后监管问"这笔单为什么这么分"，要能用当时的规则重放。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_SPLIT = {"node": 0.90, "author": 0.02, "fee": 0.03, "pool": 0.05}


@dataclass(frozen=True)
class PolicyRef:
    key: str
    version: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"key": self.key, "version": self.version, "params": self.params}


def split_rule(version: str = "2026.09.01") -> PolicyRef:
    return PolicyRef(key="split.fixed", version=version, params=dict(DEFAULT_SPLIT))


def split_amount(amount: int, ref: PolicyRef | None = None) -> dict[str, int]:
    """按分账规则拆分整数积分，余数补给节点，保证总和恒等于 amount。"""
    ref = ref or split_rule()
    p = ref.params
    parts = {k: int(amount * v) for k, v in p.items() if k != "node"}
    parts["node"] = amount - sum(parts.values())
    return parts
