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

    def __init__(self, runtime, *, base_url: str = "http://127.0.0.1:18787",
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
        self._lock = threading.RLock()

    def publish(self, service_id: str, *, visibility: str = "public",
                discover_limit: int | None = None, agent_id: str | None = None) -> PublishedHandle:
        # Register + tunnel creation is serialized per bridge so two console
        # clicks cannot create two platform listings for one local service.
        with self._lock:
            if service_id in self.handles:
                return self.handles[service_id]
            if not self.runtime.gateway:
                self.runtime.start_gateway()
            card = self.runtime.project_binding(service_id)
            client = self.client_factory(self.base_url, principal=self.principal)
            if agent_id:
                client.node_id = agent_id
                try:
                    client.update_card(agent_id, card)
                except RuntimeError as exc:
                    if "-> 404:" not in str(exc):
                        raise
                    registered = client.register(card, visibility, discover_limit)
                    agent_id = registered["agent_id"]
                else:
                    # Recovery from an interrupted down-list/publish must restore
                    # the desired visibility as well as the signed card content.
                    client.set_listing(agent_id, visibility=visibility,
                                       discover_limit=discover_limit)
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

    def is_published(self, service_id: str) -> bool:
        with self._lock:
            return service_id in self.handles

    def search(self, skill: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Use the optional platform as one discovery index, never as local state.

        Results contain a standard Agent Card plus non-secret import hints.  The
        caller still persists the chosen Agent in its own runtime.
        """
        client = self.client_factory(self.base_url, principal=self.principal)
        rows = client.discover(str(skill or "").strip(), limit=max(1, min(int(limit), 100)))
        found = []
        for row in rows:
            agent_id = str(row.get("agent_id") or "")
            if not agent_id:
                continue
            try:
                card = client.agent_card(agent_id)
            except Exception:
                continue
            found.append({
                "card": card,
                "source": "platform",
                "headers": {"X-Principal": self.principal},
                "summary": row,
            })
        return found

    def unpublish(self, service_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        """Make a listing private, then stop its local platform tunnel.

        The order is deliberate: a failed platform request leaves the tunnel
        and local state intact, so the UI never reports an Agent as removed
        while it remains publicly discoverable.
        """
        with self._lock:
            handle = self.handles.get(service_id)
            resolved_id = handle.agent_id if handle else agent_id
            if not resolved_id:
                raise KeyError(f"没有找到 {service_id} 的平台发布记录")
            client = handle.client if handle else self.client_factory(
                self.base_url, principal=self.principal)
            client.set_listing(resolved_id, visibility="private")
            if handle:
                handle.stop()
                self.handles.pop(service_id, None)
            return {"service_id": service_id, "agent_id": resolved_id,
                    "visibility": "private"}

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
        with self._lock:
            ids = [service_id] if service_id else list(self.handles)
            handles = [self.handles.pop(sid, None) for sid in ids]
        for handle in handles:
            if handle:
                # Process shutdown is not a user's down-list action.  Preserve
                # the listing so a daemon restart can reconnect it.
                handle.stop()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            handles = list(self.handles.values())
        return [{
            "service_id": h.service_id,
            "agent_id": h.agent_id,
            "connected": h.tunnel.connected.is_set(),
            "last_error": h.tunnel.last_error,
        } for h in handles]
