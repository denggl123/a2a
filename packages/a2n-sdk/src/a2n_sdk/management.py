"""Local application service: configuration, lifecycle and monitoring.

The HTTP adapter dispatches commands here; persistence, pairing and runtime each
retain their own interfaces. Upstream credentials never become Agent Card fields.
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

from .network import NetworkMonitor


class RuntimeManagement:
    def __init__(self, runtime, store, *, calls=None, publisher=None,
                 discovery=None, discovery_public_base: str | None = None,
                 receipt_auditor=None, witness_service=None,
                 public_directories=None, relay_service=None,
                 relay_provider=None):
        self.runtime, self.store = runtime, store
        self.calls, self.publisher = calls, publisher
        self.discovery = discovery
        self.receipt_auditor = receipt_auditor
        self.witness_service = witness_service
        self.public_directories = public_directories
        self.relay_service = relay_service
        self.relay_provider = relay_provider
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
        self.public_service_enabled = self.store.get(
            "node_settings", "public_service_enabled", False) is True
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
        if self.relay_provider:
            self.relay_provider.request_refresh()
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
        settlements = self.store.recent_settlements()
        if self.receipt_auditor:
            for row in settlements:
                if row["has_receipt"]:
                    try:
                        row["evidence_type"] = self.receipt_auditor(
                            row["scope"], row["task_id"])
                    except Exception:
                        row["evidence_type"] = "receipt_unverified"
        return {**self.runtime.snapshot(), "persistent": True,
                "recent_calls": self.store.recent(), "network": self.network.snapshot(),
                "published": list(live.values()),
                "settlements": settlements,
                "public_service": {
                    "enabled": self.public_service_enabled,
                    "directory_available": bool(self.discovery_public_base),
                    "directory_url": (self.discovery_public_base + "/public/v1/agents"
                                      if self.discovery_public_base else None),
                    "route_hints_available": bool(self.discovery and self.discovery_public_base),
                    "witness_available": bool(self.witness_service and self.discovery_public_base),
                    "witness_count": (self.store.count("public_witnesses")
                                      if self.witness_service else 0),
                    "relay_available": bool(self.relay_service and self.discovery_public_base),
                    "relay_provider": ({"node": self.relay_provider.relay_node,
                                        "registered": self.relay_provider.registered,
                                        "last_error": self.relay_provider.last_error}
                                       if self.relay_provider else None),
                },
                "discovery": discovery,
                "channels": {
                    "p2p": {"configured": self.discovery is not None,
                            "advertise": bool(self.discovery_public_base)},
                    "platform": {"configured": self.publisher is not None},
                    "public_nodes": {"configured": bool(self.public_directories
                                                       and self.public_directories.bases),
                                     "count": len(self.public_directories.bases)
                                     if self.public_directories else 0},
                }}

    def public_directory(self, skill: str, *, limit: int = 30) -> dict:
        """Serve only signed public supply facts; never accounts or imported cards."""
        with self._lock:
            if not self.public_service_enabled:
                raise PermissionError("本节点没有开放公共目录")
            if not self.discovery_public_base:
                raise ValueError("本节点尚未配置可回连的公共 HTTP 入口")
            if self.discovery:
                cards = self.discovery.directory_cards(skill, limit=limit)
            else:
                wanted = str(skill or "").strip()
                if not wanted or len(wanted) > 96:
                    raise ValueError("能力标识必须为 1–96 个字符")
                count = max(1, min(int(limit), 50))
                cards = [self.runtime.project_binding(
                    item.service_id, public_base=self.discovery_public_base)
                    for item in self.runtime.bindings.list()
                    if item.enabled and wanted in {
                        str(entry.get("id") or entry.get("name") or "")
                        for entry in item.source_card.get("skills") or []
                        if isinstance(entry, dict)}][:count]
            bounded, used_bytes = [], 0
            if self.relay_service:
                cards.extend(self.relay_service.directory_cards(
                    str(skill or "").strip(), limit=max(1, min(int(limit), 50))))
            seen = set()
            for card in cards:
                key = str(card.get("url") or "")
                if key in seen or len(bounded) >= max(1, min(int(limit), 50)):
                    continue
                size = len(json.dumps(card, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8"))
                if used_bytes + size > 2_000_000:
                    continue
                bounded.append(card)
                seen.add(key)
                used_bytes += size
            return {"skill": skill, "cards": bounded, "count": len(bounded),
                    "node_did": self.runtime.node_did}

    def _require_public_service(self) -> None:
        if not self.public_service_enabled:
            raise PermissionError("本节点没有开放公共服务")
        if not self.discovery_public_base:
            raise ValueError("本节点尚未配置可回连的公共 HTTP 入口")

    def public_routes(self, did: str) -> dict:
        with self._lock:
            self._require_public_service()
            if not self.discovery:
                raise ValueError("本节点未启用 P2P 路由观察")
            hints = self.discovery.route_hints(did)
            return {"did": did, "routes": hints, "count": len(hints),
                    "observer_did": self.runtime.node_did,
                    "kind": "observed-hints-not-relay"}

    def public_witness(self, claim: dict) -> dict:
        with self._lock:
            self._require_public_service()
            if not self.witness_service:
                raise ValueError("本节点未启用公共见证")
            return self.witness_service.witness(claim)

    def public_witness_get(self, receipt_hash: str) -> dict | None:
        with self._lock:
            self._require_public_service()
            return (self.witness_service.get(receipt_hash)
                    if self.witness_service else None)

    def command(self, path: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            if path == "/v1/public-service":
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled 必须是布尔值")
                self.store.put("node_settings", "public_service_enabled", enabled)
                self.public_service_enabled = enabled
                return 200, {"enabled": enabled,
                             "directory_available": bool(self.discovery_public_base)}
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
                enabled = body["enabled"]
                publication = self.store.get("publications", sid)
                active = bool(self.publisher and self.publisher.is_published(sid))
                platform_state = None
                if not enabled and (publication or active):
                    if not self.publisher:
                        raise ValueError("当前未连接原发布平台，不能保证暂停后平台立即隐藏")
                    config = self.store.get("bindings", sid)
                    if config:
                        self.store.put("bindings", sid, {**config, "enabled": False})
                    item.enabled = False
                    self._sync_discovery()
                    agent_id = (publication or {}).get("agent_id")
                    if not agent_id and active:
                        live = next((value for value in self.publisher.snapshot()
                                     if value.get("service_id") == sid), {})
                        agent_id = live.get("agent_id")
                    self.store.put("publications", sid, {
                        "service_id": sid, "agent_id": agent_id,
                        "state": "pausing", "resume_on_enable": True})
                    self.publisher.unpublish(sid, agent_id=agent_id)
                    self.store.put("publications", sid, {
                        "service_id": sid, "agent_id": agent_id,
                        "state": "paused", "resume_on_enable": True})
                    platform_state = "paused"
                elif enabled and publication and publication.get("state") in {
                        "paused", "pausing"}:
                    if not self.publisher:
                        raise ValueError("当前未连接原发布平台，不能恢复已暂停的上架供给")
                    config = self.store.get("bindings", sid)
                    if config:
                        self.store.put("bindings", sid, {**config, "enabled": True})
                    item.enabled = True
                    self._sync_discovery()
                    agent_id = publication.get("agent_id")
                    self.store.put("publications", sid, {
                        "service_id": sid, "agent_id": agent_id,
                        "state": "publishing"})
                    handle = self.publisher.publish(sid, agent_id=agent_id)
                    self.store.put("publications", sid, {
                        "service_id": sid, "agent_id": handle.agent_id,
                        "state": "published"})
                    platform_state = "published"
                config = self.store.get("bindings", sid)
                if config:
                    self.store.put("bindings", sid, {**config, "enabled": enabled})
                item.enabled = enabled
                self._sync_discovery()
                return 200, {"service_id": sid, "enabled": item.enabled,
                             "platform_state": platform_state}
            if path == "/v1/bindings/remove":
                sid = str(body.get("service_id") or "")
                binding = self.runtime.bindings.get(sid)
                if not binding:
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
                binding = self.runtime.bindings.get(sid)
                if not binding:
                    raise ValueError("挂载不存在")
                if not binding.enabled:
                    raise ValueError("这份供给已暂停；请先恢复接单再上架")
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
            skill = str(body.get("skill") or "").strip()
            if not skill:
                raise ValueError("发现技能不能为空")
            timeout = min(max(float(body.get("timeout") or 2.0), 0.1), 10.0)
            limit = min(max(int(body.get("limit") or 30), 1), 100)
            configured = False
            results = []
            errors = []
            if self.discovery:
                configured = True
                try:
                    results.extend({"card": card, "source": "p2p", "headers": {}}
                                   for card in self.discovery.discover(skill, timeout=timeout))
                except Exception as exc:
                    errors.append({"source": "p2p", "error": f"{type(exc).__name__}: {exc}"})
            if self.public_directories and self.public_directories.bases:
                configured = True
                found, failures = self.public_directories.search(skill, limit=limit)
                results.extend(found)
                errors.extend(failures)
            platform_search = getattr(self.publisher, "search", None)
            if callable(platform_search):
                configured = True
                try:
                    results.extend(platform_search(skill, limit=limit))
                except Exception as exc:
                    errors.append({"source": "platform", "error": f"{type(exc).__name__}: {exc}"})
            if not configured:
                raise ValueError("节点尚未配置任何发现通道；仍可直接粘贴 Agent Card")
            unique = []
            seen = set()
            for item in results:
                card = item.get("card") or {}
                ext = card.get("x-a2n") or {}
                projection = ext.get("projection") or {}
                key = (str(projection.get("node_did") or ""),
                       str(projection.get("service_id") or ""),
                       str(card.get("url") or ""))
                if not any(key):
                    # Plain third-party Cards may not carry A2N projection
                    # metadata or even a URL.  Do not collapse every such
                    # candidate into a single empty identity.
                    key = ("card", "",
                           json.dumps(card, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
                if len(unique) >= limit:
                    break
            return 200, {"skill": skill,
                         # ``cards`` keeps the original local API compatible.
                         "cards": [item["card"] for item in unique],
                         "results": unique, "count": len(unique),
                         "errors": errors}
        if path == "/v1/discovery/probe":
            if not self.discovery:
                raise ValueError("节点未启用 P2P 发现")
            return 200, self.discovery.probe(str(body.get("did") or ""))
        if path == "/v1/network/probe":
            pid = str(body.get("projection_id") or "")
            imported = self.runtime.imported.get(pid)
            if not imported:
                raise ValueError("请先导入这位 Agent")
            return 200, self.network.probe(pid, imported.target.route or "")
        raise ValueError("不支持的本机管理操作")
