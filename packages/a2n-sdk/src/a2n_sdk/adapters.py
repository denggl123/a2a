"""SDK 端口的可选适配器。

核心运行时只认 ``ports.py``；这里负责把现有平台客户端或标准 A2A HTTP 翻译成
``TransportPort``。以后加入 P2P/QUIC 只需再实现一个适配器，调用业务无需改动。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import Client
from .ports import AgentTarget, CallRequest, CallResponse, TransportPort
from .upstream import A2AUpstream


@dataclass(frozen=True, slots=True)
class TransportRoute:
    """一条有名字的网络路径；名字只用于观测，不参与业务判定。"""

    name: str
    transport: TransportPort


class FallbackTransport:
    """按顺序尝试网络路径，默认只对“尚未连上”做安全降级。

    业务拒绝、验收失败和超时都不会自动换路重试：它们可能已经在对端产生副作用。
    调用方如果确认自己的协议以 ``task_id`` 幂等，可以显式扩展 ``retry_states``。
    """

    def __init__(self, routes: list[TransportRoute | tuple[str, TransportPort]], *,
                 retry_states: set[str] | None = None) -> None:
        self.routes = [r if isinstance(r, TransportRoute) else TransportRoute(*r)
                       for r in routes]
        if not self.routes:
            raise ValueError("至少需要一条网络路径")
        self.retry_states = {str(s).upper() for s in (retry_states or {"UNREACHABLE"})}

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        attempts: list[dict[str, Any]] = []
        response: CallResponse | None = None
        for route in self.routes:
            try:
                response = route.transport.invoke(target, request)
            except Exception as exc:
                response = CallResponse.failure(
                    f"{type(exc).__name__}: {exc}", state="UNREACHABLE",
                    metadata={"stage": "transport"})
            attempts.append({"route": route.name, "ok": response.ok,
                             "state": response.state})
            response.metadata = {**response.metadata, "transport_route": route.name,
                                 "transport_attempts": list(attempts)}
            if response.ok or str(response.state).upper() not in self.retry_states:
                return response
        assert response is not None
        return response


class DirectA2ATransport:
    """直接调用 Card 的 A2A URL，不依赖 A2N 平台。"""

    def __init__(self, *, timeout: float = 60.0, use_system_proxy: bool = True) -> None:
        self.timeout = timeout
        self.use_system_proxy = use_system_proxy

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        endpoint = target.route or target.card.get("url")
        if not endpoint:
            return CallResponse.failure("Agent Card 没有可调用地址",
                                        state="UNREACHABLE")
        headers = target.metadata.get("headers") or {}
        return A2AUpstream(str(endpoint), headers=headers, timeout=self.timeout,
                           use_system_proxy=self.use_system_proxy).invoke(request)


class PlatformTransport:
    """兼容现有 A2N 平台治理链的网络适配器。"""

    def __init__(self, client: Client) -> None:
        self.client = client

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        try:
            out = self.client.call_agent(
                target.ref,
                skill=request.skill,
                payload=request.payload,
                message=request.message,
                currency=request.metadata.get("currency"),
                settle_points=bool(request.metadata.get("settle_points")),
                payment=request.metadata.get("payment"),
            )
        except Exception as exc:
            return CallResponse.failure(f"{type(exc).__name__}: {exc}",
                                        metadata={"stage": "platform"})
        if not isinstance(out, dict):
            return CallResponse.failure("平台没有返回规范调用结论")
        if not out.get("ok"):
            return CallResponse.failure(out.get("error") or out,
                                        state=str(out.get("state") or "FAILED"),
                                        metadata={"platform": out})
        return CallResponse.success(
            out.get("result"), state=str(out.get("state") or "ACCEPTED"),
            usage=out.get("usage") or {}, receipt=out.get("receipt"),
            metadata={"platform": out, "platform_settlement": out.get("settle") or {}},
        )
