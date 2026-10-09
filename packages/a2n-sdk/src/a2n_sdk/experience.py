"""Public experience objects, provenance, revisions and bounded snapshots.

Original private feedback never leaves this module through an implicit projection.
Identity and transport are injected by the node; no crypto or network dependency.
"""
from __future__ import annotations

import base64
import math
import secrets
import re
import time

from .feedback import DIMENSIONS
from .feedback_schema import dimension_names
from .privacy import scrub_text
from .trade_facts import digest

PUBLIC_NS = "experience_publications"
VERSION_NS = "experience_publication_versions"
CACHE_NS = "experience_cache"
CONFLICT_NS = "experience_conflicts"
PUBLIC_VERSION = "a2n-public-feedback/1"
ANCHOR_VERSION = "a2n-trade-anchor/1"
AUTH_VERSION = "a2n-trade-authorization/1"
MAX_RECORDS = 2000


def unsigned(record):
    return {k: v for k, v in record.items() if k != "proof"}


def signed(core, signer):
    return {**core, "proof": signer(core)}


def matches(record, subject):
    kind = subject.get("kind")
    if kind == "service":
        return (record.get("provider_did") == subject.get("provider_did")
                and record.get("service_id") == subject.get("service_id"))
    if kind == "provider":
        return record.get("provider_did") == subject.get("provider_did") and record.get("direction") == "buyer_to_seller"
    if kind == "buyer":
        return record.get("counterparty_did") == subject.get("buyer_did") and record.get("direction") == "seller_to_buyer"
    return False


def validate_subject(subject):
    if not isinstance(subject, dict):
        raise ValueError("查询主体必须是对象")
    kind = subject.get("kind")
    keys = {"service": {"kind", "provider_did", "service_id"},
            "provider": {"kind", "provider_did"}, "buyer": {"kind", "buyer_did"}}.get(kind)
    if keys is None or set(subject) != keys or any(not isinstance(v, str) or not v or len(v) > 256 for v in subject.values()):
        raise ValueError("查询主体必须明确绑定商品、卖方或买方")
    return dict(subject)


