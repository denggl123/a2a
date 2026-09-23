"""SDK 端口的可选适配器。

核心运行时只认 ``ports.py``；这里负责把现有平台客户端或标准 A2A HTTP 翻译成
``TransportPort``。以后加入 P2P/QUIC 只需再实现一个适配器，调用业务无需改动。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .client import Client
from .ports import AgentTarget, CallRequest, CallResponse, TransportPort
from .upstream import A2AUpstream, network_failure


@dataclass(frozen=True, slots=True)
class TransportRoute:
    """一条有名字的网络路径；名字只用于观测，不参与业务判定。"""

    name: str
    transport: TransportPort
    priority: int = 100
    enabled: bool = True


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
        if len({route.name for route in self.routes}) != len(self.routes):
            raise ValueError("网络路径名称不能重复")
        self.routes.sort(key=lambda route: route.priority)
        self.retry_states = {str(s).upper() for s in (
            {"UNREACHABLE"} if retry_states is None else retry_states)}
        self._history = deque(maxlen=30)
        self._lock = threading.RLock()

    def plan(self, target: AgentTarget, *, probe: bool = False) -> list[dict[str, Any]]:
        """List routes in selection order without executing Agent work."""
        candidates = []
        for route in self.routes:
            item: dict[str, Any] = {
                "name": route.name, "priority": route.priority,
                "enabled": route.enabled, "usable": None,
                "detail": "调用时验证",
            }
            if not route.enabled:
                item.update(usable=False, detail="已禁用")
            elif probe:
                check = getattr(route.transport, "probe", None)
                if callable(check):
                    try:
                        measured = dict(check(target) or {})
                        item.update(measured)
                    except Exception as exc:
                        item.update(usable=False,
                                    detail=f"{type(exc).__name__}: {exc}")
                else:
                    item["detail"] = "此适配器未提供无副作用探测"
            candidates.append(item)
        return candidates

    def snapshot(self, target_ref: str | None = None) -> dict[str, Any]:
        with self._lock:
            history = [dict(item) for item in self._history
                       if target_ref is None or item["target_ref"] == target_ref]
        return {"routes": [{"name": route.name, "priority": route.priority,
                             "enabled": route.enabled} for route in self.routes],
                "history": history}

    def _record(self, target: AgentTarget, route: str,
                attempts: list[dict[str, Any]]) -> None:
        with self._lock:
            self._history.append({
                "target_ref": target.ref, "route": route,
                "attempts": [dict(item) for item in attempts],
                "at": time.time(),
            })

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        attempts: list[dict[str, Any]] = []
        response: CallResponse | None = None
        last_route = ""
        for route in self.routes:
            if not route.enabled:
                attempts.append({"route": route.name, "ok": False,
                                 "state": "DISABLED"})
                continue
            try:
                response = route.transport.invoke(target, request)
            except Exception as exc:
                response = network_failure(exc)
            last_route = route.name
            attempts.append({"route": route.name, "ok": response.ok,
                             "state": response.state})
            response.metadata = {**response.metadata, "transport_route": route.name,
                                 "transport_attempts": list(attempts),
                                 "transport_candidates": self.plan(target)}
            if response.ok or str(response.state).upper() not in self.retry_states:
                self._record(target, route.name, attempts)
                return response
        if response is None:
            return CallResponse.failure(
                "没有启用的网络路径", state="UNREACHABLE",
                metadata={"transport_attempts": attempts,
                          "transport_candidates": self.plan(target)})
        response.metadata = {**response.metadata,
                             "transport_attempts": list(attempts),
                             "transport_candidates": self.plan(target)}
        self._record(target, last_route, attempts)
        return response

    def get_task(self, target: AgentTarget, remote_task_id: str, *,
                 context_id: str = "", route_name: str = "") -> CallResponse:
        return self._control("get_task", target, remote_task_id,
                             context_id=context_id, route_name=route_name)

    def cancel_task(self, target: AgentTarget, remote_task_id: str, *,
                    context_id: str = "", route_name: str = "") -> CallResponse:
        return self._control("cancel_task", target, remote_task_id,
                             context_id=context_id, route_name=route_name)

    def _control(self, action: str, target: AgentTarget, remote_task_id: str, *,
                 context_id: str, route_name: str) -> CallResponse:
        # A remote task belongs to the route that accepted message/send.  Never
        # fall through to another route: identical ids across providers are not
        # the same task and cancellation is a side effect.
        route = next((item for item in self.routes if item.name == route_name), None)
        if not route:
            return CallResponse.failure(
                "缺少创建远端任务的精确网络路径，拒绝跨路径查询或取消",
                state="ROUTE_UNKNOWN",
                metadata={"transport_route": route_name, "remote_effect_unknown": True})
        method = getattr(route.transport, action, None)
        if not callable(method):
            return CallResponse.failure(
                f"网络路径 {route.name} 不支持远端任务控制", state="UNSUPPORTED",
                metadata={"transport_route": route.name, "remote_effect_unknown": True})
        try:
            response = method(target, remote_task_id, context_id=context_id,
                              route_name=route.name)
        except Exception as exc:
            response = network_failure(exc)
        response.metadata = {**response.metadata, "transport_route": route.name}
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

    def _upstream(self, target: AgentTarget) -> A2AUpstream | None:
        endpoint = target.route or target.card.get("url")
        if not endpoint:
            return None
        return A2AUpstream(
            str(endpoint), headers=target.metadata.get("headers") or {},
            timeout=self.timeout, use_system_proxy=self.use_system_proxy)

    def probe(self, target: AgentTarget, *, timeout: float = 1.0) -> dict[str, Any]:
        """Measure TCP setup only; never submit an Agent task."""
        endpoint = target.route or target.card.get("url")
        if not endpoint:
            return {"usable": False, "rtt_ms": None, "detail": "没有直连地址"}
        parsed = urlsplit(str(endpoint))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"usable": False, "rtt_ms": None, "detail": "直连地址不合法"}
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        started = time.monotonic()
        try:
            with socket.create_connection((parsed.hostname, port), timeout=timeout):
                pass
        except OSError as exc:
            return {"usable": False, "rtt_ms": None,
                    "detail": f"TCP 不可达：{type(exc).__name__}"}
        return {"usable": True,
                "rtt_ms": round((time.monotonic() - started) * 1000, 2),
                "detail": "TCP 入口可达（未执行 Agent）"}

    def get_task(self, target: AgentTarget, remote_task_id: str, *,
                 context_id: str = "", route_name: str = "") -> CallResponse:
        upstream = self._upstream(target)
        return (upstream.get_task(remote_task_id, context_id=context_id)
                if upstream else CallResponse.failure(
                    "Agent Card 没有可调用地址", state="UNREACHABLE"))

    def cancel_task(self, target: AgentTarget, remote_task_id: str, *,
                    context_id: str = "", route_name: str = "") -> CallResponse:
        upstream = self._upstream(target)
        return (upstream.cancel_task(remote_task_id, context_id=context_id)
                if upstream else CallResponse.failure(
                    "Agent Card 没有可调用地址", state="UNREACHABLE"))


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
