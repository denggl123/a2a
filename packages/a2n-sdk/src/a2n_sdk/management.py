"""Local application service: configuration, lifecycle and monitoring.

The HTTP adapter dispatches commands here; persistence, pairing and runtime each
retain their own interfaces. Upstream credentials never become Agent Card fields.
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

from .disputes import DisputeBook
from .feedback import FeedbackBook
from .network import NetworkMonitor
from .projection import card_identity
from .trials import TrialBook

MAX_SAVED_SEEDS = 16
# 公开样品面（买方入口）一页上限：公开面是"看履历"，不是发现源，不能被拉爆。
PUBLIC_SAMPLES_MAX = 20


def parse_seed(address: str) -> tuple[str, int]:
    """把 `host:port` 解析成 (host, port)。IPv6 写成 `[::1]:9701`。

    控制台的「连接节点」要一次性接受两种输入（host:port 与 URL），所以这里
    只负责最严格的那一半：只认带端口的地址，绝不猜默认端口 —— 猜错端口会
    变成一个"连不上的种子"，而人只会看到"连了没反应"。
    """
    text = str(address or "").strip()
    if not text:
        raise ValueError("节点地址不能为空")
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError(f"IPv6 地址缺少右方括号：{text}")
        host, rest = text[1:end].strip(), text[end + 1:]
        if not rest.startswith(":"):
            raise ValueError("地址必须带端口：host:port")
        port_text = rest[1:]
    else:
        if ":" not in text:
            raise ValueError("P2P 种子必须写成 host:port（例如 127.0.0.1:9701）")
        host, _, port_text = text.rpartition(":")
        host = host.strip()
    if not host:
        raise ValueError("节点地址缺少主机名")
    try:
        port = int(port_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"端口不是数字：{port_text!r}") from exc
    if not 0 < port <= 65_535:
        raise ValueError("端口必须在 1–65535 之间")
    return host, port


class RuntimeManagement:
    def __init__(self, runtime, store, *, calls=None, publisher=None,
                 discovery=None, discovery_public_base: str | None = None,
                 receipt_auditor=None, witness_service=None,
                 public_directories=None, relay_service=None,
                 relay_provider=None, trials=None, feedback=None, feedback_deliver=None):
        self.runtime, self.store = runtime, store
        self.calls, self.publisher = calls, publisher
        self.discovery = discovery
        self.receipt_auditor = receipt_auditor
        self.witness_service = witness_service
        self.public_directories = public_directories
        self.relay_service = relay_service
        self.relay_provider = relay_provider
        # 「这单我不认」的本机账本：只留痕与撤回，不做仲裁、不自动退钱。
        self.disputes = DisputeBook(store)
        # 试用期 + 样品账本（`docs/VISION.md` §1.2 #12）：前 N 次完成调用免费，
        # 并默认沉淀为公开履历。由 Daemon 传入同一个实例（与交付完成钩子共享）。
        self.trials = trials or TrialBook(store)
        # 双方反馈账本（R2）：一式两份、各存自己的那份；R2 不算分、不改排序。
        # 签名/验签由节点装配注入（Daemon 持有 ed25519 身份），SDK 不依赖密码学库。
        self.feedback = feedback or FeedbackBook(store)
        # 反馈补交（R2）：把本机买方反馈随收据回执再捎一次给供给方。需要网络栈
        # （a2n_node），所以由节点装配注入；SDK 自己不做 I/O 到对端。
        self.feedback_deliver = feedback_deliver
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
                "disputes": self.disputes.list(limit=50),
                "dispute_counts": self.disputes.counts(),
                # 双方反馈（R2）：本机写的 + 收到的，界面一个列表看全。
                "feedback": self.feedback.board(limit=50),
                "feedback_counts": self.feedback.counts(),
                # 试用进度 + 样品：每个供给前 N 次完成调用免费、默认成样品（§1.2 #12）。
                "trials": self.trials.all_for(
                    [b.service_id for b in self.runtime.bindings.list()]),
                "trial_counts": self.trials.counts(),
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
                "connections": {
                    # 种子是 host:port（我主动去连）；目录源是 URL（搜索时去问）。
                    # 两种输入的语义不同，前端不许把它们合成一个"已连接"。
                    "seeds": ([f"{h}:{p}" for h, p in self.discovery.seeds()]
                              if self.discovery
                              else [f"{h}:{p}" for h, p in self._saved_seeds()]),
                    "seeds_live": self.discovery is not None,
                    "public_nodes": (list(self.public_directories.bases)
                                     if self.public_directories else []),
                    "save_limit": MAX_SAVED_SEEDS,
                    "public_node_limit": getattr(self.public_directories, "limit", None),
                },
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

    def public_samples(self, service_id: str, *, limit: int = 10,
                       cursor: str = "", brief: bool = False) -> dict:
        """公开只读样品面（**买方入口**）：调用前就能读到某供给真实交付过的公开样品。

        边界（与 `docs/VISION.md` §6.4 一致）：
        * **只在公益开关打开时对外**（`_require_public_service`）；
        * **只对本节点真挂着的公开供给**发样品 —— 不存在的 service_id 明确报错，不发空壳；
        * **强制分页 + 条数上限 + 摘要优先**：公开面是"看履历"，不是发现源，不能被拉爆；
        * 样品本就是**已脱敏的可公开投影**（`TrialBook` 隐去了密钥/隐私并留 `redactions`）。
        """
        with self._lock:
            self._require_public_service()
            sid = str(service_id or "").strip()
            if not sid:
                raise ValueError("必须给出 service_id")
            binding = self.runtime.bindings.get(sid)
            if binding is None or not binding.enabled:
                raise ValueError("本节点没有这份公开供给")
            status = self.trials.status(sid)
            rows = self.trials.samples(sid, limit=100)  # TrialBook 自带 100 条上限
            start = 0
            if cursor:
                # 游标用样品的 `id`（十六进制，URL 安全）—— 别用 ISO 时间：`+00:00`
                # 里的 `+` 经 URL 解码会变空格，定位必失败。
                idx = next((i for i, r in enumerate(rows)
                            if str(r.get("id")) == str(cursor)), None)
                if idx is not None:
                    start = idx + 1
            size = max(1, min(int(limit), PUBLIC_SAMPLES_MAX))
            page = rows[start:start + size]
            has_more = bool(page) and (start + len(page)) < len(rows)

            def view(s: dict) -> dict:
                out = {"id": s.get("id"), "task_id": s.get("task_id"),
                       "version": s.get("version"), "at": s.get("at"),
                       "summary": s.get("summary"), "redactions": s.get("redactions") or [],
                       "digest": s.get("digest")}
                if not brief:
                    out["preview"] = s.get("preview")
                    out["hidden_reason"] = s.get("hidden_reason") or ""
                return out

            return {"service_id": sid, "kind": "real_delivery_samples_not_promotion",
                    "cap": status["cap"], "completed": status["completed"],
                    "ended": status["ended"], "samples_total": len(rows),
                    "count": len(page), "limit": size,
                    "next_cursor": str(page[-1].get("id")) if has_more else "",
                    "notice": status["notice"],
                    "samples": [view(s) for s in page]}

    def _search_all(self, skill: str, *, timeout: float = 2.0,
                    limit: int = 30) -> tuple[list, list, bool]:
        """把"各发现通道各查一遍再按身份去重"收成一处（`/v1/discovery/search` 与
        投影刷新共用同一套口径，不各写一份）。返回 `(results, errors, configured)`。"""
        configured = False
        results: list = []
        errors: list = []
        if self.discovery:
            configured = True
            try:
                results.extend({"card": card, "source": "p2p", "headers": {}}
                               for card in self.discovery.discover(skill, timeout=timeout))
            except Exception as exc:  # noqa: BLE001 - 一个通道坏不该拖垮整次搜索
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
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": "platform", "error": f"{type(exc).__name__}: {exc}"})
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
                # Plain third-party Cards may not carry A2N projection metadata or
                # even a URL.  Do not collapse every such candidate into one identity.
                key = ("card", "",
                       json.dumps(card, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")))
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique, errors, configured

    def refresh_projection(self, projection_id: str) -> dict:
        """刷新一个买方收藏：按**逻辑商品身份**找回最新卡，原地更新地址。

        旧收藏 404 的根因是卖方 service_id 曾随描述/端口漂移（已修）。这里给买方一条
        明路：拿该商品的稳定身份去各发现通道重找同一商品的**当前**卡，找到就原地更新
        本机投影（投影 id 不变，工作台 Card URL 因此不变）；找不到就**如实说找不到**，
        不假装刷新成功、也不悄悄删掉用户的收藏。
        """
        with self._lock:
            config = self.store.get("projections", projection_id)
            if not config:
                raise ValueError("本机没有这个投影")
            stored = config.get("network_card") or {}
            wanted = card_identity(stored)
            skills = [str(s.get("id")) for s in stored.get("skills") or []
                      if isinstance(s, dict) and s.get("id")]
            if not skills:
                skills = [""]
            fresh = None
            errors: list = []
            configured = False
            for skill in skills[:2]:
                results, errs, configured = self._search_all(skill, timeout=2.0, limit=40)
                errors.extend(errs)
                for item in results:
                    card = item.get("card") or {}
                    if str(card.get("url") or "") and card_identity(card) == wanted:
                        fresh = (card, item.get("headers") or {})
                        break
                if fresh:
                    break
            if fresh is None:
                reason = ("发现通道没有返回同一商品的当前卡片"
                          if configured else "本节点尚未配置任何发现通道")
                return {"projection_id": projection_id, "refreshed": False,
                        "reason": reason, "errors": errors}
            card, headers = fresh
            item = self.runtime.import_agent(
                card, projection_id=projection_id, headers=headers or None,
                account_ref=config.get("account_ref"))
            self.network.watch(item.projection_id, item.target.route)
            self.store.put("projections", item.projection_id, {
                **config, "network_card": card, "target_ref": item.target.ref,
                "projection_id": item.projection_id, "headers": headers})
            return {"projection_id": item.projection_id, "refreshed": True,
                    "unchanged": item.target.ref == config.get("target_ref"),
                    "target_ref": item.target.ref, "card": item.local_card}

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

    # ---------------- 连接节点（种子 / 目录源，落设置且可热改） ----------------

    def _saved_seeds(self) -> list[list]:
        raw = self.store.get("node_settings", "bootstrap_peers", []) or []
        out: list[list] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    out.append([str(item[0]), int(item[1])])
                except (TypeError, ValueError):
                    continue          # 坏值不放大：读不动就跳过，不让整个节点起不来
        return out

    def _saved_public_nodes(self) -> list[str]:
        raw = self.store.get("node_settings", "public_nodes", []) or []
        return [str(x) for x in raw if str(x or "").strip()]

    def saved_seeds(self) -> list[list]:
        return self._saved_seeds()

    def saved_public_nodes(self) -> list[str]:
        return self._saved_public_nodes()

    def connect_node(self, address: str) -> dict:
        """控制台「连接节点」：一次接受两种输入，各走各的语义。

        · `host:port`  → **P2P 种子**：本节点主动去认识它，连上后按直邻交换供给；
        · `http(s)://…` → **目录源**（自愿公共节点）：搜索时向它查目录。

        两条路都会落 `node_settings`，重启后照旧生效。返回值写明走的是哪一条，
        以及"是否新加 / 是否当场生效"——前端不许把"已保存"画成"已连上"。
        """
        text = str(address or "").strip()
        if not text:
            raise ValueError("节点地址不能为空")
        if "://" in text:
            if self.public_directories is None:
                raise ValueError("本节点没有配置公共目录客户端")
            normalized, added = self.public_directories.add(text)
            saved = self._saved_public_nodes()
            if normalized not in saved:
                saved.append(normalized)
                self.store.put("node_settings", "public_nodes", saved)
            return {"kind": "public-node", "address": normalized, "added": added,
                    "applied": True, "persisted": True,
                    "bases": list(self.public_directories.bases),
                    "limit": getattr(self.public_directories, "limit", None),
                    "note": "已作为目录源；搜索时会向它查目录，返回的卡片仍逐张本地验签"}
        host, port = parse_seed(text)
        entry = [host, port]
        saved = self._saved_seeds()
        already_saved = entry in saved
        if not already_saved:
            if len(saved) >= MAX_SAVED_SEEDS:
                raise ValueError(f"最多保存 {MAX_SAVED_SEEDS} 条种子")
            saved.append(entry)
            self.store.put("node_settings", "bootstrap_peers", saved)
        if self.discovery is None:
            return {"kind": "seed", "address": f"{host}:{port}",
                    "added": not already_saved, "applied": False, "persisted": True,
                    "seeds": [f"{h}:{p}" for h, p in self._saved_seeds()],
                    "note": "本节点未启用 P2P 发现；种子已保存，下次以 --p2p-port 启动时生效"}
        added = self.discovery.add_seed(host, port)
        return {"kind": "seed", "address": f"{host}:{port}", "added": added,
                "applied": True, "persisted": True,
                "seeds": [f"{h}:{p}" for h, p in self.discovery.seeds()],
                "note": "已加入冷启动种子并立刻握手；对方此刻不在线也会持续重试"}

    def disconnect_node(self, address: str) -> dict:
        """撤销一条连接。种子只停止重试（已建立的邻居不会被单方面踢下线）；
        目录源则从列表移除，之后不再向它查目录。"""
        text = str(address or "").strip()
        if not text:
            raise ValueError("节点地址不能为空")
        if "://" in text:
            if self.public_directories is None:
                raise ValueError("本节点没有配置公共目录客户端")
            removed = self.public_directories.remove(text)
            normalized = text.rstrip("/")
            saved = [x for x in self._saved_public_nodes() if x.rstrip("/") != normalized]
            self.store.put("node_settings", "public_nodes", saved)
            return {"kind": "public-node", "address": normalized, "removed": removed,
                    "bases": list(self.public_directories.bases)}
        host, port = parse_seed(text)
        removed = (self.discovery.remove_seed(host, port)
                   if self.discovery else False)
        saved = [x for x in self._saved_seeds() if x != [host, port]]
        self.store.put("node_settings", "bootstrap_peers", saved)
        return {"kind": "seed", "address": f"{host}:{port}", "removed": removed,
                "seeds": [f"{h}:{p}" for h, p in (self.discovery.seeds()
                                                  if self.discovery else self._saved_seeds())],
                "note": "已删除种子；已经建立的邻居关系不受影响"}

    def _feedback_open(self, body: dict) -> dict:
        """开一份本方反馈。方向与对手方身份**由本机事实决定，不信客户端自报** ——
        否则等于让评价自己说"评的是谁"，伪造反馈的门就开了。"""
        scope = str(body.get("scope") or "").strip()
        task_id = str(body.get("task_id") or "").strip()
        if not scope or not task_id:
            raise ValueError("必须指明是哪一笔（scope + task_id）")
        target = self._feedback_targets(scope, task_id)
        return self.feedback.open(
            scope=scope, task_id=task_id, direction=target["direction"],
            dimensions=body.get("dimensions"), note=body.get("note"),
            author_did=self.runtime.node_did,
            counterparty_did=target["counterparty_did"],
            provider_did=target["provider_did"],
            service_id=target["service_id"], task_state=target["task_state"])

    def _feedback_targets(self, scope: str, task_id: str) -> dict:
        """按 scope 判方向 + 解析对手方身份与任务状态。

        * scope 是本机**投影 id**（待使用）→ 买方评供给：对手方 = 投影卡上的 node_did；
        * scope 是本机**供给 service_id** → 卖方评买方：对手方 = 该笔**签名调用**里的
          caller_did；普通 A2A（无签名）拿不到对方 did 就如实留空，不猜。
        """
        row = self.store.task(scope, task_id)
        if not row:
            raise ValueError("本机没有这条调用记录，无法对它写反馈")
        state = str(row.get("state") or "").upper()
        projection = self.store.get("projections", scope)
        if not projection and self.runtime is not None:
            # 控制台路径会把投影落库（/v1/projections）；SDK 级 import_agent 只挂内存。
            # 两种都是本机实实在在的一个投影，都应可评 —— 只认落库那份会漏掉后者。
            item = getattr(self.runtime, "imported", {}).get(scope)
            if item is not None:
                projection = {"network_card": getattr(item, "network_card", {})}
        if projection:
            card = projection.get("network_card") or {}
            meta = ((card.get("x-a2n") or {}).get("projection") or {})
            did = str(meta.get("node_did") or "")
            return {"direction": "buyer_to_seller", "counterparty_did": did,
                    "provider_did": did,
                    "service_id": str(meta.get("service_id") or ""),
                    "task_state": state}
        binding = self.runtime.bindings.get(scope) if self.runtime else None
        if binding:
            meta = ((row.get("request") or {}).get("metadata") or {})
            peer = meta.get("_a2n_verified_peer") or {}
            return {"direction": "seller_to_buyer",
                    "counterparty_did": str(peer.get("caller_did") or ""),
                    "provider_did": str(self.runtime.node_did or ""),
                    "service_id": str(getattr(binding, "service_id", scope) or scope),
                    "task_state": state}
        raise ValueError("这条记录既不是本机待使用（买方），也不是本机供给（卖方）")

    def command(self, path: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            if path == "/v1/feedback/open":
                return 201, self._feedback_open(body)
            if path == "/v1/feedback/revise":
                return 200, self.feedback.revise(
                    feedback_id=str(body.get("feedback_id") or ""),
                    dimensions=body.get("dimensions"), note=body.get("note"))
            if path == "/v1/feedback/deliver":
                if not self.feedback_deliver:
                    raise ValueError("本节点未启用反馈补交")
                scope = str(body.get("scope") or "").strip()
                task_id = str(body.get("task_id") or "").strip()
                if not scope or not task_id:
                    raise ValueError("必须指明是哪一笔（scope + task_id）")
                return 200, self.feedback_deliver(scope, task_id)
            if path == "/v1/disputes/open":
                record = self.disputes.open(
                    str(body.get("scope") or ""), str(body.get("task_id") or ""),
                    str(body.get("side") or "requester"), str(body.get("reason") or ""),
                    details=body.get("details") if isinstance(body.get("details"), dict) else None)
                return 201, record
            if path == "/v1/disputes/withdraw":
                record = self.disputes.withdraw(str(body.get("dispute_id") or ""),
                                                str(body.get("note") or ""))
                return 200, record
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
            if path == "/v1/bindings/rebind":
                # 同一逻辑商品**换上游地址/协议**：原地换 transport，service_id 不变。
                # 与 `/v1/bindings/http` 的区别是"这份供给已经存在"，不是新建；因此
                # 不动 publications、不重置 enabled，也不打断按 service_id 累积的
                # 试用/样品/信誉。管理面负责把这次变更说出来（调用方在启动日志里）。
                sid = str(body.get("service_id") or "")
                if not self.runtime.bindings.get(sid):
                    raise ValueError("挂载不存在")
                protocol = body.get("protocol") or "a2a"
                endpoint = str(body.get("endpoint") or "")
                item = self.runtime.rebind_http(
                    sid, endpoint, protocol=protocol,
                    account_ref=body.get("account_ref"),
                    headers=body.get("headers") or {})
                config = dict(self.store.get("bindings", sid) or {})
                config.update({"service_id": sid, "endpoint": endpoint,
                               "protocol": protocol})
                self.store.put("bindings", sid, config)
                self._sync_discovery()
                return 200, {"service_id": sid, "rebound": True,
                             "card": self.runtime.project_binding(sid)}
            if path == "/v1/trials":
                # 只读：某个供给的试用进度 + 样品。不给"删样品"，样品删不掉（§6.4）。
                sid = body.get("service_id")
                if sid:
                    sid = str(sid)
                    return 200, {"service_id": sid,
                                 "status": self.trials.status(sid),
                                 "samples": self.trials.samples(sid)}
                return 200, {
                    "counts": self.trials.counts(),
                    "trials": self.trials.all_for(
                        [b.service_id for b in self.runtime.bindings.list()]),
                }
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
            if path == "/v1/projections/refresh":
                # 买方刷新收藏：按逻辑商品身份找回最新卡，原地更新地址（R0-4）。
                return 200, self.refresh_projection(str(body.get("projection_id") or ""))
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
        if path == "/v1/peers/connect":
            return 200, self.connect_node(str(body.get("address") or ""))
        if path == "/v1/peers/disconnect":
            return 200, self.disconnect_node(str(body.get("address") or ""))
        if path == "/v1/discovery/search":
            skill = str(body.get("skill") or "").strip()
            if not skill:
                raise ValueError("发现技能不能为空")
            timeout = min(max(float(body.get("timeout") or 2.0), 0.1), 10.0)
            limit = min(max(int(body.get("limit") or 30), 1), 100)
            results, errors, configured = self._search_all(skill, timeout=timeout, limit=limit)
            if not configured:
                raise ValueError("节点尚未配置任何发现通道；仍可直接粘贴 Agent Card")
            return 200, {"skill": skill,
                         # ``cards`` keeps the original local API compatible.
                         "cards": [item["card"] for item in results],
                         "results": results, "count": len(results),
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
