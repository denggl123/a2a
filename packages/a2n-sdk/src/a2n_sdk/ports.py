"""A2N SDK 的层间契约。

这一层只放数据形状与 ``Protocol``，不认识 HTTP、P2P、平台、数据库或钱。
上层调用编排依赖这些小接口，具体实现由适配器注入：

    工作台 / A2A 适配
        -> CallPipeline（调用业务）
            -> TransportPort（网络层）
            -> AcceptancePort（验收层）
            -> SettlementPort（结算层）

供给侧的真实 Agent 同样藏在 ``UpstreamPort`` 后面。它可以是当前进程里的函数、
127.0.0.1 上的工作流，也可以是带账户凭据的远程 A2A 服务。网络层永远不需要知道。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
import uuid


@dataclass(slots=True)
class CallRequest:
    """与入口协议无关的一次 Agent 调用。"""

    skill: str = ""
    payload: Any = None
    message: dict | None = None
    context_id: str = ""
    task_id: str = field(default_factory=lambda: f"t_{uuid.uuid4().hex}")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CallResponse:
    """网络层或真实 Agent 返回的规范结果。"""

    ok: bool
    result: Any = None
    state: str = "COMPLETED"
    error: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    receipt: dict | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, result: Any, **kwargs: Any) -> "CallResponse":
        return cls(ok=True, result=result, **kwargs)

    @classmethod
    def failure(cls, error: Any, *, state: str = "FAILED", **kwargs: Any) -> "CallResponse":
        return cls(ok=False, error=error, state=state, **kwargs)


@dataclass(slots=True)
class AgentTarget:
    """传输层要拨号的逻辑对象；``ref`` 是身份，``route`` 只是此刻的路。"""

    ref: str
    card: dict[str, Any]
    route: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CallOutcome:
    """调用、验收、结算收口后的单一结论。"""

    ok: bool
    task_id: str
    state: str
    result: Any = None
    error: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    receipt: dict | None = None
    verdict: dict[str, Any] = field(default_factory=dict)
    settlement: dict[str, Any] = field(default_factory=dict)
    target_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "task_id": self.task_id,
            "state": self.state,
            "result": self.result,
            "error": self.error,
            "usage": dict(self.usage),
            "receipt": self.receipt,
            "verdict": dict(self.verdict),
            "settlement": dict(self.settlement),
            "target_ref": self.target_ref,
        }


@runtime_checkable
class TransportPort(Protocol):
    """把规范请求送到另一个节点；不做验收、不做结算。"""

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse: ...


@runtime_checkable
class UpstreamPort(Protocol):
    """供给节点背后的真实 Agent。"""

    def invoke(self, request: CallRequest) -> CallResponse: ...


@runtime_checkable
class AcceptancePort(Protocol):
    """调用方对交付做判断；供给方不得替调用方宣布验收。"""

    def evaluate(self, target: AgentTarget, request: CallRequest,
                 response: CallResponse) -> dict[str, Any]: ...


@runtime_checkable
class SettlementPort(Protocol):
    """验收通过后执行约定的结算方式。"""

    def settle(self, target: AgentTarget, request: CallRequest,
               response: CallResponse, verdict: dict[str, Any]) -> dict[str, Any]: ...
