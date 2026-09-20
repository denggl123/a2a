"""M5 验收：策略可插拔，默认基准策略。

只有"可计量 + 可验证"的维度能进入分账；这里对账节点自报与平台观测。

调用方（a2n-task）把"这次交付"与"该 agent 声明的验收模板"一起放进 `task` 上下文
（键名 `template` / `delivery`）—— 策略据此顺带算出**模板偏差（质量硬指标）**。
模板里的 ``required_fields`` / ``required_content`` 是真正的交付硬条件，缺失会让
``passed=False``；总偏差仍是质量刻度，只有卡明确声明 ``enforce_tolerance`` 才成为
硬条件。硬验收、质量刻度与软评分分开呈现，绝不合成一个总分。
"""
from __future__ import annotations

import json
import time
from typing import Any

from a2n_kernel.policy import PolicyRef

from .template import deviation, template_ref

VARIANCE_TOLERANCE = 0.05  # 双向计量对账阈值 ±5%


class BaselineSamplePolicy:
    key = "acceptance.baseline"
    version = "2026.09.01"

    def judge(self, task: dict, usage: dict | None, result_hash: str | None,
              started_at: float | None) -> dict[str, Any]:
        reasons = []
        if not result_hash:
            return {"passed": False, "score": 0.0, "reasons": ["缺少 result_hash"]}
        if not usage:
            return {"passed": False, "score": 0.0, "reasons": ["缺少计量上报"]}

        observed_ms = int((time.time() - (started_at or time.time())) * 1000)
        reported_ms = int(usage.get("wall_time_ms", 0))
        # 双向计量对账：容忍相对 10% 或绝对 500ms（网络与排队开销），取大者
        tolerance_ms = max(int(reported_ms * 0.10), 500)
        if abs(observed_ms - reported_ms) > tolerance_ms:
            reasons.append(
                f"时长双向计量差异 {abs(observed_ms - reported_ms)}ms 超容差 {tolerance_ms}ms"
                f"（自报 {reported_ms}ms / 平台观测 {observed_ms}ms）"
            )

        sla = json.loads(task.get("sla") or "{}") if isinstance(task.get("sla"), str) else {}
        max_latency = sla.get("max_latency_ms")
        if max_latency and observed_ms > max_latency:
            reasons.append(f"超出 SLA 时延 {max_latency}ms")

        passed = not reasons

        # 模板偏差：**有模板才算**。没声明模板的能力不产生偏差指标，
        # 也绝不许伪造一个 0 偏差（那等于把"没测"说成"满分"）。
        out: dict[str, Any] = {
            "passed": passed,
            "score": 1.0 if passed else 0.4,
            "reasons": reasons,
            "variance": abs(observed_ms - reported_ms),
            "observed_ms": observed_ms,
            "reported_ms": reported_ms,
        }
        template = task.get("template") if isinstance(task, dict) else None
        if template:
            dev = deviation(template, task.get("delivery"))
            out["deviation"] = dev
            out["quality"] = dev["quality"]
            out["template_ref"] = template_ref(template)
            # score 升级为连续值（1 − D）：不改调用方，只让"质量"第一次有刻度
            out["score"] = round(1.0 - dev["D"], 6)
            if not dev.get("hard_passed", True):
                out["passed"] = False
                out["reasons"] = list(out.get("reasons") or []) + list(dev["hard_failures"])
        return out


_DEFAULT = BaselineSamplePolicy()


def set_policy(policy) -> None:
    """替换验收策略（发展点：策略类只要有 .judge(task, usage, hash, started_at) 即可）。

    任务域只调模块级 judge()，不认识具体策略 —— 换策略不改调用方。
    """
    global _DEFAULT
    _DEFAULT = policy


def judge(task: dict, usage: dict | None, result_hash: str | None, started_at: float | None) -> dict:
    return _DEFAULT.judge(task, usage, result_hash, started_at)


def policy_ref() -> PolicyRef:
    return PolicyRef(key=_DEFAULT.key, version=_DEFAULT.version, params={"variance": VARIANCE_TOLERANCE})
