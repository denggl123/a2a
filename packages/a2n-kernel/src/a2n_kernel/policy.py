"""策略注册表与规则版本化。

规则必须版本化：两年后监管问"这笔单为什么这么分"，要能用当时的规则重放。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 纯公益：网络**不抽任何费用** ────────────────────────────────────────────
# 一次调用结算多少，节点就拿到多少。发起人 2026-09-18 拍板：
# **"当成纯公益项目（即便以后有托管商，也不收费，只把不同货币转换为积分，
#   解决不同账户不互通问题）"**。目的：人人都能加入网络、匹配到适合自己的 agent。
#
# 早期默认值 90/2/3/5（网络服务费 3% + 冷启动池 5% + 样本作者池 2%，出自
# 立项期《项目全案设计文档 v1.0》）**已作废**：它不是被调小，是被取消。
# 托管商的角色随之明确为**兑换与清算**（多币种 → 积分，让不同账户互通），
# 不是收费方 —— 见 `docs/VISION.md` §6.2。
#
# 规则仍是**可插拔且版本化**的（这是重放历史单子的前提）：`split_amount` 对任意
# PolicyRef 都能算。但"默认执行不许切给网络"由 `a2n_settlement.settle` 的运行时
# 守卫钉死 —— 想恢复抽成必须显式改代码 + 改纲领，不会悄悄继承一个默认。
DEFAULT_SPLIT = {"node": 1.0}


@dataclass(frozen=True)
class PolicyRef:
    key: str
    version: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"key": self.key, "version": self.version, "params": self.params}


def split_rule(version: str = "2026.09.18") -> PolicyRef:
    """默认分账规则（当前 = 全额归节点）。

    版本号 = 规则生效日：**2026.09.18 起为"纯公益、不抽费用"**。
    旧版 "2026.09.01"（90/2/3/5）仍可显式传进来重放历史单子 —— 这正是
    规则版本化存在的意义，不是把旧规则删掉了事。
    """
    return PolicyRef(key="split.fixed", version=version, params=dict(DEFAULT_SPLIT))


def split_amount(amount: int, ref: PolicyRef | None = None) -> dict[str, int]:
    """按分账规则拆分整数积分，余数补给节点，保证总和恒等于 amount。"""
    ref = ref or split_rule()
    p = ref.params
    parts = {k: int(amount * v) for k, v in p.items() if k != "node"}
    parts["node"] = amount - sum(parts.values())
    return parts
