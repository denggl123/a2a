"""Persistent discovery application service. Network and identity are injected.

Owns the frontier, budgets and candidates; never executes an Agent or settles a
trade. One worker per search makes reservations and pause/restart deterministic.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from typing import Protocol

from .coordination import (Budget, BudgetLedger, CandidateKey, CoordinationError,
                           ERROR_HTTP, RESUMABLE_STATES, RoutePlan, SearchSnapshot, SearchSpec)
from .projection import canonical_json


class DiscoverySourcePort(Protocol):
    """Verified, bounded pages, implemented by the node's network adapter."""
    def roots(self, skill: str) -> list[dict]: ...
    def cost(self, action: dict) -> int: ...
    def perform(self, action: dict, skill: str, byte_cap: int, timeout: float) -> dict: ...
    def describe_card(self, card: dict, *, compatibility: bool = False) -> dict: ...
    def probe_route(self, route: dict, timeout: float) -> dict: ...


class SearchError(CoordinationError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, ERROR_HTTP.get(code, 400), details

    def body(self):
        return {"code": self.code, "error": str(self), **self.details}


def parse_spec(body):
    data = dict(body)
    for name in ("round_budget", "total_budget"):
        if data.get(name) is not None:
            data[name] = Budget.from_dict(data[name])
    spec = SearchSpec(**data)
    if len(spec.skill) > 96:
        raise ValueError("能力标识最多 96 个字符")
    if not isinstance(spec.required, dict) or not isinstance(spec.preferences, dict):
        raise ValueError("required 和 preferences 必须是对象")
    CandidatePolicy(spec).evaluate([], "local/1", {})
    return spec


class CandidatePolicy:
    """Small, explicit local policy; unknown business conditions need input."""
    def __init__(self, spec):
        self.spec = spec

    def evaluate(self, candidates, policy_version, budget_remaining):
        from .coordination import PolicyDecision
        supported = {"provider_did", "version"}
        if set(self.spec.required) - supported or self.spec.policy_id not in {"", "local-count/1"}:
            return PolicyDecision("NEEDS_INPUT", ["UNSUPPORTED_LOCAL_CONDITION"],
                                  policy_version=policy_version)
        selected = []
        for candidate in candidates:
            if candidate["verification"] != "CARD_VERIFIED":
                continue
            if self.spec.required.get("provider_did") not in (None, candidate["key"]["provider_did"]):
                continue
            if self.spec.required.get("version") is not None and not any(
                    c["card"].get("version") == self.spec.required["version"]
                    for c in candidate["cards"]):
                continue
            selected.append(CandidateKey.from_dict(candidate["key"]))
        wanted = self.spec.preferences.get("min_candidates", 3)
        if isinstance(wanted, bool) or not isinstance(wanted, int) or not 1 <= wanted <= 256:
            raise ValueError("min_candidates 必须是 1–256 的整数")
        return PolicyDecision("SATISFIED" if len(selected) >= wanted else "CONTINUE",
                              ["ENOUGH_VERIFIED_CANDIDATES"] if len(selected) >= wanted else [],
                              selected, policy_version=policy_version)


class CoordinationService:
    NS = "coord_searches"
    COMMANDS = "coord_commands"
    TTL = 3600

    def __init__(self, store, network: DiscoverySourcePort, *, policy_factory=CandidatePolicy):
        self.store, self.network = store, network
        self.policy_factory = policy_factory
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._workers = {}
        self._stop = threading.Event()
        # Interrupted read-only work can be resumed; its reservation stays used.
        for sid, row in self.store.items(self.NS).items():
            if row["snapshot"]["state"] == "RUNNING" or row.get("inflight") or row.get("probe_active"):
                if row.get("inflight"):
                    row["frontier"].insert(0, row.pop("inflight"))
                row["snapshot"]["state"] = "PAUSED"
                row["snapshot"]["revision"] += 1
                row["snapshot"]["stop_reason"] = "NODE_RESTARTED"
                row.pop("probe_active", None)
                self.store.put(self.NS, sid, row)
        for cid, command in self.store.items(self.COMMANDS).items():
            if command["result"].get("in_progress"):
                command["result"].update(in_progress=False, reason_codes=["PROBE_INTERRUPTED"])
                self.store.put(self.COMMANDS, cid, command)

    def _load(self, sid):
        row = self.store.get(self.NS, sid)
        if row is None:
            raise SearchError("NOT_FOUND", "没有这次搜索")
        if time.time() >= row["expires_at"]:
            self._state(row, "EXPIRED", "SESSION_TTL")
            self.store.put(self.NS, sid, row)
            raise SearchError("SESSION_EXPIRED", "搜索会话已过期")
        return row

    def _save(self, row):
        snap = row["snapshot"]
        ledger = BudgetLedger.from_dict(row["ledger"])
        snap.update(budget_used=ledger.used(), budget_remaining=ledger.remaining(),
                    candidate_count=len(row["candidates"]), frontier_count=len(row["frontier"]))
        self.store.put(self.NS, snap["search_id"], row)
        self._changed.notify_all()

    def _state(self, row, state, reason=""):
        snap = row["snapshot"]
        if snap["state"] != state:
            snap["revision"] += 1
        snap.update(state=state, stop_reason=reason)

    def _command(self, sid, action, command_id, content, revision, fn):
        if not command_id or len(command_id) > 200:
            raise SearchError("INVALID_REQUEST", "必须提供有效的 Idempotency-Key")
        digest = hashlib.sha256(canonical_json([sid, action, content]).encode()).hexdigest()
        with self._lock, self.store.tx():
            if self._stop.is_set():
                raise SearchError("BUSY", "节点正在停止")
            previous = self.store.get(self.COMMANDS, command_id)
            if previous and previous["expires_at"] > time.time():
                if previous["digest"] != digest:
                    raise SearchError("IDEMPOTENCY_CONFLICT", "同一命令标识对应了不同内容")
                return previous["result"]
            row = self._load(sid) if sid else None
            if row is not None:
                if revision is None:
                    raise SearchError("REV_REQUIRED", "必须提供 If-Match 控制版本")
                if row["snapshot"]["revision"] != revision:
                    raise SearchError("REV_CONFLICT", "搜索状态已变化",
                                      current_revision=row["snapshot"]["revision"])
            result = fn(row)
            expires = row["expires_at"] if row else time.time() + self.TTL
            self.store.put(self.COMMANDS, command_id,
                           {"digest": digest, "result": result, "expires_at": expires})
            return result

    def start(self, spec: SearchSpec, command_id: str):
        body = {**spec.__dict__} if hasattr(spec, "__dict__") else {
            name: getattr(spec, name) for name in spec.__dataclass_fields__}
        for name in ("round_budget", "total_budget"):
            body[name] = body[name].to_dict() if body[name] else None
        def create(_):
            for old_sid, old in self.store.items(self.NS).items():
                if old["expires_at"] <= time.time() and old_sid not in self._workers:
                    self.store.delete(self.NS, old_sid)
            for cid, old in self.store.items(self.COMMANDS).items():
                if old["expires_at"] <= time.time():
                    self.store.delete(self.COMMANDS, cid)
            if self.store.count(self.NS) >= 256:
                raise SearchError("BUSY", "本机搜索会话已达上限，请等待旧会话过期")
            if sum(r["snapshot"]["state"] == "RUNNING" for r in self.store.items(self.NS).values()) >= 4:
                raise SearchError("BUSY", "同时最多运行四次搜索")
            sid = "search_" + uuid.uuid4().hex
            roots = self.network.roots(spec.skill)
            frontier = [{"kind": "find", "source": s, "depth": 0, "cursor": ""} for s in roots[:32]]
            snap = SearchSnapshot(sid, state="RUNNING" if frontier else "ISOLATED",
                                  spec_version=spec.policy_version or "local/1").to_dict()
            row = {"snapshot": snap, "spec": body, "frontier": frontier,
                   "created_at": time.time(),
                   "visited": [], "candidates": {}, "errors": [],
                   "ledger": BudgetLedger(spec.round_budget or Budget.defaults()).to_dict(),
                   "total_ledger": BudgetLedger(spec.total_budget).to_dict() if spec.total_budget else None,
                   "expires_at": time.time() + self.TTL, "active_ms": 0}
            self._save(row)
            return copy.deepcopy(row["snapshot"])
        out = self._command("", "start", command_id, body, None, create)
        self._spawn(out["search_id"])
        return SearchSnapshot(**out)

    def get(self, sid):
        with self._lock:
            return SearchSnapshot(**self._load(sid)["snapshot"])

    def control(self, sid, action, command_id, revision, body=None):
        body = body or {}
        def change(row):
            state = row["snapshot"]["state"]
            if state in {"CANCELLED", "EXPIRED"} and action != "cancel":
                raise SearchError("STATE_CONFLICT", "终止的搜索不能继续")
            if action == "pause":
                if state not in {"RUNNING", "PAUSED"}:
                    raise SearchError("STATE_CONFLICT", "当前搜索不能暂停")
                self._state(row, "PAUSED", "USER_PAUSED")
            elif action == "cancel":
                self._state(row, "CANCELLED", "USER_CANCELLED")
            elif action == "resume":
                if state not in RESUMABLE_STATES:
                    raise SearchError("STATE_CONFLICT", "当前搜索不能续查")
                if row.get("inflight") or row.get("probe_active"):
                    raise SearchError("BUSY", "上一项只读网络操作尚未结束，请稍后续查")
                if sum(r["snapshot"]["state"] == "RUNNING" for r in self.store.items(self.NS).values()) >= 4:
                    raise SearchError("BUSY", "同时最多运行四次搜索")
                budget = Budget.from_dict(body["round_budget"]) if body.get("round_budget") else Budget.defaults()
                if len(row["candidates"]) > budget.candidate_limit:
                    raise SearchError("INVALID_REQUEST", "续查候选上限不能小于已有候选数")
                if "preferences" in body:
                    spec = {**row["spec"], "preferences": body["preferences"]}
                    parse_spec(spec)
                    row["spec"] = spec
                    row["snapshot"]["spec_version"] = "local/" + str(row["snapshot"]["round"] + 1)
                row["ledger"] = BudgetLedger(budget).to_dict()
                row["ledger"]["used"]["candidate_limit"] = len(row["candidates"])
                row["snapshot"]["round"] += 1
                # Reconnect roots when the previous scope was exhausted/offline.
                if not row["frontier"]:
                    row["frontier"] = [{"kind": "find", "source": s, "depth": 0, "cursor": ""}
                                       for s in self.network.roots(row["spec"]["skill"])[:32]]
                    row["visited"] = []
                elif any(item["routes"] and all(r["expires_at"] <= time.time() for r in item["routes"])
                         for item in row["candidates"].values()):
                    # Continue the queue and revalidate stale channels through known authenticated roots.
                    queued = {a["source"].get("endpoint") for a in row["frontier"] if a["kind"] == "find"}
                    row["frontier"].extend({"kind": "find", "source": source, "depth": 0, "cursor": ""}
                                           for source in self.network.roots(row["spec"]["skill"])[:32]
                                           if source.get("endpoint") not in queued)
                    row["frontier"] = row["frontier"][:512]
                self._state(row, "RUNNING" if row["frontier"] else "ISOLATED")
            elif action == "evaluate":
                self._evaluate(row)
            else:
                raise SearchError("NOT_FOUND", "没有这个搜索操作")
            self._save(row)
            return {**row["snapshot"], **({"decision": row.get("decision")} if action == "evaluate" else {})}
        result = self._command(sid, action, command_id, body, revision, change)
        if action == "resume":
            self._spawn(sid)
        return result

    def pause(self, search_id, command_id, expected_revision):
        return SearchSnapshot(**self.control(search_id, "pause", command_id, expected_revision))

    def resume(self, search_id, command_id, expected_revision, round_budget=None):
        return SearchSnapshot(**self.control(search_id, "resume", command_id, expected_revision,
                              {"round_budget": round_budget.to_dict()} if round_budget else {}))

    def cancel(self, search_id, command_id, expected_revision):
        return SearchSnapshot(**self.control(search_id, "cancel", command_id, expected_revision))

    def evaluate(self, search_id, command_id, expected_revision):
        return self.control(search_id, "evaluate", command_id, expected_revision)

    def reserve_metadata(self, search_id, command_id, expected_revision, budget):
        """Generic read-only metadata budget port; knows no evaluation rules.

        Reserve before dispatch and keep reservations after failure/restart. These
        operations share discovery ledgers and a cumulative per-session ceiling.
        """
        caps = {"remote_operations": 24, "received_bytes": 524288, "duration_ms": 5000}
        if (not isinstance(budget, dict) or set(budget) != set(caps)
                or any(type(v) is not int or not 1 <= v <= caps[k] for k,v in budget.items())):
            raise ValueError("INVALID_METADATA_BUDGET")
        def reserve(row):
            if row["snapshot"]["state"] in {"RUNNING", "CANCELLED", "EXPIRED"} or row.get("inflight") or row.get("probe_active"):
                raise SearchError("STATE_CONFLICT", "请等本轮发现结束，再补充资料")
            used = row.get("metadata_used", dict.fromkeys(caps, 0))
            if any(used[k]+budget[k] > caps[k] for k in caps) or not self._reserve(row, **budget):
                raise SearchError("STATE_CONFLICT", "本次搜索的资料额度不足，可继续发现或开始新搜索")
            row["metadata_used"] = {k: used[k]+budget[k] for k in caps}
            row["snapshot"]["revision"] += 1
            self._save(row)
            return {"search_id": search_id, "revision": row["snapshot"]["revision"],
                    "reserved": budget, "metadata_used": row["metadata_used"],
                    "budget_remaining": row["snapshot"]["budget_remaining"]}
        return self._command(search_id, "metadata-reserve", command_id, budget, expected_revision, reserve)

    def _evaluate(self, row):
        spec = parse_spec(row["spec"])
        decision = self.policy_factory(spec).evaluate(copy.deepcopy(list(row["candidates"].values())),
                                                      row["snapshot"]["spec_version"],
                                                      BudgetLedger.from_dict(row["ledger"]).remaining())
        row["decision"] = decision.to_dict()
        row["decision"]["result_revision"] = row["snapshot"]["result_revision"]
        if decision.decision == "SATISFIED":
            self._state(row, "SATISFIED", "LOCAL_POLICY")
        elif decision.decision == "NEEDS_INPUT":
            self._state(row, "PAUSED", "LOCAL_POLICY_NEEDS_INPUT")

    def _spawn(self, sid):
        with self._lock:
            worker = self._workers.get(sid)
            if worker and worker.is_alive():
                return
            worker = threading.Thread(target=self._run, args=(sid,), daemon=True, name="a2n-coordination")
            self._workers[sid] = worker
            worker.start()

    def _reserve(self, row, **deltas):
        ledgers = [BudgetLedger.from_dict(row["ledger"])]
        if row["total_ledger"]:
            ledgers.append(BudgetLedger.from_dict(row["total_ledger"]))
        if not all(l.reserve(**deltas) for l in ledgers):
            return False
        row["ledger"] = ledgers[0].to_dict()
        if row["total_ledger"]:
            row["total_ledger"] = ledgers[1].to_dict()
        return True

    def _merge(self, row, card, source, compatibility=False):
        info = self.network.describe_card(card, compatibility=compatibility)
        key = canonical_json(info["key"])
        item = row["candidates"].get(key)
        if item is None:
            cap = min(r["total"]["candidate_limit"] for r in (row["ledger"], row["total_ledger"]) if r)
            if len(row["candidates"]) >= cap:
                return
            item = {"key": info["key"], "cards": [], "routes": [], "sources": [],
                    "verification": info["verification"]}
            row["candidates"][key] = item
            for ledger in (row["ledger"], row["total_ledger"]):
                if ledger:
                    ledger["used"]["candidate_limit"] = len(row["candidates"])
        if not any(c["card_hash"] == info["card_hash"] for c in item["cards"]):
            item["cards"].append({"card": card, "card_hash": info["card_hash"],
                                  "verified_at": int(time.time()), **info["key"]})
            if info.get("route"):
                item["routes"].append(info["route"])
            row["snapshot"]["result_revision"] += 1
        elif info.get("route"):
            # The card can be unchanged while the verified observation lease is renewed.
            route = next((r for r in item["routes"] if r["route_id"] == info["route"]["route_id"]), None)
            if route is not None and route["expires_at"] < info["route"]["expires_at"]:
                route.update(info["route"])
                row["snapshot"]["result_revision"] += 1
        provenance = {"node_did": source.get("node_did", ""), "source": source.get("kind", "coord"),
                      "endpoint": source.get("endpoint", "")}
        if provenance not in item["sources"]:
            item["sources"].append(provenance)
        if info["verification"] == "CARD_VERIFIED":
            item["verification"] = "CARD_VERIFIED"

    def _next_round(self, row, cost, byte_cap):
        """Automatic continuation is bounded by the separately authorized total."""
        if not row["spec"].get("auto_continue") or not row["total_ledger"] or row["snapshot"]["round"] >= 64:
            return False
        remaining = BudgetLedger.from_dict(row["total_ledger"]).remaining()
        if remaining["remote_operations"] < cost or remaining["received_bytes"] < byte_cap or remaining["duration_ms"] <= 0:
            return False
        budget = parse_spec(row["spec"]).round_budget or Budget.defaults()
        row["ledger"] = BudgetLedger(budget).to_dict()
        row["ledger"]["used"]["candidate_limit"] = len(row["candidates"])
        row["snapshot"]["round"] += 1
        row["snapshot"]["revision"] += 1
        self._save(row)
        return True

    def _run(self, sid):
        try:
            while not self._stop.is_set():
                with self._lock:
                    row = self._load(sid)
                    if row["snapshot"]["state"] != "RUNNING":
                        return
                    if not row["frontier"]:
                        self._evaluate(row)
                        if row["snapshot"]["state"] == "RUNNING":
                            self._state(row, "FRONTIER_EXHAUSTED", "KNOWN_SCOPE_EXHAUSTED")
                        self._save(row)
                        return
                    action = row["frontier"][0]
                    budget = BudgetLedger.from_dict(row["ledger"])
                    remaining = budget.remaining()
                    if row["total_ledger"]:
                        total = BudgetLedger.from_dict(row["total_ledger"]).remaining()
                        remaining = {k: min(v, total[k]) for k, v in remaining.items()}
                    depth_cap = min(budget.total.introduction_depth, 16,
                                    row["total_ledger"]["total"]["introduction_depth"] if row["total_ledger"] else 16)
                    def source_id(action):
                        source = action["source"]
                        return source.get("node_did") or (source.get("record") or {}).get("node_did") or source.get("endpoint", "")
                    turns = row.setdefault("source_turns", {})
                    eligible = min((i for i, a in enumerate(row["frontier"]) if a["depth"] <= depth_cap),
                                   key=lambda i: (turns.get(source_id(row["frontier"][i]), 0),
                                                  row["frontier"][i]["kind"] != "card", i), default=None)
                    if eligible is not None:
                        action = row["frontier"].pop(eligible)
                        row["frontier"].insert(0, action)
                    if action["depth"] > depth_cap or remaining["duration_ms"] <= 0:
                        if action["depth"] <= depth_cap and self._next_round(row, self.network.cost(action), 1024):
                            continue
                        self._state(row, "BUDGET_REACHED", "DEPTH_OR_DURATION_LIMIT")
                        self._save(row)
                        return
                    byte_cap = min(131072 if action["kind"] == "card" else 65536, remaining["received_bytes"])
                    cost = self.network.cost(action)
                    # Reserve bytes before I/O; an interrupted operation keeps its reservation.
                    if byte_cap < 1024 or not self._reserve(row, remote_operations=cost, received_bytes=byte_cap):
                        if self._next_round(row, cost, 1024):
                            # If this round is too small for one operation, do not loop.
                            fresh = BudgetLedger.from_dict(row["ledger"]).remaining()
                            if fresh["remote_operations"] >= cost and fresh["received_bytes"] >= 1024:
                                continue
                        self._state(row, "BUDGET_REACHED", "OPERATION_OR_BYTE_LIMIT")
                        self._save(row)
                        return
                    row["frontier"].pop(0)
                    turns[source_id(action)] = turns.get(source_id(action), 0) + 1
                    row["inflight"] = action
                    for ledger in (row["ledger"], row["total_ledger"]):
                        if ledger:
                            ledger["used"]["introduction_depth"] = max(ledger["used"]["introduction_depth"], action["depth"])
                            ledger["used"]["max_concurrency"] = 1
                    row["snapshot"]["progress_revision"] += 1
                    self._save(row)
                began = time.monotonic()
                result, error = None, None
                try:
                    result = self.network.perform(action, row["spec"]["skill"], byte_cap,
                                                  max(.001, remaining["duration_ms"] / 1000))
                except Exception as exc:
                    error = str(exc)
                duration = max(1, int((time.monotonic() - began) * 1000))
                with self._lock:
                    if self._stop.is_set():
                        return
                    row = self._load(sid)
                    row.pop("inflight", None)
                    # A complete, bounded response gives an exact byte count.
                    actual = max(0, min(byte_cap, int((result or {}).get("bytes", byte_cap))))
                    for ledger_name in ("ledger", "total_ledger"):
                        if row[ledger_name]:
                            ledger = row[ledger_name]
                            ledger["used"]["received_bytes"] -= byte_cap - actual
                            ledger["used"]["duration_ms"] = min(ledger["total"]["duration_ms"],
                                                                   ledger["used"]["duration_ms"] + duration)
                    row["snapshot"]["progress_revision"] += 1
                    if error:
                        row["errors"] = (row["errors"] + [{"source": action["source"], "error": error}])[-32:]
                    elif result:
                        source = result.get("source", action["source"])
                        entries = [{"card": card, "source": source} for card in result.get("cards", [])]
                        entries.extend(result.get("entries", []))
                        for entry in entries[:256]:
                            try:
                                self._merge(row, entry["card"], entry.get("source", source), result.get("compatibility", False))
                            except (ValueError, TypeError, KeyError) as exc:
                                row["errors"] = (row["errors"] + [{"error": str(exc)}])[-32:]
                        pending = [{"kind": "card", "source": source, "depth": action["depth"], "offer": o}
                                   for o in result.get("offers", [])[:20]]
                        if result.get("next_cursor"):
                            pending.append({"kind": "find", "source": source, "depth": action["depth"],
                                            "cursor": result["next_cursor"]})
                        for target in result.get("referrals", [])[:8]:
                            identity = target.get("node_did")
                            if identity and identity not in row["visited"]:
                                row["visited"].append(identity)
                                pending.append({"kind": "find", "source": target, "depth": action["depth"] + 1,
                                                "cursor": ""})
                        row["frontier"] = (row["frontier"] + pending)[:512]
                        if entries and row["snapshot"]["state"] == "RUNNING":
                            self._evaluate(row)
                    self._save(row)
        except Exception as exc:
            with self._lock:
                row = self.store.get(self.NS, sid)
                if row and row["snapshot"]["state"] == "RUNNING":
                    self._state(row, "PAUSED", "WORKER_ERROR")
                    row["errors"] = (row["errors"] + [{"error": str(exc)}])[-32:]
                    self._save(row)
        finally:
            with self._lock:
                self._workers.pop(sid, None)
                # A resume can arrive while a paused worker is finishing its I/O.
                row = self.store.get(self.NS, sid) if not self._stop.is_set() else None
                if row and row["snapshot"]["state"] == "RUNNING":
                    self._spawn(sid)

    def candidates(self, sid, result_cursor="", limit=20):
        with self._lock:
            row = self._load(sid)
            limit = max(1, min(int(limit), 100))
            revision = row["snapshot"]["result_revision"]
            start = 0
            if result_cursor:
                try:
                    cursor_sid, cursor_revision, cursor_limit, start = json.loads(result_cursor)
                except (TypeError, ValueError):
                    raise SearchError("INVALID_REQUEST", "结果游标无效")
                if (cursor_sid, cursor_revision, cursor_limit) != (sid, revision, limit):
                    raise SearchError("RESULT_CHANGED", "结果版本已变化，请重新读取第一页")
                if isinstance(start, bool) or not isinstance(start, int) or start < 0:
                    raise SearchError("INVALID_REQUEST", "结果游标偏移无效")
            items = list(row["candidates"].values())
            return {"search_id": sid, "result_revision": revision,
                    "items": copy.deepcopy(items[start:start + limit]), "errors": row["errors"],
                    "next_result_cursor": json.dumps([sid, revision, limit, start + limit])
                    if start + limit < len(items) else ""}

    def plan(self, sid, key, command_id, expected_revision, probe=False):
        if isinstance(key, dict):
            key = CandidateKey.from_dict(key)
        owner = False
        def build(row):
            nonlocal owner
            candidate = row["candidates"].get(canonical_json(key.to_dict()))
            if not candidate:
                raise SearchError("NOT_FOUND", "没有这个候选")
            choices = [copy.deepcopy(r) for r in candidate["routes"] if r["expires_at"] > time.time()]
            for route in choices:
                route["observation"] = row.get("observations", {}).get(route["route_id"])
            if probe and choices:
                if row["snapshot"]["state"] == "RUNNING" or row.get("inflight") or row.get("probe_active"):
                    raise SearchError("BUSY", "请等待搜索停止后再探测")
                count = len(choices)
                if not self._reserve(row, remote_operations=count, probe_operations=count,
                                     received_bytes=65536 * count, duration_ms=800 * count):
                    raise SearchError("STATE_CONFLICT", "探测额度不足")
                row["probe_active"] = command_id
                owner = True
            choices.sort(key=self._route_order)
            self._save(row)
            return {"key": key.to_dict(), "plan_revision": row["snapshot"]["result_revision"],
                    "choices": choices, "preferred_route_id": choices[0]["route_id"] if choices else "",
                    "reason_codes": ["LOCAL_OBSERVATION_THEN_DIRECT"],
                    "remaining_budget": BudgetLedger.from_dict(row["ledger"]).remaining(),
                    "in_progress": owner}
        result = self._command(sid, "plan", command_id, {"key": key.to_dict(), "probe": probe},
                               expected_revision, build)
        if not owner:
            return self._plan_contract(result)
        # Never perform I/O in a store transaction: the peer can be this same node.
        choices = copy.deepcopy(result["choices"])
        for route in choices:
            try:
                route["observation"] = self.network.probe_route(route, .8)
            except Exception as exc:
                route["observation"] = {"control_reachable": False, "error": str(exc)}
        with self._lock:
            if self._stop.is_set():
                return self._plan_contract({**result, "in_progress": False, "reason_codes": ["PROBE_INTERRUPTED"]})
            with self.store.tx():
                row = self._load(sid)
                row.pop("probe_active", None)
                for route in choices:
                    row.setdefault("observations", {})[route["route_id"]] = route["observation"]
                row["snapshot"]["result_revision"] += 1
                self._save(row)
                choices.sort(key=self._route_order)
                result.update(choices=choices, preferred_route_id=choices[0]["route_id"],
                              plan_revision=row["snapshot"]["result_revision"], in_progress=False)
                command = self.store.get(self.COMMANDS, command_id)
                command["result"] = result
                self.store.put(self.COMMANDS, command_id, command)
                return self._plan_contract(result)

    @staticmethod
    def _plan_contract(result):
        data = {k: copy.deepcopy(v) for k, v in result.items() if k != "in_progress"}
        data["key"] = CandidateKey.from_dict(data["key"])
        if result.get("in_progress"):
            data["reason_codes"] = ["PROBE_IN_PROGRESS"]
        return RoutePlan(**data)

    @staticmethod
    def _route_order(route):
        obs = route.get("observation") or {}
        return (obs.get("control_reachable") is False,
                obs.get("rtt_ms") if obs.get("control_reachable") and obs.get("rtt_ms") is not None else float("inf"),
                route["channel_type"] != "direct_a2a", route["route_id"])

    def selected_routes(self, sid, key):
        """Local application boundary: return only verified, unexpired choices."""
        with self._lock:
            row = self._load(sid)
            item = row["candidates"].get(canonical_json(CandidateKey.from_dict(key).to_dict()))
            if not item or item["verification"] != "CARD_VERIFIED":
                raise SearchError("NOT_FOUND", "没有已验证的候选")
            choices = [copy.deepcopy(r) for r in item["routes"] if r["expires_at"] > time.time()]
            for route in choices:
                route["observation"] = row.get("observations", {}).get(route["route_id"])
            return sorted(choices, key=self._route_order)

    def search(self, skill, *, timeout=2, limit=30):
        spec = SearchSpec(skill, preferences={"min_candidates": min(limit, 256)},
                          round_budget=Budget(**{**Budget.defaults().to_dict(), "duration_ms": max(100, int(timeout * 1000))}))
        snap = self.start(spec, uuid.uuid4().hex)
        end = time.monotonic() + timeout + .1
        with self._changed:
            while self.get(snap.search_id).state == "RUNNING" and time.monotonic() < end:
                self._changed.wait(max(.001, end - time.monotonic()))
            current = self.get(snap.search_id)
            if current.state == "RUNNING":
                self.pause(snap.search_id, uuid.uuid4().hex, current.revision)
            page = self.candidates(snap.search_id, limit=limit)
            results = []
            for item in page["items"]:
                if not item["cards"]:
                    continue
                provenance = item["sources"][0]["source"] if item["sources"] else "coordination"
                results.append({"card": item["cards"][-1]["card"], "headers": {},
                                "source": "public-node" if provenance == "coord" else provenance,
                                "key": item["key"], "routes": item["routes"], "sources": item["sources"],
                                "verification": item["verification"]})
            return results, page["errors"], {**self.get(snap.search_id).to_dict()}

    def close(self):
        self._stop.set()
        with self._lock:
            for sid in list(self._workers):
                row = self.store.get(self.NS, sid)
                if row and row["snapshot"]["state"] == "RUNNING":
                    self._state(row, "PAUSED", "NODE_STOPPED")
                    self._save(row)
            workers = list(self._workers.values())
        for worker in workers:
            worker.join(timeout=6)
