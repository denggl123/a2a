"""Application adapter from existing books to normalized assessment facts.

Only this adapter knows ledger/card schemas. Selection rules receive neither the
books nor a transport. Projection writes are independent of scoring and trading.
"""
from __future__ import annotations

import hmac
import hashlib
import secrets
import time

from .pricing import price_book, quote_card
from .experience import validate_subject
from .selection.contracts import digest, metric
from .selection.metrics import personalized, credit, network, execution

TRACKED = {"trade_facts", "feedback", "feedback_received", "feedback_inbox_conflicts",
           "experience_publications", "experience_publication_versions", "experience_cache",
           "experience_conflicts", "experience_queries", "disputes", "resolution_messages",
           "resolution_agreements", "execution_observations", "bindings", "projections", "task_reviews"}


def subjects(record):
    if not isinstance(record, dict):
        return []
    record = record.get("record", record)
    provider, sid = record.get("provider_did"), record.get("service_id")
    result = []
    if provider:
        result.append({"kind": "provider", "provider_did": provider})
        if sid:
            result.append({"kind": "service", "provider_did": provider, "service_id": sid})
    buyer = record.get("buyer_did")
    if record.get("direction") == "seller_to_buyer":
        buyer = record.get("counterparty_did")
    if buyer:
        result.append({"kind": "buyer", "buyer_did": buyer})
    return result


