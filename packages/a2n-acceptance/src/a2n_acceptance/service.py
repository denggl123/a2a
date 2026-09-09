"""M5 验收：策略可插拔，默认基准策略。

只有"可计量 + 可验证"的维度能进入分账；这里对账节点自报与平台观测。
"""
from __future__ import annotations

import json
import time
from typing import Any

from a2n_kernel.policy import PolicyRef

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
        return {
            "passed": passed,
            "score": 1.0 if passed else 0.4,
            "reasons": reasons,
            "variance": abs(observed_ms - reported_ms),
            "observed_ms": observed_ms,
            "reported_ms": reported_ms,
        }


_DEFAULT = BaselineSamplePolicy()


def judge(task: dict, usage: dict | None, result_hash: str | None, started_at: float | None) -> dict:
    return _DEFAULT.judge(task, usage, result_hash, started_at)


def policy_ref() -> PolicyRef:
    return PolicyRef(key=_DEFAULT.key, version=_DEFAULT.version, params={"variance": VARIANCE_TOLERANCE})
