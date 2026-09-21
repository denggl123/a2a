"""Local application service: configuration, lifecycle and monitoring.

The HTTP adapter dispatches commands here; persistence, pairing and runtime each
retain their own interfaces. Upstream credentials never become Agent Card fields.
"""
from __future__ import annotations

import threading

from .network import NetworkMonitor


class RuntimeManagement:
    def __init__(self, runtime, store, *, calls=None, publisher=None):
        self.runtime, self.store = runtime, store
        self.calls, self.publisher = calls, publisher
        self.network = NetworkMonitor()
        self._lock = threading.RLock()

    def restore(self) -> None:
        with self._lock:
            for value in self.store.items("accounts").values():
                self.runtime.add_account(**value)
            for value in self.store.items("bindings").values():
                enabled = value.pop("enabled", True)
                item = self.runtime.mount_http(**value)
                item.enabled = enabled
            for value in self.store.items("projections").values():
                self.runtime.import_agent(**value)

    def snapshot(self) -> dict:
        return {**self.runtime.snapshot(), "persistent": True,
                "recent_calls": self.store.recent(), "network": self.network.snapshot(),
                "published": self.publisher.snapshot() if self.publisher else []}

    def command(self, path: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            if path == "/v1/accounts":
                config = {"account_id": str(body.get("account_id") or ""),
                          "label": str(body.get("label") or ""), "kind": str(body.get("kind") or "agent"),
                          "headers": dict(body.get("headers") or {}),
                          "metadata": dict(body.get("metadata") or {})}
                item = self.runtime.add_account(**config)
                self.store.put("accounts", config["account_id"], config)
                return 201, item
            if path == "/v1/bindings/http":
                config = {"source_card": body.get("card") or {}, "endpoint": body.get("endpoint") or "",
                          "protocol": body.get("protocol") or "a2a", "service_id": body.get("service_id"),
                          "account_ref": body.get("account_ref"), "headers": body.get("headers") or {},
                          "source_kind": body.get("source_kind") or "auto"}
                if config["account_ref"]:
                    self.runtime.accounts.headers(config["account_ref"])
                item = self.runtime.mount_http(**config)
                try:
                    card = self.runtime.project_binding(item.service_id)
                    config["service_id"] = item.service_id
                    self.store.put("bindings", item.service_id, config)
                except Exception:
                    self.runtime.bindings.remove(item.service_id)
                    raise
                return 201, {"service_id": item.service_id, "card": card}
            if path == "/v1/projections":
                config = {"network_card": body.get("card") or {}, "target_ref": body.get("target_ref"),
                          "projection_id": body.get("projection_id"), "headers": body.get("headers") or {},
                          "account_ref": body.get("account_ref")}
                item = self.runtime.import_agent(**config)
                config["projection_id"] = item.projection_id
                self.store.put("projections", item.projection_id, config)
                return 201, {"projection_id": item.projection_id, "card": item.local_card,
                              "card_url": item.local_card["url"] + "/.well-known/agent.json"}
            if path == "/v1/bindings/state":
                sid = str(body.get("service_id") or "")
                item = self.runtime.bindings.get(sid)
                if not item:
                    raise ValueError("挂载不存在")
                if not isinstance(body.get("enabled"), bool):
                    raise ValueError("enabled 必须是布尔值")
                config = self.store.get("bindings", sid)
                if config:
                    self.store.put("bindings", sid, {**config, "enabled": body["enabled"]})
                item.enabled = body["enabled"]
                return 200, {"service_id": sid, "enabled": item.enabled}
            if path == "/v1/publish":
                if not self.publisher:
                    raise ValueError("节点未配置平台；启动时可指定 --platform")
                sid = str(body.get("service_id") or "")
                saved = self.store.get("publications", sid) or {}
                handle = self.publisher.publish(sid, agent_id=saved.get("agent_id"))
                self.store.put("publications", handle.service_id,
                               {"service_id": handle.service_id, "agent_id": handle.agent_id})
                return 201, {"agent_id": handle.agent_id, "service_id": handle.service_id}
        # Network probing must not hold the configuration lock.
        if path == "/v1/network/probe":
            pid = str(body.get("projection_id") or "")
            imported = self.runtime.imported.get(pid)
            if not imported:
                raise ValueError("请先导入这位 Agent")
            return 200, self.network.probe(pid, imported.target.route or "")
        raise ValueError("不支持的本机管理操作")
