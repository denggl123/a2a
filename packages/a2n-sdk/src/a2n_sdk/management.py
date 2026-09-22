"""Local application service: configuration, lifecycle and monitoring.

The HTTP adapter dispatches commands here; persistence, pairing and runtime each
retain their own interfaces. Upstream credentials never become Agent Card fields.
"""
from __future__ import annotations

import threading
from urllib.parse import urlsplit

from .network import NetworkMonitor


class RuntimeManagement:
    def __init__(self, runtime, store, *, calls=None, publisher=None,
                 discovery=None, discovery_public_base: str | None = None):
        self.runtime, self.store = runtime, store
        self.calls, self.publisher = calls, publisher
        self.discovery = discovery
        self.discovery_public_base = (discovery_public_base or "").rstrip("/")
        if self.discovery_public_base:
            parsed = urlsplit(self.discovery_public_base)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("P2P 对外 HTTP 入口端口不合法") from exc
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment):
                raise ValueError("P2P 对外 HTTP 入口必须是无账号、查询参数和片段的 http(s) URL")
        self._discovery_error = ""
        self.network = NetworkMonitor()
        self._lock = threading.RLock()
        # Prevent the daemon's asynchronous restore loop from re-publishing a
        # service using a stale pre-unpublish snapshot.  An explicit UI action
        # can clear this in /v1/publish with force=true.
        self._publication_tombstones: set[str] = set()

    def restore(self) -> None:
        with self._lock:
            pending_binding_removals = self.store.items("binding_removals")
            for sid in list(pending_binding_removals):
                if not self.store.get("bindings", sid):
                    self.store.delete("binding_removals", sid)
            for value in self.store.items("accounts").values():
                self.runtime.add_account(**value)
            for value in self.store.items("bindings").values():
                config = dict(value)
                enabled = config.pop("enabled", True)
                item = self.runtime.mount_http(**config)
                item.enabled = enabled
            for value in self.store.items("projections").values():
                item = self.runtime.import_agent(**value)
                if item.target.route:
                    self.network.watch(item.projection_id, item.target.route)
            self._sync_discovery()

    def _sync_discovery(self) -> None:
        """Publish a fresh P2P index only when a reachable base was declared."""
        if not self.discovery:
            return
        try:
            cards = []
            if self.discovery_public_base:
                cards = [self.runtime.project_binding(
                    item.service_id, public_base=self.discovery_public_base)
                    for item in self.runtime.bindings.list() if item.enabled]
            self.discovery.advertise(cards)
            self._discovery_error = ""
        except Exception as exc:
            # Local configuration remains authoritative and usable even when a
            # UDP socket/network adapter is temporarily unavailable.
            self._discovery_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict:
        discovery = self.discovery.snapshot() if self.discovery else None
        if discovery is not None:
            discovery = {**discovery, "advertise_enabled": bool(self.discovery_public_base),
                         "last_error": self._discovery_error or None}
        live = {item["service_id"]: dict(item)
                for item in (self.publisher.snapshot() if self.publisher else [])}
        for sid, saved in self.store.items("publications").items():
            live.setdefault(sid, {
                "service_id": sid, "agent_id": saved.get("agent_id"),
                "connected": False, "last_error": None,
            })["state"] = saved.get("state") or "published"
        for item in live.values():
            item.setdefault("state", "published")
        return {**self.runtime.snapshot(), "persistent": True,
                "recent_calls": self.store.recent(), "network": self.network.snapshot(),
                "published": list(live.values()),
                "discovery": discovery}

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
            if path == "/v1/accounts/remove":
                account_id = str(body.get("account_id") or "")
                if not account_id:
                    raise ValueError("account_id 不能为空")
                # Validate existence and references before making persistence
                # authoritative.  The runtime performs the same reference
                # check, keeping non-HTTP callers safe as well.
                self.runtime.accounts.public(account_id)
                references = self.runtime.account_references(account_id)
                if references:
                    names = "、".join(ref["id"] for ref in references)
                    raise ValueError(f"账户仍被 Agent 使用：{names}；请先移除这些资源")
                self.store.delete("accounts", account_id)
                removed = self.runtime.remove_account(account_id)
                return 200, {"removed": True, "account": removed}
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
                self._sync_discovery()
                return 201, {"service_id": item.service_id, "card": card}
            if path == "/v1/projections":
                config = {"network_card": body.get("card") or {}, "target_ref": body.get("target_ref"),
                          "projection_id": body.get("projection_id"), "headers": body.get("headers") or {},
                          "account_ref": body.get("account_ref")}
                item = self.runtime.import_agent(**config)
                try:
                    if item.target.route:
                        self.network.watch(item.projection_id, item.target.route)
                    config["projection_id"] = item.projection_id
                    self.store.put("projections", item.projection_id, config)
                except Exception:
                    self.network.unwatch(item.projection_id)
                    self.runtime.remove_projection(item.projection_id)
                    raise
                return 201, {"projection_id": item.projection_id, "card": item.local_card,
                              "card_url": item.local_card["url"] + "/.well-known/agent.json"}
            if path == "/v1/projections/remove":
                pid = str(body.get("projection_id") or "")
                if not self.runtime.imported.get(pid):
                    raise ValueError("投影不存在")
                self.store.delete("projections", pid)
                item = self.runtime.remove_projection(pid)
                self.network.unwatch(pid)
                return 200, {"removed": True, "projection_id": item.projection_id}
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
                self._sync_discovery()
                return 200, {"service_id": sid, "enabled": item.enabled}
            if path == "/v1/bindings/remove":
                sid = str(body.get("service_id") or "")
                if not self.runtime.bindings.get(sid):
                    raise ValueError("挂载不存在")
                saved = self.store.get("publications", sid)
                active = bool(self.publisher and self.publisher.is_published(sid))
                if saved or active:
                    if body.get("unpublish") is not True:
                        raise ValueError("这份供给仍在平台上架；请先下架，或确认同时下架并卸载")
                    if not self.publisher:
                        raise ValueError("当前未连接原发布平台，无法确认下架")
                # Persist only after every user-facing precondition passed.  A
                # rejected "remove" click must never become a delayed delete.
                self.store.put("binding_removals", sid, {"service_id": sid})
                if saved or active:
                    self.store.put("publications", sid, {
                        "service_id": sid,
                        "agent_id": (saved or {}).get("agent_id"),
                        "state": "unpublishing",
                        "remove_binding": True,
                    })
                    self.publisher.unpublish(sid, agent_id=(saved or {}).get("agent_id"))
                    self.store.delete("publications", sid)
                    self._publication_tombstones.add(sid)
                self.store.delete("bindings", sid)
                self.runtime.unmount_binding(sid)
                self.store.delete("binding_removals", sid)
                self._sync_discovery()
                return 200, {"removed": True, "service_id": sid,
                             "unpublished": bool(saved or active)}
            if path == "/v1/publish":
                if not self.publisher:
                    raise ValueError("节点未配置平台；启动时可指定 --platform")
                sid = str(body.get("service_id") or "")
                if not self.runtime.bindings.get(sid):
                    raise ValueError("挂载不存在")
                if sid in self._publication_tombstones and body.get("force") is not True:
                    raise ValueError("这份供给刚刚下架；如需重新上架，请由控制台再次确认")
                if body.get("force") is True:
                    self._publication_tombstones.discard(sid)
                saved = self.store.get("publications", sid) or {}
                if saved.get("state") == "unpublishing" and body.get("force") is not True:
                    raise ValueError("这份供给正在下架；如需重新上架，请由控制台再次确认")
                projected = self.runtime.project_binding(sid)
                expected_id = ((projected.get("x-a2n") or {}).get("node_id") or None)
                agent_id = saved.get("agent_id") or expected_id
                # Desired state is durable *before* the remote side effect.  A
                # crash can therefore retry the same deterministic platform id
                # instead of leaving an orphan public listing.
                self.store.put("publications", sid, {
                    "service_id": sid, "agent_id": agent_id,
                    "state": "publishing",
                })
                handle = self.publisher.publish(sid, agent_id=agent_id)
                self.store.put("publications", handle.service_id,
                               {"service_id": handle.service_id, "agent_id": handle.agent_id,
                                "state": "published"})
                return 201, {"agent_id": handle.agent_id, "service_id": handle.service_id}
            if path == "/v1/unpublish":
                if not self.publisher:
                    raise ValueError("节点未配置平台，无法确认下架")
                sid = str(body.get("service_id") or "")
                saved = self.store.get("publications", sid)
                active = self.publisher.is_published(sid)
                if not saved and not active:
                    raise ValueError("这份供给尚未上架")
                self.store.put("publications", sid, {
                    "service_id": sid,
                    "agent_id": (saved or {}).get("agent_id"),
                    "state": "unpublishing",
                    "remove_binding": bool((saved or {}).get("remove_binding")),
                })
                result = self.publisher.unpublish(
                    sid, agent_id=(saved or {}).get("agent_id"))
                self.store.delete("publications", sid)
                self._publication_tombstones.add(sid)
                return 200, result
        # Network operations must not hold the configuration lock.
        if path == "/v1/discovery/search":
            if not self.discovery:
                raise ValueError("节点未启用 P2P 发现")
            skill = str(body.get("skill") or "").strip()
            timeout = min(max(float(body.get("timeout") or 2.0), 0.1), 10.0)
            cards = self.discovery.discover(skill, timeout=timeout)
            return 200, {"skill": skill, "cards": cards,
                         "count": len(cards)}
        if path == "/v1/network/probe":
            pid = str(body.get("projection_id") or "")
            imported = self.runtime.imported.get(pid)
            if not imported:
                raise ValueError("请先导入这位 Agent")
            return 200, self.network.probe(pid, imported.target.route or "")
        raise ValueError("不支持的本机管理操作")