class SelectionFactAdapter:
    def validate_choice(self, scope, candidate_key):
        row = self.store.get("projections", scope)
        extension = (row or {}).get("network_card", {}).get("x-a2n", {}).get("projection", {})
        if extension.get("node_did", "")+"|"+extension.get("service_id", "") != candidate_key:
            raise ValueError("SELECTION_CHOICE_PROVIDER_MISMATCH")

    def choice_outcome(self, choice):
        from .trade_facts import digest as trade_digest
        uid = self.store.get("trade_fact_index", trade_digest([choice["scope"],choice["task_id"]]))
        fact = self.store.get("trade_facts",uid) if uid else None
        if not fact or not fact.get("relation_verified") or fact.get("buyer_did") != self.node_did:
            return {"execution":"WAITING", "usefulness":None, "label_source":None}
        reviews=[r for r in self.store.items("task_reviews").values() if r["trade_uid"]==uid and not r["withdrawn"]]
        review=max(reviews,key=lambda r:r["label_at"]) if reviews else {}
        return {"execution":fact["execution"],"trade_uid":uid,"usefulness":review.get("human",{}).get("usefulness"),
                "label_source":review.get("label_source","HUMAN") if review else None}

    def __init__(self, store, *, node_did, coordination, feedback, experience,
                 reputation, policies, route_observations=lambda: [], task_quality=None, now=None):
        self.store, self.node_did, self.coordination = store, node_did, coordination
        self.feedback, self.experience = feedback, experience
        self.reputation, self.policies = reputation, policies
        self.route_observations = route_observations
        self.task_quality = task_quality
        self.now = now or time.time
        secret = store.get("selection_settings", "index_key")
        if not secret:
            secret = secrets.token_hex(32)
            store.put("selection_settings", "index_key", secret)
        self._index_secret = bytes.fromhex(secret)
        self._bootstrap = True
        self._next_expiry = 0
        self._cycle_at = self.now()
        self.store.track_changes(TRACKED)

    def _key(self, subject):
        return hmac.new(self._index_secret, digest(subject).encode(), hashlib.sha256).hexdigest()

    def reset_network_context(self, now):
        """Invalidate earlier measurements after an explicit local environment change."""
        self._cycle_at = now
        return {"context_changed_at": now, "state":"WAITING_FOR_NEW_OBSERVATIONS"}

    def _known_subjects(self):
        found = {}
        for ns in ("trade_facts", "experience_publications", "experience_cache", "selection_inputs"):
            for record in self.store.items(ns).values():
                for subject in ([record["subject"]] if ns == "selection_inputs" else subjects(record)):
                    found[self._key(subject)] = subject
        return found

    def _event_subjects(self, event):
        ns, key = event["namespace"], event["key"]
        record = self.store.get(ns, key)
        if ns in {"feedback", "feedback_received"}:
            return subjects(record)
        if ns in {"disputes", "resolution_messages", "resolution_agreements"}:
            uid = (record or {}).get("trade_uid") or ((record or {}).get("proposal") or {}).get("trade_uid")
            return subjects(self.store.get("trade_facts", uid or ""))
        if ns == "execution_observations" and record:
            uid = self.store.get("trade_fact_index", digest([record["scope"], record["task_id"]]))
            return subjects(self.store.get("trade_facts", uid or ""))
        if ns in {"bindings", "projections", "experience_queries", "experience_conflicts", "feedback_inbox_conflicts"} or record is None:
            return list(self._known_subjects().values())
        return subjects(record)

    def drain(self, limit=10):
        """Bounded durable projection. No remote access and no policy enforcement."""
        now = self.now()
        with self.store.tx():
            state = self.store.get("selection_projection", "cursor", {"sequence": 0})
            events = self.store.changes(state["sequence"], limit=256)
            pending = {}
            if self._bootstrap or now >= self._next_expiry:
                pending.update(self._known_subjects())
                self._bootstrap = False
                self._next_expiry = now + 3600
            for event in events:
                for subject in self._event_subjects(event):
                    pending[self._key(subject)] = subject
            generation = events[-1]["sequence"] if events else state["sequence"]
            for key, subject in pending.items():
                self.store.put("projection_jobs", key, {"subject": subject, "generation": generation})
            self.store.put("selection_projection", "cursor", {"sequence": generation, "scheduled_at": now})
            self.store.prune_changes(generation)
        done = 0
        for key, job in list(self.store.items("projection_jobs").items())[:limit]:
            with self.store.tx():
                inputs = self._project(job["subject"], now)
                inputs.update({"subject": job["subject"], "generation": job["generation"], "projected_at": now})
                self.store.put("selection_inputs", key, inputs)
                current = self.store.get("projection_jobs", key)
                if current and current["generation"] == job["generation"]:
                    self.store.delete("projection_jobs", key)
                self.reputation.rebuild(job["subject"], command_id="projection-" + digest([key, job["generation"], int(now//3600)]))
            done += 1
        return {"processed": done, "pending": self.store.count("projection_jobs"), "sequence": generation}

    def _project(self, subject, now):
        ledger = self.store.items("trade_facts")
        kind = subject["kind"]
        def relevant(f):
            return (f.get("relation_verified") is True and not f.get("known_self") and
                    (f.get("buyer_did") == subject.get("buyer_did") if kind == "buyer" else
                     f.get("provider_did") == subject.get("provider_did") and
                     (kind != "service" or f.get("service_id") == subject.get("service_id"))))
        facts = {uid: f for uid, f in ledger.items() if relevant(f)}
        telemetry = {(r["scope"], r["task_id"]): r for r in self.store.items("execution_observations").values()}
        own = []
        rows = list(self.store.items("feedback").values()) + list(self.store.items("feedback_received").values())
        for r in rows:
            if r.get("conflict") or not r.get("proof") or not self.feedback.verify_record(r):
                continue
            if r.get("source") == "counterparty" and not r.get("verified"):
                continue
            direction = "seller_to_buyer" if kind == "buyer" else "buyer_to_seller"
            if r.get("direction") != direction or r.get("author_did") != self.node_did:
                continue
            context = self.store.get("feedback_contexts", r["feedback_id"]) or {}
            matches = [f for f in facts.values() if f.get("provider_did") == r.get("provider_did")
                       and f.get("service_id") == r.get("service_id")
                       and (f.get("task_id") == context.get("task_id") and f.get("scope") == context.get("scope")
                            or r.get("task_id") in {f.get("task_id"), f.get("wire_task_id")})]
            for f in matches[:1]:
                expected_author = f["provider_did"] if kind == "buyer" else f["buyer_did"]
                expected_target = f["buyer_did"] if kind == "buyer" else f["provider_did"]
                if expected_author != r["author_did"] or expected_target != r.get("counterparty_did"):
                    continue
                own.append({"trade_uid": f["trade_uid"], "author_did": r["author_did"],
                    "at": f.get("delivered_at") or f["admitted_at"], "revision": r["revision"],
                    "ref": r["digest"], "version": f.get("version", ""),
                    "skill": telemetry.get((f["scope"], f["task_id"]), {}).get("skill"),
                    "dimensions": {k: v for k,v in r["dimensions"].items() if k != "quality" or f["execution"] == "DELIVERED"},
                    "execution": f["execution"]})
        external = []
        cache_expiry = {digest(r["record"]): r.get("expires_at", 0) for r in self.store.items("experience_cache").values()}
        for v in self.experience.latest(subject):
            r = v["record"]
            direction = "seller_to_buyer" if kind == "buyer" else "buyer_to_seller"
            if not v["eligible"] or r["author_did"] == self.node_did or r["direction"] != direction:
                continue
            external.append({"trade_uid": r["trade_uid"], "author_did": r["author_did"],
                "at": r["trade_at"], "revision": r["publication_revision"], "ref": digest(r),
                "version": r["service_version"], "dimensions": r["dimensions"],
                "execution": r["trade_anchor"]["execution"], "expires_at":cache_expiry.get(digest(r), now+30*86400)})
        observations = []
        for row in telemetry.values():
            f = next((f for f in facts.values() if f["scope"] == row["scope"] and f["task_id"] == row["task_id"]), None)
            if not f or f.get("role") != "buyer":
                continue
            state = f.get("execution", "UNKNOWN")
            stage = row.get("stage")
            origin = "network" if stage == "transport" else "local" if stage == "persistence" else "unknown"
            observations.append({**row, "trade_uid": f["trade_uid"], "execution": state if state in {"DELIVERED", "FAILED", "CANCELED"} else "UNKNOWN",
                "version": f.get("version", ""), "failure_origin": origin,
                "no_useful_result": row.get("first_useful_ms") is None})
        breaches = []
        for agreement in self.store.items("resolution_agreements").values():
            proposal = agreement.get("proposal") or {}
            uid = proposal.get("trade_uid")
            if uid not in facts or agreement.get("state") != "BOTH_ACCEPTED":
                continue
            # A signed CLOSE is not a successful remedy. Unknown/non-executed stays unknown.
            if agreement.get("execution") not in {"REWORK_DELIVERED", "REFUNDED", "REFUND_CONFIRMED"}:
                continue
            f = facts[uid]
            breaches.append({"trade_uid": uid, "author_did": f["buyer_did"] if kind != "buyer" else f["provider_did"],
                             "at": f["admitted_at"], "value": 0, "ref": digest(agreement)})
        disputed = {r.get("trade_uid") for r in self.store.items("disputes").values()}
        queries = [q for q in self.store.items("experience_queries").values() if q.get("subject") == subject]
        query = max(queries, key=lambda q: q["created_at"], default={})
        previous = self._inputs(subject)
        completed = query.get("known_sources_complete", False)
        prior_refs = previous.get("qualified_negative_refs", [])
        complete_at = query.get("created_at", 0) if completed else previous.get("negative_complete_at", 0)
        eligible_refs = {r["ref"] for r in external}
        qualified_refs = sorted(eligible_refs if completed else eligible_refs & set(prior_refs))
        if now >= complete_at + 30*86400:
            qualified_refs = []
        return {"local": own, "external": external, "execution": observations,
                "breaches": breaches, "dispute_count": len(set(facts)&disputed), "observed_trades": len(facts),
                "qualified_negative_refs": qualified_refs, "negative_complete_at": complete_at,
                "coverage": {"known_sources_complete": completed, "global": "UNKNOWN",
                             "latest_query_state": query.get("state", "NONE"),
                             "retained_complete_evidence": bool(qualified_refs) and not completed}}

    @staticmethod
    def _ratings(records, dimension, *, version=None, negative=False):
        result = []
        for r in records:
            rating = r["dimensions"].get(dimension)
            if rating is None or version is not None and r["version"] != version:
                continue
            result.append({k:v for k,v in r.items() if k in {"trade_uid", "author_did", "at", "revision", "ref"}} | {
                "value": max(0, (3-rating)/2) if negative else (rating-1)/4})
        return result

    def _inputs(self, subject):
        return self.store.get("selection_inputs", self._key(subject), {"local": [], "external": [],
            "execution": [], "breaches": [], "generation": 0, "coverage": {"known_sources_complete": False, "global": "UNKNOWN"}})

    def _credit(self, subject, now):
        owner = self._inputs(subject)
        current_external = [r for r in owner["external"] if r.get("expires_at", now+1) > now]
        base = personalized(self._ratings(owner["local"], "honoring"), self._ratings(current_external, "honoring"), now=now)
        eligible = set(owner.get("qualified_negative_refs", []))
        external = [r for r in current_external if r["ref"] in eligible]
        c = credit(base, owner["breaches"], self._ratings(owner["local"], "dispute_handling", negative=True), now=now,
                   public_handling=self._ratings(external, "dispute_handling", negative=True),
                   complete=bool(eligible))
        c["raw"].update({"known_disputes": owner.get("dispute_count", 0), "observed_trades": owner.get("observed_trades", 0),
                         "global_dispute_rate": None})
        if owner["coverage"].get("retained_complete_evidence") and external:
            c["status"] = "LIMITED" if c["value"] is not None else "UNKNOWN"
            c["reasons"].append("LAST_COMPLETE_EVIDENCE_RETAINED_SOURCE_INCOMPLETE")
        c["valid_until"] = min([now+30]+[r["expires_at"] for r in current_external if "expires_at" in r])
        return c

    def subject_view(self, subject, now):
        subject = validate_subject(subject)
        inputs = self._inputs(subject)
        return {"subject": subject, "as_of": inputs.get("projected_at"),
                "generation": inputs["generation"], "coverage": inputs["coverage"],
                "credit": self._credit(subject, now), "algorithm": "a2n-selection/1"}

    def _metrics(self, candidate, task, profile, now):
        subject = {"kind": "service", "provider_did": candidate["provider_did"], "service_id": candidate["service_id"]}
        inputs = self._inputs(subject)
        local, external = inputs["local"], inputs["external"]
        external = [r for r in external if r.get("expires_at", now+1) > now]
        if task.get("skill"):
            local = [r for r in local if not r.get("skill") or r["skill"] == task["skill"]]
        own_quality = self._ratings(local, "quality", version=candidate["version"])
        public_quality = self._ratings(external, "quality", version=candidate["version"])
        quality = personalized(own_quality, public_quality, now=now)
        if len(candidate["skills"]) > 1 and (public_quality or any(
                "quality" in r["dimensions"] and not r.get("skill") for r in local)):
            quality["reasons"].append("SERVICE_LEVEL_QUALITY_TASK_SCOPE_UNCONFIRMED")
            quality["status"] = "LIMITED" if quality["value"] is not None else "UNKNOWN"
        quality["raw"]["scope"] = "service_version_and_skill" if task.get("skill") and own_quality and not public_quality else "service_version"
        if task.get("rubric"):
            quality = metric(reasons=["RUBRIC_SPECIFIC_QUALITY_UNKNOWN"], raw={"requested_rubric":task["rubric"]})
            if self.task_quality is not None:
                try:
                    quality = self.task_quality.metric(candidate["provider_did"], candidate["service_id"],
                                                       candidate["version"], task, now)
                except ValueError as exc:
                    if str(exc) not in {"TASK_RUBRIC_NOT_FOUND", "INVALID_TASK_RUBRIC_REFERENCE"}:
                        raise
        quality["valid_until"] = min([now+30]+[r["expires_at"] for r in external if "expires_at" in r])
        c = self._credit({"kind": "provider", "provider_did": candidate["provider_did"]}, now)
        rows = [r for r in inputs["execution"] if r["version"] == candidate["version"]
                and (not task.get("skill") or r["skill"] == task["skill"])
                and (not task.get("workload_bucket") or r["workload_bucket"] == task["workload_bucket"])]
        result = {"quality": quality, "credit": c, **execution(rows, now=now, anchors=profile["anchors"])}
        if not task.get("workload_bucket") and len({r["workload_bucket"] for r in rows}) > 1:
            result["time"] = metric(reasons=["CHOOSE_WORKLOAD_FOR_COMPARABLE_TIMING"])
        return result

    def snapshot(self, search_id, result_revision, task, profile, now):
        if not isinstance(task, dict):
            raise ValueError("INVALID_SELECTION_TASK")
        items, cursor, revision = [], "", None
        while True:
            page = self.coordination.candidates(search_id, result_cursor=cursor, limit=100)
            if revision is not None and revision != page["result_revision"]:
                raise ValueError("RESULT_CHANGED")
            revision = page["result_revision"]
            items.extend(page["items"])
            cursor = page["next_result_cursor"]
            if not cursor:
                break
            if len(items) >= 4096:
                raise ValueError("SELECTION_CANDIDATE_LIMIT")
        if result_revision is not None and result_revision != revision:
            raise ValueError("RESULT_CHANGED")
        session = self.coordination.get(search_id).to_dict()
        if session["result_revision"] != revision:
            raise ValueError("RESULT_CHANGED")
        task = {"skill": session.get("spec", {}).get("skill", ""), **task} if "spec" in session else task
        # A separately obtained descriptor never causes a probe here.
        saved = self.store.get("coord_searches", search_id) or {}
        if not task.get("skill"):
            task["skill"] = (saved.get("spec") or {}).get("skill", "")
        normalized, metrics = [], {}
        valid_until = now+30
        monitored = self.route_observations()
        with self.store.tx():
            projections = self.store.items("projections")
            for item in items:
                if not item.get("cards"):
                    continue
                card = item["cards"][-1]["card"]
                c = self._candidate(item, card, task)
                normalized.append(c)
                m = self._metrics(c, task, profile, now)
                route_scores = []
                for route in item.get("routes", []):
                    obs = (saved.get("observations") or {}).get(route["route_id"])
                    if route.get("expires_at", 0) <= now or not obs or obs.get("at", 0) < self._cycle_at:
                        continue
                    if obs.get("measured_by") != self.node_did:
                        continue
                    route_scores.append(network([{"checked_at": obs["at"], "reachable": obs.get("control_reachable"),
                        "rtt_ms": obs.get("rtt_ms"), "kind": obs.get("probe_kind", "control")}], now=now, anchors=profile["anchors"]))
                for observation in monitored:
                    p = projections.get(observation["target"], {})
                    ext = (p.get("network_card", {}).get("x-a2n") or {}).get("projection", {})
                    if ext.get("node_did") == c["provider_did"] and ext.get("service_id") == c["service_id"]:
                        rows = [{**r, "kind": observation["kind"]} for r in observation.get("history", []) if r["checked_at"] >= self._cycle_at]
                        route_scores.append(network(rows, now=now, anchors=profile["anchors"]))
                m["network"] = max((v for v in route_scores if v["value"] is not None),
                    key=lambda v: (v["status"] != "STALE", v["value"]), default=metric())
                m["network"]["valid_until"] = min(now+30, m["network"]["raw"].get("latest_at", now)+120)
                valid_until = min([valid_until]+[v.get("valid_until", now+30) for v in m.values()])
                metrics[c["key"]] = m
            head = self.store.change_head()
        return {"candidates": normalized, "metrics": metrics, "task": task, "result_revision": revision,
                "search_started_at": saved.get('created_at'),
                "metrics_revision": digest([head, metrics]),
                "valid_until": valid_until, "coverage": {"global": "UNKNOWN", "projection_pending": self.store.count("projection_jobs")}}

    def _candidate(self, item, card, task):
        def mapping(value):
            return value if isinstance(value, dict) else {}
        def strings(value):
            return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
        ext = mapping(card.get("x-a2n"))
        points = mapping(ext.get("points"))
        modes = [m for m in strings(points.get("modes")) if m in {"EARN", "DEBT", "PAY"}] if points.get("enabled") is True else []
        methods = []
        aliases = {"a2n-points/1": "points", "evm-native/1": "native", "x402/2": "x402"}
        declarations = strings(card.get("accepts")) + strings(ext.get("accepts"))
        payment_methods = mapping(ext.get("payments")).get("methods", [])
        declarations += [p.get("method") for p in payment_methods if isinstance(p, dict)] if isinstance(payment_methods, list) else []
        for name in declarations:
            if isinstance(name, str):
                mapped = aliases.get(name, name)
                if mapped != "points":
                    methods.append(mapped)
        if modes or not points and any(aliases.get(x, x) == "points" for x in declarations if isinstance(x, str)):
            methods.append("points")
        costs = []
        try:
            for currency, entries in price_book(card).get(task.get("skill", ""), {}).items():
                if all(e["key"] == "call_count" for e in entries):
                    quote = quote_card(card, task.get("skill", ""), currency, {"call_count": 1})
                    costs.append({"currency": currency, "amount_minor": quote["amount_minor"],
                                  "complete": True, "basis": "DECLARED_PER_CALL_ESTIMATE"})
        except (TypeError, ValueError, KeyError, AttributeError):
            pass
        # Points-enabled services have their own conditions: a zero money tag is not free.
        if modes:
            costs = [c for c in costs if c["amount_minor"] > 0]
            amount = points.get("amount")
            if isinstance(amount, int) and not isinstance(amount, bool) and 0 <= amount < 2**256:
                for mode in modes:
                    costs.append({"currency": "points:"+item["key"]["provider_did"],
                        "amount_minor": 0 if mode == "EARN" else amount, "complete": True,
                        "mode": mode, "basis": "DECLARED_POINTS_CONDITION"})
        key = item["key"]
        policy_subject = {"kind": "service", **key}
        opportunity = self.policies.opportunity(policy_subject, persist=False) or {"effective_state":"NORMAL"}
        owner_opportunity = self.policies.opportunity({"kind": "provider", "provider_did": key["provider_did"]}, persist=False) or {"effective_state":"NORMAL"}
        effective = opportunity["effective_state"]
        if owner_opportunity["effective_state"] in {"BLOCKED_LOCAL", "MANUAL_ONLY"}:
            effective = owner_opportunity["effective_state"]
        skills = [s for s in card.get("skills", []) if isinstance(s, dict)] if isinstance(card.get("skills"), list) else []
        selection = mapping(ext.get("selection"))
        return {"key": key["provider_did"] + "|" + key["service_id"], **key,
                "description": str(card.get("name", ""))+" "+str(card.get("description", "")),
                "version": str(card.get("version", "")), "skills": sorted({str(s["id"]) for s in skills if s.get("id")}),
                "tags": sorted({t for s in skills for t in strings(s.get("tags"))}),
                "input_modes": sorted(set(strings(card.get("defaultInputModes")) + [m for s in skills for m in strings(s.get("inputModes"))])),
                "output_modes": sorted(set(strings(card.get("defaultOutputModes")) + [m for s in skills for m in strings(s.get("outputModes"))])),
                "languages": strings(selection.get("languages")), "styles": strings(selection.get("styles")),
                "methods": sorted(set(methods)), "point_modes": modes, "costs": costs,
                "verified": item.get("verification") == "CARD_VERIFIED",
                "opportunity": effective, "available": None}
