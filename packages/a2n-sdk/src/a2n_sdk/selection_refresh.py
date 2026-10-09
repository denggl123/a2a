"""Composition adapter for explicit metadata refresh, separate from pure ranking."""
from __future__ import annotations

import secrets
import threading
import time

from .selection.contracts import digest


class SelectionMetadataAdapter:
    def __init__(self, store, coordination, experience_service):
        self.store, self.coordination, self.experience_service = store, coordination, experience_service
        self._lock = threading.RLock()

    def start(self, body):
        allowed = {"search_id", "result_revision", "expected_revision", "keys", "budget", "command_id"}
        if not isinstance(body, dict) or set(body)-allowed:
            raise ValueError("INVALID_SELECTION_REFRESH")
        command = body.get("command_id")
        keys = body.get("keys")
        if (not isinstance(command, str) or not 1 <= len(command) <= 128 or not isinstance(keys, list)
                or not 1 <= len(keys) <= 2 or any(not isinstance(k, str) or len(k) > 512 for k in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError("INVALID_SELECTION_REFRESH_KEYS_OR_COMMAND")
        if type(body.get("expected_revision")) is not int or type(body.get("result_revision")) is not int:
            raise ValueError("INVALID_SELECTION_REVISION")
        fingerprint = digest({k:v for k,v in body.items() if k != "command_id"})
        with self._lock:
            previous = self.store.get("selection_refresh_commands", command)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(previous["job_id"])
            sid = body.get("search_id")
            selected, cursor, revision = {}, "", None
            while True:
                page = self.coordination.candidates(sid, result_cursor=cursor, limit=100)
                if revision is not None and revision != page["result_revision"]:
                    raise ValueError("RESULT_CHANGED")
                revision = page["result_revision"]
                for item in page["items"]:
                    key = item["key"]["provider_did"]+"|"+item["key"]["service_id"]
                    if key in keys and item["verification"] == "CARD_VERIFIED" and item["cards"]:
                        selected[key] = item
                cursor = page["next_result_cursor"]
                if not cursor:
                    break
            if body.get("result_revision") != revision or len(selected) != len(keys):
                raise ValueError("RESULT_CHANGED_OR_UNKNOWN_CANDIDATE")
            subjects, sources = {}, {}
            for item in selected.values():
                provider, service = item["key"]["provider_did"], item["key"]["service_id"]
                card = item["cards"][-1]["card"]
                ext = card.get("x-a2n")
                declaration = ext.get("experience") if isinstance(ext, dict) else None
                if isinstance(declaration, dict) and isinstance(declaration.get("endpoint"), str):
                    # The existing metadata network adapter verifies endpoints and signatures.
                    sources[provider] = [{"node_did": provider, "endpoint": declaration["endpoint"]}]
                for subject in ({"kind":"service", "provider_did":provider, "service_id":service},
                                {"kind":"provider", "provider_did":provider}):
                    subjects[digest(subject)] = subject
            budget = body.get("budget") or {"remote_operations": 12, "received_bytes": 262144, "duration_ms": 1500}
            count = len(subjects)
            if not isinstance(budget, dict) or set(budget) != {"remote_operations", "received_bytes", "duration_ms"}:
                raise ValueError("INVALID_METADATA_BUDGET")
            if any(type(v) is not int for v in budget.values()) or budget["remote_operations"] < count or budget["received_bytes"] < 1024*count or budget["duration_ms"] < 100:
                raise ValueError("METADATA_BUDGET_TOO_SMALL")
            reservation = self.coordination.reserve_metadata(sid, "selection-"+command,
                body.get("expected_revision"), budget)
            jid = "sr_"+secrets.token_hex(16)
            row = {"job_id": jid, "search_id": sid, "result_revision": revision, "state": "RUNNING",
                   "reservation": reservation, "queries": [], "errors": [], "created_at": time.time()}
            with self.store.tx():
                self.store.put("selection_refresh_jobs", jid, row)
                self.store.put("selection_refresh_commands", command, {"fingerprint":fingerprint, "job_id":jid})
            deadline = time.monotonic()+budget["duration_ms"]/1000
            for index, subject in enumerate(subjects.values()):
                try:
                    query = self.experience_service.start({"subject":subject, "budget":{
                        "max_sources":4, "remote_operations":budget["remote_operations"]//count,
                        "received_bytes":budget["received_bytes"]//count,
                        "duration_ms":budget["duration_ms"], "max_pages_per_source":2}},
                        "selection-query-"+digest([jid, index]),
                        extra_sources=sources.get(subject["provider_did"], []), deadline=deadline)
                    row["queries"].append(query["query_id"])
                except (ValueError, RuntimeError) as exc:
                    row["errors"].append(str(exc)[:200])
                self.store.put("selection_refresh_jobs", jid, row)
            return self.get(jid)

    def get(self, job_id):
        row = self.store.get("selection_refresh_jobs", job_id)
        if row is None:
            raise ValueError("SELECTION_REFRESH_NOT_FOUND")
        queries = [self.experience_service.get(qid) for qid in row["queries"]]
        running = any(q and q["state"] == "RUNNING" for q in queries)
        state = "CANCELLED" if row["state"] == "CANCELLED" else "RUNNING" if running else "COMPLETED"
        # Local read only: no network, re-ranking, policy action or ledger update.
        return {**row, "state":state, "used_operations":sum(q["used_operations"] for q in queries if q),
                "used_bytes":sum(q["used_bytes"] for q in queries if q),
                "known_sources_complete":bool(queries) and not row["errors"] and all(q and q["known_sources_complete"] for q in queries),
                "query_states":[{"query_id":q["query_id"], "subject":q["subject"], "state":q["state"],
                    "completed_sources":len(q["completed_sources"]), "sources":len(q["sources"]),
                    "missing_sources":q["missing_sources"]} for q in queries if q], "global":"UNKNOWN"}

    def cancel(self, job_id):
        with self._lock:
            row = self.get(job_id)
            if row["state"] == "RUNNING":
                for qid in row["queries"]:
                    self.experience_service.cancel(qid)
                saved = self.store.get("selection_refresh_jobs", job_id)
                self.store.put("selection_refresh_jobs", job_id, {**saved, "state":"CANCELLED"})
            return self.get(job_id)
