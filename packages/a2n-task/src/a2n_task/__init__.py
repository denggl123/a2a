"""a2n-task（L3 业务）：任务全链路编排。

CREATED → ASSIGNED → SUBMITTED → ACCEPTED → SETTLED
        └── CANCELED / FAILED        └── REJECTED
预算冻结 → 执行 → 计量上报 → 按实结算 → 差额退回 → 分账。

对外视角遵循 A2A v1.0：见 `a2a`（state 映射与 Task 对象渲染）。
"""
from .a2a import A2A_STATES, STATE_TO_A2A, TRANSITIONS, a2a_view, can_transition, to_a2a
from .service import STATE_FLOW, Tasks, tasks

__all__ = ["Tasks", "tasks", "STATE_FLOW", "a2a_view", "can_transition", "to_a2a",
           "STATE_TO_A2A", "TRANSITIONS", "A2A_STATES"]