class ExperienceBook:
    def __init__(self, store, feedback, *, signer, verifier, now=None, cache_enabled=False):
        self.store, self.feedback = store, feedback
        self.signer, self.verifier = signer, verifier
        self.now = now or time.time
        self.cache_enabled = cache_enabled

    def verify(self, record):
        try:
            return isinstance(record, dict) and self.verifier(record.get("proof"), unsigned(record))
        except (ValueError, TypeError, KeyError):
            return False

    def validate_anchor(self, anchor):
        if not isinstance(anchor, dict) or anchor.get("v") != ANCHOR_VERSION or not self.verify(anchor):
            raise ValueError("UNVERIFIED_TRADE_ANCHOR")
        if set(anchor) != {"v", "author_did", "provider_did", "buyer_did", "service_id",
                           "service_version", "trade_uid", "execution", "admitted_at", "observed_at",
                           "buyer_authorization", "proof"}:
            raise ValueError("INVALID_ANCHOR_FIELDS")
        authorization = anchor.get("buyer_authorization")
        self.validate_authorization(authorization)
        if (anchor.get("author_did") != anchor.get("provider_did")
                or authorization.get("author_did") != anchor.get("buyer_did")
                or authorization.get("buyer_did") != anchor.get("buyer_did")):
            raise ValueError("TRADE_ROLE_MISMATCH")
        for key in ("provider_did", "service_id", "trade_uid"):
            if not anchor.get(key) or authorization.get(key) != anchor.get(key):
                raise ValueError("TRADE_BINDING_MISMATCH")
        if anchor.get("execution") not in {"DELIVERED", "FAILED", "CANCELED", "UNKNOWN", "RUNNING"}:
            raise ValueError("INVALID_EXECUTION_FACT")
        at = anchor.get("admitted_at")
        issue, expiry = authorization.get("issued_at"), authorization.get("expires_at")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in (at, issue, expiry, anchor.get("observed_at"))):
            raise ValueError("INVALID_TRADE_TIME")
        if not issue - 30 <= at < expiry:
            raise ValueError("AUTHORIZATION_NOT_VALID_AT_ADMISSION")
        return anchor

    def validate_authorization(self, authorization):
        if not isinstance(authorization, dict) or authorization.get("v") != AUTH_VERSION or not self.verify(authorization):
            raise ValueError("UNVERIFIED_BUYER_AUTHORIZATION")
        if set(authorization) != {"v", "author_did", "buyer_did", "provider_did", "service_id",
                                   "trade_uid", "issued_at", "expires_at", "proof"}:
            raise ValueError("INVALID_AUTHORIZATION_FIELDS")
        for key in ("author_did", "buyer_did", "provider_did", "service_id"):
            if not isinstance(authorization[key], str) or not 1 <= len(authorization[key]) <= 256:
                raise ValueError("INVALID_AUTHORIZATION_IDENTITY")
        if not isinstance(authorization["trade_uid"], str) or not re.fullmatch(r"tr_[0-9a-f]{64}", authorization["trade_uid"]):
            raise ValueError("INVALID_TRADE_UID")
        issue, expiry = authorization["issued_at"], authorization["expires_at"]
        if (any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (issue, expiry))
                or not 0 < expiry - issue <= 600):
            raise ValueError("INVALID_AUTHORIZATION_PERIOD")
        return authorization

    def validate(self, record):
        if (not isinstance(record, dict) or record.get("v") not in {PUBLIC_VERSION, "a2n-public-feedback/2"}
                or not self.verify(record)):
            raise ValueError("UNVERIFIED_PUBLIC_FEEDBACK")
        if set(record) != {"v", "feedback_id", "feedback_revision", "publication_revision",
                          "previous_publication_hash", "author_did", "direction", "counterparty_did",
                          "provider_did", "service_id", "service_version", "trade_uid", "trade_at",
                          "trade_anchor", "visibility", "dimensions", "note", "redactions",
                          "original_commitment", "published_at", "proof"}:
            raise ValueError("INVALID_PUBLIC_FEEDBACK_FIELDS")
        revision = record.get("publication_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 10000:
            raise ValueError("INVALID_PUBLICATION_REVISION")
        feedback_revision = record.get("feedback_revision")
        if isinstance(feedback_revision, bool) or not isinstance(feedback_revision, int) or not 1 <= feedback_revision <= 10000:
            raise ValueError("INVALID_FEEDBACK_REVISION")
        for key in ("published_at", "trade_at"):
            number = record.get(key)
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number):
                raise ValueError("INVALID_PUBLICATION_TIME")
        if record["published_at"] > self.now() + 30 or record["trade_at"] > record["published_at"] + 30:
            raise ValueError("FUTURE_PUBLICATION")
        for key in ("original_commitment", "previous_publication_hash"):
            value = record.get(key)
            if key == "previous_publication_hash" and revision == 1 and value == "":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("INVALID_PUBLICATION_HASH")
        redactions = record.get("redactions")
        if not isinstance(redactions, list) or len(redactions) > 32 or any(not isinstance(v, str) or len(v) > 64 for v in redactions):
            raise ValueError("INVALID_REDACTIONS")
        if record.get("visibility") not in {"PUBLIC", "WITHDRAWN"}:
            raise ValueError("INVALID_VISIBILITY")
        anchor = self.validate_anchor(record.get("trade_anchor"))
        if (record.get("trade_at") != anchor["observed_at"]
                or record.get("service_version") != anchor["service_version"]):
            raise ValueError("FEEDBACK_ANCHOR_MISMATCH")
        buyer_direction = record.get("direction") == "buyer_to_seller"
        if record.get("direction") not in DIMENSIONS:
            raise ValueError("INVALID_DIRECTION")
        expected_author = anchor["buyer_did"] if buyer_direction else anchor["provider_did"]
        expected_other = anchor["provider_did"] if buyer_direction else anchor["buyer_did"]
        if record.get("author_did") != expected_author or record.get("counterparty_did") != expected_other:
            raise ValueError("FEEDBACK_ROLE_MISMATCH")
        for key in ("provider_did", "service_id", "trade_uid"):
            if record.get(key) != anchor.get(key):
                raise ValueError("FEEDBACK_TRADE_MISMATCH")
        dimensions = record.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) - set(dimension_names(record["direction"], record["v"])):
            raise ValueError("INVALID_DIMENSIONS")
        if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5 for v in dimensions.values()):
            raise ValueError("INVALID_DIMENSION_VALUE")
        if "quality" in dimensions and anchor["execution"] != "DELIVERED":
            raise ValueError("NO_DELIVERY_QUALITY")
        if not isinstance(record.get("note"), str) or len(record["note"]) > 500:
            raise ValueError("INVALID_PUBLIC_NOTE")
        if not isinstance(record.get("feedback_id"), str) or not 1 <= len(record["feedback_id"]) <= 128:
            raise ValueError("INVALID_FEEDBACK_ID")
        return record

    def publish(self, feedback_id, *, visibility="PUBLIC", public_note=""):
        if visibility not in {"PUBLIC", "PARTIES_ONLY"}:
            raise ValueError("公开范围只能是 PUBLIC / PARTIES_ONLY")
        if not isinstance(public_note, str) or len(public_note) > 500:
            raise ValueError("公开短评不能超过 500 字")
        with self.store.tx():
            feedback = self.feedback.get(feedback_id)
            if not feedback or not feedback.get("proof") or not self.feedback.verify_record(feedback):
                raise ValueError("只能发布本机作者的有效签名反馈")
            previous = self.store.get(PUBLIC_NS, feedback_id)
            if visibility == "PARTIES_ONLY" and not previous:
                return {"feedback_id": feedback_id, "visibility": "PARTIES_ONLY", "published": False}
            context = self.store.get("feedback_contexts", feedback_id) or {}
            task = self.store.task(context.get("scope", ""), feedback.get("task_id", "")) or {}
            anchor = ((task.get("outcome") or {}).get("metadata") or {}).get("public_trade_anchor")
            if not anchor and previous:
                anchor = previous["trade_anchor"]
            self.validate_anchor(anchor)
            local_uid = ((task.get("outcome") or {}).get("metadata") or {}).get("trade_uid")
            if local_uid and local_uid != anchor["trade_uid"]:
                raise ValueError("PUBLICATION_LOCAL_TRADE_MISMATCH")
            removed = []
            note = scrub_text(public_note, removed)
            public_visibility = "WITHDRAWN" if visibility == "PARTIES_ONLY" else "PUBLIC"
            dimensions = feedback["dimensions"] if public_visibility == "PUBLIC" else {}
            if previous and previous["visibility"] == public_visibility and previous["dimensions"] == dimensions and previous["note"] == note:
                return previous
            salt = self.store.get("experience_commitment_salts", feedback_id) or secrets.token_hex(32)
            core = {"v": "a2n-public-feedback/2" if feedback.get("v") == "a2n-feedback/2" else PUBLIC_VERSION, "feedback_id": feedback_id,
                "feedback_revision": feedback["revision"],
                "publication_revision": int((previous or {}).get("publication_revision", 0)) + 1,
                "previous_publication_hash": digest(previous) if previous else "",
                "author_did": feedback["author_did"], "direction": feedback["direction"],
                "counterparty_did": feedback["counterparty_did"],
                "provider_did": feedback["provider_did"], "service_id": feedback["service_id"],
                "service_version": anchor.get("service_version", ""), "trade_uid": anchor["trade_uid"],
                "trade_at": anchor["observed_at"], "trade_anchor": anchor,
                "visibility": public_visibility, "dimensions": dict(dimensions), "note": note,
                "redactions": removed, "original_commitment": digest([salt, feedback]),
                "published_at": self.now()}
            record = signed(core, self.signer)
            self.validate(record)
            self.store.put("experience_commitment_salts", feedback_id, salt)
            self.store.put(PUBLIC_NS, feedback_id, record)
            self.store.put(VERSION_NS, f"{feedback_id}:{record['publication_revision']}", record)
            return record

    def ingest(self, record, *, source, source_complete=True):
        self.validate(record)
        key = digest([record["author_did"], record["feedback_id"]])
        version_key = f"{key}:{record['publication_revision']}"
        with self.store.tx():
            existing = self.store.get(CACHE_NS, version_key)
            if existing and digest(existing["record"]) != digest(record):
                self.store.put(CONFLICT_NS, key, {"reason": "SIGNED_REVISION_CONFLICT", "at": self.now()})
                self.store.put("experience_conflict_records", digest(record), record)
                return {"conflict": True, "accepted": False}
            if not existing and self.store.count(CACHE_NS) >= MAX_RECORDS:
                raise ValueError("EXPERIENCE_CACHE_FULL")
            sources = list((existing or {}).get("sources") or [])
            if source not in sources:
                sources.append(str(source)[:256])
            self.store.put(CACHE_NS, version_key, {"record": record, "sources": sources[:32],
                "observed_at": self.now(), "expires_at": self.now() + 30 * 86400 if source_complete else
                (existing or {}).get("expires_at", 0)})
        return {"accepted": True, "idempotent": bool(existing)}

    def confirm_source_page_set(self, records):
        with self.store.tx():
            for record in records:
                key = digest([record["author_did"], record["feedback_id"]])
                version_key = f"{key}:{record['publication_revision']}"
                cached = self.store.get(CACHE_NS, version_key)
                if cached and digest(cached["record"]) == digest(record):
                    self.store.put(CACHE_NS, version_key, {**cached,
                        "expires_at": self.now() + 30 * 86400, "last_complete_fetch": self.now()})

    def records(self, subject=None, *, public=False):
        if subject is not None:
            validate_subject(subject)
        raw = [(r, "author", True) for r in self.store.items(VERSION_NS).values()]
        if not public or self.cache_enabled:
            raw.extend((r["record"], "cache", r.get("expires_at", 0) > self.now())
                       for r in self.store.items(CACHE_NS).values())
        unique = {}
        for record, source, fresh in raw:
            if subject and not matches(record, subject):
                continue
            key = (record["author_did"], record["feedback_id"], record["publication_revision"])
            if key not in unique or source == "author":
                unique[key] = (record, source, fresh)
        return list(unique.values())

    def latest(self, subject):
        groups = {}
        for record, source, fresh in self.records(subject):
            groups.setdefault((record["author_did"], record["feedback_id"]), []).append((record, source, fresh))
        out = []
        for (author, fid), versions in groups.items():
            versions.sort(key=lambda x: x[0]["publication_revision"])
            chain_ok, previous, expected = True, None, 1
            for record, _, _ in versions:
                if record["publication_revision"] != expected or record["previous_publication_hash"] != (digest(previous) if previous else ""):
                    chain_ok = False
                expected = record["publication_revision"] + 1
                previous = record
            record, source, fresh = versions[-1]
            conflict = bool(self.store.get(CONFLICT_NS, digest([author, fid])))
            out.append({"record": record, "source": source, "fresh": fresh,
                        "chain_complete": chain_ok, "conflict": conflict,
                        "eligible": fresh and chain_ok and not conflict and record["visibility"] == "PUBLIC"})
        return out

    def page(self, subject, *, limit=50, cursor=""):
        validate_subject(subject)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit 必须是 1–50 的整数")
        rows = sorted((r for r, _, _ in self.records(subject, public=True)),
                      key=lambda r: (r["author_did"], r["feedback_id"], r["publication_revision"]))
        snapshot = digest([digest(r) for r in rows])
        offset = 0
        if cursor:
            try:
                import json
                data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if data["snapshot"] != snapshot or data["query"] != digest(subject) or data["limit"] != limit:
                    raise ValueError("RESULT_CHANGED")
                offset = data["offset"]
                if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(rows):
                    raise ValueError("CURSOR_EXPIRED")
            except (KeyError, TypeError, UnicodeError) as exc:
                raise ValueError("CURSOR_EXPIRED") from exc
        items = rows[offset:offset + limit]
        next_cursor = ""
        if offset + limit < len(rows):
            import json
            next_cursor = base64.urlsafe_b64encode(json.dumps({"snapshot": snapshot,
                "query": digest(subject), "limit": limit, "offset": offset + limit}).encode()).decode().rstrip("=")
        return {"items": items, "snapshot": snapshot, "next_cursor": next_cursor,
                "scope": "THIS_SOURCE_PUBLIC_RECORDS", "total": len(rows)}
