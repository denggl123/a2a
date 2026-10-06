"""Budgeted on-demand experience queries. Queries never execute an Agent."""
from __future__ import annotations

import secrets
import threading
import time

from .executor import DaemonExecutor
from .experience import matches, validate_subject
from .trade_facts import digest


class ExperienceService:
    def __init__(self, store, book, network, *, sources):
        self.store, self.book, self.network, self.sources = store, book, network, sources
        self._lock = threading.RLock()
        self._pool = DaemonExecutor(2, thread_name_prefix="a2n-experience")
        self._jobs = {}
        self._closed = False
        for key, row in store.items("experience_queries").items():
            if row.get("state") == "RUNNING":
                store.put("experience_queries", key, {**row, "state": "INTERRUPTED", "stop_reason": "RESTARTED"})

    def start(self, spec, command_id):
        subject = validate_subject(spec.get("subject"))
        budget = dict(spec.get("budget") or {})
        defaults = {"max_sources": 8, "remote_operations": 16, "received_bytes": 1048576,
                    "duration_ms": 6000, "max_pages_per_source": 5}
        if set(budget) - set(defaults):
            raise ValueError("未知体验查询额度")
        budget = {**defaults, **budget}
        if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= defaults[k] for k, v in budget.items()):
            raise ValueError("体验查询额度必须是有界正整数")
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("必须提供 Idempotency-Key")
        fingerprint = digest([subject, budget])
        with self._lock, self.store.tx():
            prior = self.store.get("experience_query_commands", command_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(prior["query_id"])
            if self._closed or len(self._jobs) >= 4:
                raise ValueError("EXPERIENCE_QUERY_BUSY")
            candidates = self.sources(subject)
            unique = {}
            for source in candidates:
                if not isinstance(source, dict) or not source.get("endpoint"):
                    continue
                unique.setdefault((source["endpoint"], source.get("node_did", "")), dict(source))
            selected = list(unique.values())[:budget["max_sources"]]
            qid = "eq_" + secrets.token_hex(16)
            row = {"query_id": qid, "subject": subject, "budget": budget, "state": "RUNNING",
                   "revision": 1, "progress_revision": 0, "sources": selected,
                   "received_count": 0, "used_operations": 0, "used_bytes": 0,
                   "completed_sources": [], "missing_sources": [], "created_at": time.time(),
                   "global_completeness": "UNKNOWN", "known_sources_complete": False}
            self.store.put("experience_queries", qid, row)
            self.store.put("experience_query_commands", command_id, {"fingerprint": fingerprint, "query_id": qid})
            # Worker waits on this lock; no network runs inside the transaction.
            self._jobs[qid] = self._pool.submit(self._run, qid)
            return dict(row)

    def get(self, query_id):
        return self.store.get("experience_queries", query_id)

    def _save(self, row):
        with self._lock:
            if self._closed:
                return False
            row["progress_revision"] += 1
            self.store.put("experience_queries", row["query_id"], row)
            return True

    def _run(self, qid):
        with self._lock:
            row = self.get(qid)
        deadline = time.monotonic() + row["budget"]["duration_ms"] / 1000
        try:
            for source in row["sources"]:
                cursor, completed = "", False
                fetched = []
                snapshot, seen_cursors = None, set()
                source_id = source.get("node_did") or source["endpoint"]
                try:
                    for _ in range(row["budget"]["max_pages_per_source"]):
                        cost = (2 if source.get("mailbox") else 1) if source.get("node_did") else 3
                        remaining = row["budget"]["received_bytes"] - row["used_bytes"]
                        if (time.monotonic() >= deadline or remaining <= 0
                                or row["used_operations"] + cost > row["budget"]["remote_operations"]):
                            raise ValueError("QUERY_BUDGET_REACHED")
                        row["used_operations"] += cost  # Reserve before I/O, including failed requests.
                        reservation = min(262144, remaining)
                        row["used_bytes"] += reservation
                        if not self._save(row):
                            return
                        result = self.network.fetch(source, row["subject"], cursor=cursor,
                            timeout=min(3, max(.001, deadline - time.monotonic())), cap=reservation)
                        actual = result["bytes"]
                        if isinstance(actual, bool) or not isinstance(actual, int) or not 0 <= actual <= reservation:
                            raise ValueError("INVALID_SOURCE_BYTE_COUNT")
                        row["used_bytes"] -= reservation - actual
                        source["node_did"] = result["source_did"]
                        if not isinstance(result.get("snapshot"), str) or not result["snapshot"]:
                            raise ValueError("INVALID_SOURCE_SNAPSHOT")
                        if snapshot is not None and result["snapshot"] != snapshot:
                            raise ValueError("SOURCE_SNAPSHOT_CHANGED")
                        snapshot = result["snapshot"]
                        items = result.get("items")
                        if not isinstance(items, list) or len(items) > 50:
                            raise ValueError("INVALID_SOURCE_PAGE")
                        for record in items:
                            if not matches(record, row["subject"]):
                                raise ValueError("SOURCE_SUBJECT_MISMATCH")
                            accepted = self.book.ingest(record, source=source["node_did"], source_complete=False)
                            fetched.append(record)
                            if accepted.get("accepted") and not accepted.get("idempotent"):
                                row["received_count"] += 1
                        cursor = result.get("next_cursor") or ""
                        if not cursor:
                            completed = True
                            break
                        if not isinstance(cursor, str) or len(cursor) > 4096:
                            raise ValueError("INVALID_SOURCE_CURSOR")
                        if cursor in seen_cursors:
                            raise ValueError("SOURCE_CURSOR_LOOP")
                        seen_cursors.add(cursor)
                        if not self._save(row):
                            return
                    if completed:
                        self.book.confirm_source_page_set(fetched)
                        row["completed_sources"].append(source["node_did"])
                    else:
                        raise ValueError("SOURCE_PAGE_BUDGET_REACHED")
                except Exception as exc:
                    row["missing_sources"].append({"source": source_id, "reason": str(exc)[:200]})
                if not self._save(row):
                    return
            row["state"] = "COMPLETED" if row["sources"] else "ISOLATED"
            row["known_sources_complete"] = bool(row["sources"]) and not row["missing_sources"]
            row["stop_reason"] = "KNOWN_SOURCES_CHECKED" if row["known_sources_complete"] else "SOURCES_INCOMPLETE"
            row["revision"] += 1
            self._save(row)
        finally:
            with self._lock:
                self._jobs.pop(qid, None)

    def close(self):
        with self._lock:
            self._closed = True
            for qid in self._jobs:
                row = self.get(qid)
                self.store.put("experience_queries", qid, {**row, "state": "INTERRUPTED", "stop_reason": "STOPPED"})
        self._pool.shutdown(wait_timeout=.2)
