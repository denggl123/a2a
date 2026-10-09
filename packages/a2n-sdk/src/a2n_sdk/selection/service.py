"""Local selection application service. FactSource supplies local snapshots only."""
from __future__ import annotations

import secrets
import time
import json
from .contracts import VERSION, StorePort, FactSource, MetadataRefreshPort, clone, digest
from .scoring import normalize_profile, rank
from .demand import interpret


class SelectionService:
    algorithm = VERSION

    def __init__(self, store: StorePort, source: FactSource, *, refresh: MetadataRefreshPort | None = None, now=None):
        self.store, self.source = store, source
        self.refresh = refresh
        self.now = now or time.time

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
            raise ValueError("INVALID_SELECTION_ID")
        return value

    def profile(self, profile_id="balanced"):
        self._id(profile_id)
        return self.store.get("selection_profiles", profile_id) or {
            "profile_id": profile_id, "revision": 0, "values": normalize_profile(), "updated_at": None}

    def profiles(self):
        rows = self.store.items("selection_profiles")
        rows.setdefault("balanced", self.profile())
        return {"items": sorted(rows.values(), key=lambda r: r["profile_id"]), "algorithm": VERSION}

    def update_profile(self, profile_id, values, *, expected_revision):
        self._id(profile_id)
        cfg = normalize_profile(values)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("INVALID_SELECTION_REVISION")
        with self.store.tx():
            old = self.profile(profile_id)
            if expected_revision != old["revision"]:
                raise ValueError("REV_CONFLICT")
            row = {"profile_id": profile_id, "revision": old["revision"]+1,
                   "values": cfg, "updated_at": self.now()}
            self.store.put("selection_profiles", profile_id, row)
            self.store.put("selection_profile_versions", digest([profile_id, row["revision"]]), row)
        return clone(row)

    def assess(self, body):
        allowed = {"search_id", "result_revision", "profile_id", "task", "limit"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("INVALID_SELECTION_REQUEST")
        search_id = self._id(body.get("search_id"))
        revision = body.get("result_revision")
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
            raise ValueError("INVALID_SELECTION_REVISION")
        profile = self.profile(body.get("profile_id", "balanced"))
        limit = body.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("INVALID_SELECTION_PAGE_SIZE")
        if body.get("task") is not None and not isinstance(body["task"], dict):
            raise ValueError("INVALID_SELECTION_TASK")
        now = self.now()
        facts = self.source.snapshot(search_id, revision, clone(body.get("task") or {}), profile["values"], now)
        result = rank(facts["candidates"], profile=profile["values"], task=facts["task"],
                      metrics=facts["metrics"], now=now, seed=digest([search_id, profile["profile_id"]]))
        sid = "ss_" + secrets.token_hex(16)
        row = {"v": VERSION, **result, "snapshot_id": sid, "search_id": search_id,
               "result_revision": facts["result_revision"], "profile_id": profile["profile_id"],
               "search_started_at":facts.get('search_started_at'),
               "profile_revision": profile["revision"], "metrics_revision": facts["metrics_revision"],
               "as_of": now, "valid_until": min(now+60, facts.get("valid_until", now+60)),
               "coverage": facts.get("coverage", {"global": "UNKNOWN"}),
               "input_digest": digest([facts, profile]), "notice": "适合度是本机本次需求下的推荐，不是成功概率。"}
        size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
        if size > 16*1024*1024:
            raise ValueError("SELECTION_SNAPSHOT_TOO_LARGE")
        with self.store.tx():
            self.store.put("selection_snapshots", sid, row)
            self.store.put("selection_snapshot_sizes", sid, size)
            # Snapshots are rebuildable; keep a bounded local cache.
            all_rows = sorted(self.store.items("selection_snapshots").items(), key=lambda r: r[1]["as_of"])
            sizes = self.store.items("selection_snapshot_sizes")
            for key, old in all_rows:
                if key not in sizes:
                    sizes[key] = len(json.dumps(old, ensure_ascii=False).encode())
                    self.store.put("selection_snapshot_sizes", key, sizes[key])
            total = sum(sizes.get(key, 0) for key, old in all_rows)
            for i, (key, old) in enumerate(all_rows):
                if old["as_of"] < now-7*86400 or i < len(all_rows)-128 or total > 50*1024*1024:
                    self.store.delete("selection_snapshots", key)
                    self.store.delete("selection_snapshot_sizes", key)
                    total -= sizes[key]
                else:
                    break
        return self.page(sid, limit=limit)

    def page(self, snapshot_id, *, cursor="", limit=100):
        self._id(snapshot_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("INVALID_SELECTION_PAGE_SIZE")
        row = self.store.get("selection_snapshots", snapshot_id)
        if row is None:
            raise ValueError("SELECTION_SNAPSHOT_NOT_FOUND")
        start = 0
        if cursor:
            import json
            try:
                binding, start, original_limit = json.loads(cursor)
            except (TypeError, ValueError):
                raise ValueError("INVALID_SELECTION_CURSOR") from None
            if binding != row["input_digest"] or original_limit != limit or isinstance(start, bool) or not isinstance(start, int) or start < 0:
                raise ValueError("INVALID_SELECTION_CURSOR")
        import json
        output = {k: v for k, v in row.items() if k != "items"}
        output["items"] = row["items"][start:start+limit]
        output["next_cursor"] = json.dumps([row["input_digest"], start+limit, limit]) if start+limit < len(row["items"]) else ""
        output["stale"] = self.now() >= row["valid_until"]
        return clone(output)

    def explain(self, snapshot_id, candidate_key):
        row = self.store.get("selection_snapshots", self._id(snapshot_id))
        if row is None:
            raise ValueError("SELECTION_SNAPSHOT_NOT_FOUND")
        item = next((i for i in row["items"] if i["key"] == candidate_key), None)
        if item is None:
            raise ValueError("SELECTION_CANDIDATE_NOT_FOUND")
        return clone({"snapshot_id": snapshot_id, "as_of": row["as_of"],
                      "valid_until": row["valid_until"], "weights": row["weights"], "item": item})

    def command(self, path, body):
        if path == "/v1/selection/interpret":
            return 200, interpret(body)
        if path == "/v1/selection/choices":
            return 201, self.choose(body)
        if path == "/v1/selection/rank":
            return 200, self.assess(body)
        if path == "/v1/selection/network-context/reset":
            return 200, self.source.reset_network_context(self.now())
        if path == "/v1/selection/refresh":
            if self.refresh is None:
                raise ValueError("SELECTION_REFRESH_NOT_CONFIGURED")
            return 202, self.refresh.start(body)
        if path.startswith("/v1/selection/refresh/") and path.endswith("/cancel"):
            if self.refresh is None:
                raise ValueError("SELECTION_REFRESH_NOT_CONFIGURED")
            return 200, self.refresh.cancel(self._id(path.split("/")[-2]))
        prefix = "/v1/selection/profiles/"
        if path.startswith(prefix):
            return 200, self.update_profile(path[len(prefix):], body.get("values"),
                                             expected_revision=body.get("expected_revision"))
        raise ValueError("UNKNOWN_SELECTION_COMMAND")

    def read(self, path, query):
        if path == "/v1/selection/outcomes":
            return 200, self.outcomes()
        if path == "/v1/selection/subjects":
            subject = {k:v for k,v in query.items() if k in {"kind", "provider_did", "buyer_did", "service_id"}}
            return 200, self.source.subject_view(subject, self.now())
        if path.startswith("/v1/selection/refresh/"):
            if self.refresh is None:
                raise ValueError("SELECTION_REFRESH_NOT_CONFIGURED")
            return 200, self.refresh.get(self._id(path.rsplit("/", 1)[-1]))
        if path == "/v1/selection/profiles":
            return 200, self.profiles()
        if path.startswith("/v1/selection/profiles/"):
            return 200, self.profile(path.removeprefix("/v1/selection/profiles/"))
        if path.startswith("/v1/selection/snapshots/"):
            tail = path.removeprefix("/v1/selection/snapshots/")
            if "/explain/" in tail:
                sid, key = tail.split("/explain/", 1)
                return 200, self.explain(sid, key)
            return 200, self.page(tail, cursor=query.get("cursor", ""), limit=int(query.get("limit", 100)))
        raise ValueError("UNKNOWN_SELECTION_QUERY")

    def choose(self, body):
        if not isinstance(body, dict) or set(body) != {"snapshot_id", "candidate_key", "scope", "task_id"}:
            raise ValueError("INVALID_SELECTION_CHOICE")
        sid = self._id(body["snapshot_id"])
        snapshot = self.store.get("selection_snapshots", sid)
        if not snapshot or self.now() - snapshot["as_of"] > 3600:
            raise ValueError("SELECTION_CHOICE_SNAPSHOT_EXPIRED")
        candidates = snapshot["items"]
        item = next((r for r in candidates if r["key"] == body["candidate_key"]), None)
        if item is None: raise ValueError("SELECTION_CANDIDATE_NOT_FOUND")
        scope, task = body["scope"], body["task_id"]
        if not all(isinstance(v,str) and 1 <= len(v) <= 96 for v in (scope,task)):
            raise ValueError("INVALID_SELECTION_CHOICE_TASK")
        self.source.validate_choice(scope, item["key"])
        key = digest([scope, task])
        row = {**body, "choice_id":key,"rank":next(i+1 for i,r in enumerate(candidates) if r["key"]==item["key"]),
               "chosen_at":self.now(),"search_id":snapshot["search_id"],"task":snapshot["task"],
               "selection_seconds":max(0,self.now()-snapshot["as_of"])}
        started=snapshot.get('search_started_at')
        row['discovery_to_choice_seconds']=max(0,self.now()-started) if type(started) in (int,float) else None
        with self.store.tx():
            old = self.store.get("selection_choices",key)
            if old:
                if any(old[k] != body[k] for k in body): raise ValueError("IDEMPOTENCY_CONFLICT")
                return clone(old)
            self.store.put("selection_choices",key,row)
        return clone(row)

    def outcomes(self):
        choices = sorted(self.store.items("selection_choices").values(), key=lambda r:r["chosen_at"], reverse=True)
        rows = [{**r, **self.source.choice_outcome(r)} for r in choices[:5000]]
        selected = len(rows); rated=[r for r in rows if r.get("usefulness") is not None]
        human=[r for r in rated if r.get("label_source")=="HUMAN"]
        assistant=[r for r in rated if r.get("label_source")=="ASSISTANT"]
        used=[r for r in rows if r.get("execution")=="DELIVERED"]
        counts={}
        for r in used: counts[r["candidate_key"]]=counts.get(r["candidate_key"],0)+1
        return {"items":rows[:100],"total":len(choices),"measured":selected,"selected_top3":sum(r["rank"]<=3 for r in rows),
                "delivered":len(used),"rated":len(rated),"usefulness_mean":sum(r["usefulness"] for r in human)/len(human) if human else None,
                "human_rated":len(human),"assistant_rated":len(assistant),
                "assistant_usefulness_mean":sum(r["usefulness"] for r in assistant)/len(assistant) if assistant else None,
                "reused_services":sum(v>1 for v in counts.values()),
                "notice":"仅统计本机明确选用并关联实际交易的记录，不参与公开信用评分。"}
