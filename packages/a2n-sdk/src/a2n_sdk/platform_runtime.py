"""把多 Agent ``NodeRuntime`` 接到现有平台的兼容桥。

核心运行时不依赖平台。本模块只是适配当前服务端仍以 ``agent_id`` 建隧道的现实：
一个人只有一个 Runtime 和一个本机 A2A 端口，但每张上架卡暂时各持一条平台隧道。
未来 P2P 传输替换本桥时，挂载、投影、调用、验收代码都不需要改变。
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from .client import Client
from .connection import connection_report
from .ports import CallRequest
from .transport import TunnelClient


@dataclass(slots=True)
class PublishedHandle:
    service_id: str
    agent_id: str
    client: Client
    tunnel: TunnelClient
    stop_event: threading.Event
    heartbeat_thread: threading.Thread
    card: dict[str, Any]

    def stop(self) -> None:
        self.stop_event.set()
        self.tunnel.stop()
        self.heartbeat_thread.join(timeout=2)


class RuntimePlatformBridge:
    """一个节点运行时上架多份本地或远程 Agent。"""

    def __init__(self, runtime, *, base_url: str = "http://127.0.0.1:8000",
                 principal: str | None = None, heartbeat_interval: float = 30.0,
                 attest_fn: Callable[[str, str, dict], dict | None] | None = None,
                 calls=None,
                 client_factory: Callable[..., Client] = Client,
                 tunnel_factory: Callable[..., TunnelClient] = TunnelClient) -> None:
        self.runtime = runtime
        self.base_url = base_url.rstrip("/")
        self.principal = principal or runtime.node_did
        self.heartbeat_interval = max(2.0, float(heartbeat_interval))
        self.attest_fn = attest_fn
        self.calls = calls
        self.client_factory = client_factory
        self.tunnel_factory = tunnel_factory
        self.handles: dict[str, PublishedHandle] = {}

    def publish(self, service_id: str, *, visibility: str = "public",
                discover_limit: int | None = None, agent_id: str | None = None) -> PublishedHandle:
        if service_id in self.handles:
            return self.handles[service_id]
        if not self.runtime.gateway:
            self.runtime.start_gateway()
        card = self.runtime.project_binding(service_id)
        client = self.client_factory(self.base_url, principal=self.principal)
        if agent_id:
            client.node_id = agent_id
            client.update_card(agent_id, card)
        else:
            registered = client.register(card, visibility, discover_limit)
            agent_id = registered["agent_id"]
        local_base = f"{self.runtime.local_base_url}/_a2n/upstream/{service_id}"
        tunnel = self.tunnel_factory(
            client, lambda task: self._on_task(service_id, client, task),
            local_base=local_base, attest_fn=self.attest_fn)
        tunnel.start()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(client, card, stop_event), daemon=True,
            name=f"a2n-hb-{service_id[:16]}")
        thread.start()
        handle = PublishedHandle(service_id, agent_id, client, tunnel,
                                 stop_event, thread, card)
        self.handles[service_id] = handle
        return handle

    def _heartbeat_loop(self, client: Client, card: dict,
                        stop_event: threading.Event) -> None:
        delay = 0.1
        while not stop_event.wait(delay):
            try:
                report = connection_report(card.get("url"), mode="relay")
                client.heartbeat(report)
                delay = self.heartbeat_interval
            except Exception:  # 通道自己退避重连；心跳失败不能杀掉整个节点
                delay = min(max(delay * 2, 1.0), 30.0)

    def _on_task(self, service_id: str, client: Client, task: dict) -> None:
        started = time.time()
        request = CallRequest(
            skill=str(task.get("skill_id") or ""), payload=task.get("payload"),
            task_id=str(task.get("id") or CallRequest().task_id),
            metadata={"source": "platform-push"})
        response = (self.calls.invoke(service_id, request) if self.calls else
                    self.runtime.invoke_binding(service_id, request))
        if not response.ok:
            client.fail(request.task_id, str(response.error or response.state)[:180])
            return
        usage = {"call_count": 1,
                 "wall_time_ms": int((time.time() - started) * 1000),
                 **response.usage}
        client.submit(request.task_id, response.result, usage)

    def stop(self, service_id: str | None = None) -> None:
        ids = [service_id] if service_id else list(self.handles)
        for sid in ids:
            handle = self.handles.pop(sid, None)
            if handle:
                handle.stop()

    def snapshot(self) -> list[dict[str, Any]]:
        return [{
            "service_id": h.service_id,
            "agent_id": h.agent_id,
            "connected": h.tunnel.connected.is_set(),
            "last_error": h.tunnel.last_error,
        } for h in self.handles.values()]
