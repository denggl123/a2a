"""A2A v1.0 任务状态映射。

A2N 的任务状态比 A2A 多，因为 A2N 在"agent 交付"之后还有一段 A2A 不关心、
但结算必须有的过程：**验收与分账**。

    A2A:  submitted → working → completed / failed / canceled / rejected / input-required
    A2N:  CREATED → ASSIGNED → SUBMITTED → ACCEPTED → SETTLED
                                    └── REJECTED / FAILED / CANCELED

映射原则：
  1. **对外说 A2A 的话** —— 使用方拿到的是标准 A2A Task 对象，无需了解 A2N；
  2. **内部状态不丢** —— 全部塞进 `metadata["x-a2n"]`，A2A 视角看不见，
     A2N 视角可完整追溯；
  3. **验收失败对外是 failed 不是 rejected** —— A2A 的 rejected 指
     "agent 拒绝接单"，而 A2N 的 REJECTED 是"活干了但没通过验收"，
     语义更接近 failed。混用会让调用方误以为节点耍赖。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_kernel.hashing import now_iso

# A2A v1.0 TaskState
A2A_SUBMITTED = "submitted"
A2A_WORKING = "working"
A2A_COMPLETED = "completed"
A2A_FAILED = "failed"
A2A_CANCELED = "canceled"
A2A_REJECTED = "rejected"
A2A_INPUT_REQUIRED = "input-required"
A2A_AUTH_REQUIRED = "auth-required"
A2A_UNKNOWN = "unknown"

A2A_STATES = {A2A_SUBMITTED, A2A_WORKING, A2A_COMPLETED, A2A_FAILED, A2A_CANCELED,
              A2A_REJECTED, A2A_INPUT_REQUIRED, A2A_AUTH_REQUIRED, A2A_UNKNOWN}

STATE_TO_A2A = {
    "CREATED": A2A_SUBMITTED,
    "ASSIGNED": A2A_WORKING,
    "SUBMITTED": A2A_COMPLETED,      # 产物已交付；验收是 A2N 内部后续
    "ACCEPTED": A2A_COMPLETED,
    "SETTLED": A2A_COMPLETED,
    "REJECTED": A2A_FAILED,          # 验收不通过 → 失败，见上方第 3 条
    "FAILED": A2A_FAILED,
    "CANCELED": A2A_CANCELED,
    "INPUT_REQUIRED": A2A_INPUT_REQUIRED,
}

# 允许的迁移。显式列出而非"随便改" —— 状态机越自由，对账越难。
TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"ASSIGNED", "CANCELED", "INPUT_REQUIRED"},
    "ASSIGNED": {"SUBMITTED", "FAILED", "CANCELED", "INPUT_REQUIRED"},
    "INPUT_REQUIRED": {"ASSIGNED", "CANCELED"},
    "SUBMITTED": {"ACCEPTED", "REJECTED"},
    "ACCEPTED": {"SETTLED"},
    "SETTLED": set(),
    "REJECTED": set(),
    "FAILED": set(),
    "CANCELED": set(),
}

TERMINAL = {s for s, nxt in TRANSITIONS.items() if not nxt}


def can_transition(cur: str, nxt: str) -> bool:
    return nxt in TRANSITIONS.get(cur, set())


def to_a2a(state: str) -> str:
    return STATE_TO_A2A.get(state, A2A_UNKNOWN)


def is_terminal(state: str) -> bool:
    return state in TERMINAL


def a2a_view(task: dict[str, Any]) -> dict[str, Any]:
    """把 A2N 任务渲染成 A2A v1.0 的 Task 对象。

    调用方（另一个 agent）看到的是标准 A2A 结构；A2N 特有的一切
    都在 metadata 里，不污染标准字段。
    """
    state = task.get("state") or "CREATED"
    ts = task.get("updated_at") or task.get("created_at") or now_iso()
    out: dict[str, Any] = {
        "id": task.get("id"),
        "skill": task.get("skill_id"),
        "status": {"state": to_a2a(state), "timestamp": ts},
        "metadata": {
            "x-a2n": {
                "state": state,
                "node_id": task.get("node_id"),
                "amount_points": task.get("amount") or 0,
                "budget": task.get("budget"),
                "result_hash": task.get("result_hash"),
                "card_hash": task.get("card_hash"),
            }
        },
    }
    if state in ("SUBMITTED", "ACCEPTED", "SETTLED"):
        out["artifacts"] = [_artifact(task)]
    reason = task.get("reject_reason") or task.get("fail_reason")
    if reason:
        out["status"]["message"] = {"role": "agent", "parts": [{"type": "text", "text": str(reason)}]}
    return out


def _artifact(task: dict[str, Any]) -> dict[str, Any]:
    """结果封装为 A2A Artifact。结果可能是 dict 也可能是字符串（都合法）。"""
    raw = task.get("result")
    data: Any
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = raw
    else:
        data = raw
    part = {"type": "text", "text": data} if isinstance(data, str) else {"type": "data", "data": data}
    return {
        "artifactId": f"{task.get('id')}-result",
        "name": "result",
        "parts": [part],
        "metadata": {"result_sha256": task.get("result_hash")},
    }
