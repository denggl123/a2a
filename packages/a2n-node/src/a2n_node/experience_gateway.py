"""Authenticated, bounded public metadata; separate from coordination operations."""
from __future__ import annotations

import math
import threading
import time
import uuid

from a2n_sdk.experience import signed, unsigned, validate_subject
from a2n_sdk.projection import canonical_json

from .feedback_identity import signer_for, verifier_for
from .peer import ReplayGuard

VERSION = "a2n-experience/1"
PATHS = {"manifest": "MANIFEST", "summary": "SUMMARY", "feedback": "GET_FEEDBACK", "samples": "GET_SAMPLES"}
REQUEST_CAP = 16384
RESPONSE_CAP = 262144


def envelope(identity, op, target_did, body, *, request_id=None):
    now = time.time()
    return signed({"v": VERSION, "op": op, "request_id": request_id or uuid.uuid4().hex,
        "author_did": identity.did, "target_did": target_did, "issued_at": now,
        "expires_at": now + 30, "body": body}, signer_for(identity))


class PublicExperience:
    def __init__(self, identity, book, management):
        self.identity, self.book, self.management = identity, book, management
        self.guard = ReplayGuard()
        self._lock = threading.Lock()
        self._calls = []

    def _response(self, request, body, *, error=False):
        return signed({"v": VERSION, "op": "ERROR" if error else "RESULT",
            "request_id": request["request_id"], "author_did": self.identity.did,
            "target_did": request["author_did"], "issued_at": time.time(),
            "expires_at": time.time() + 30, "body": body}, signer_for(self.identity))

    def handle(self, path, request):
        if path not in PATHS:
            return 404, {"code": "UNSUPPORTED_OPERATION"}
        authenticated = False
        try:
            if (not isinstance(request, dict) or set(request) != {"v", "op", "request_id", "author_did",
                    "target_did", "issued_at", "expires_at", "body", "proof"}
                    or len(canonical_json(request).encode()) > REQUEST_CAP
                    or request["v"] != VERSION or request["op"] != PATHS[path]
                    or request["target_did"] != self.identity.did
                    or not verifier_for()(request.get("proof"), unsigned(request))):
                return 401, {"code": "UNVERIFIED_IDENTITY"}
            authenticated = True
            if (not isinstance(request["request_id"], str) or not 1 <= len(request["request_id"]) <= 128):
                raise ValueError("INVALID_REQUEST_ID")
            issue, expiry = request["issued_at"], request["expires_at"]
            if (any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in (issue, expiry))
                    or not 0 < expiry - issue <= 60 or not issue - 30 <= time.time() < expiry):
                return 400, self._response(request, {"code": "REQUEST_EXPIRED"}, error=True)
            ok, why = self.guard.check(request["request_id"], issue)
            if not ok:
                return 409, self._response(request, {"code": "REPLAYED_REQUEST", "message": why}, error=True)
            with self._lock:
                cutoff = time.monotonic() - 60
                self._calls = [(at, did) for at, did in self._calls if at >= cutoff]
                if len(self._calls) >= 128 or sum(did == request["author_did"] for _, did in self._calls) >= 32:
                    return 429, self._response(request, {"code": "RATE_LIMITED", "retry_after": 2}, error=True)
                self._calls.append((time.monotonic(), request["author_did"]))
            body = request["body"]
            if not isinstance(body, dict):
                raise ValueError("请求正文必须为对象")
            if path == "manifest":
                result = {"protocol": VERSION, "node_did": self.identity.did,
                          "operations": list(PATHS.values()), "request_cap": REQUEST_CAP,
                          "response_cap": RESPONSE_CAP, "max_page_size": 50,
                          "scope": "THIS_NODE_PUBLIC_PROJECTIONS"}
            else:
                subject = validate_subject(body.get("subject"))
                if path == "feedback":
                    result = self.book.page(subject, limit=body.get("limit", 50), cursor=body.get("cursor", ""))
                elif path == "summary":
                    rows = self.book.records(subject, public=True)
                    heads = {}
                    for record, _, _ in rows:
                        key = (record["author_did"], record["feedback_id"])
                        if record["publication_revision"] > heads.get(key, {}).get("publication_revision", 0):
                            heads[key] = record
                    result = {"subject": subject, "published_count": sum(r["visibility"] == "PUBLIC" for r in heads.values()),
                              "withdrawn_count": sum(r["visibility"] == "WITHDRAWN" for r in heads.values()),
                              "source_did": self.identity.did, "global_completeness": "UNKNOWN"}
                else:
                    if subject["kind"] != "service" or subject["provider_did"] != self.identity.did:
                        raise ValueError("本节点只提供自己商品的样品")
                    result = self.management.public_samples(subject["service_id"],
                        limit=body.get("limit", 10), cursor=body.get("cursor", ""), brief=body.get("brief", True))
            response = self._response(request, result)
            if len(canonical_json(response).encode()) > RESPONSE_CAP:
                return 413, self._response(request, {"code": "RESPONSE_TOO_LARGE"}, error=True)
            return 200, response
        except PermissionError:
            return 403, self._response(request, {"code": "PRIVATE_CONTENT"}, error=True)
        except (ValueError, TypeError, KeyError, AttributeError):
            return (400, self._response(request, {"code": "INVALID_REQUEST"}, error=True)) if authenticated else (401, {"code": "UNVERIFIED_IDENTITY"})


class ExperienceNetwork:
    def __init__(self, identity, coordination_network):
        self.identity, self.network = identity, coordination_network

    def fetch(self, source, subject, *, cursor="", limit=50, timeout=3, cap=RESPONSE_CAP):
        deadline = time.monotonic() + timeout
        base = str(source["endpoint"]).rstrip("/")
        target = source.get("node_did")
        used = 0
        operations = 1
        if not target:
            session, used = self.network.handshake({"endpoint": base + "/public/v1/coord"}, min(cap, 16384), timeout)
            target = session["node_did"]
            operations += 2
        request = envelope(self.identity, "GET_FEEDBACK", target, {"subject": subject, "cursor": cursor, "limit": limit})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("QUERY_DEADLINE_REACHED")
        if source.get("mailbox"):
            from .metadata_mailbox import exchange, verify_lease
            relay = source["mailbox"]
            found, size = exchange(self.network, self.identity, relay, "lookup", {"target_did": target},
                                   timeout=remaining, cap=min(16384, cap - used))
            used += size
            lease = found.get("lease")
            if (not verify_lease(lease) or lease["author_did"] != target
                    or lease["relay_did"] != relay["relay_did"] or lease["endpoint"] != relay["endpoint"]):
                raise ValueError("UNVERIFIED_TARGET_LEASE")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or cap - used <= 0:
                raise ValueError("QUERY_DEADLINE_REACHED")
            answer, count = exchange(self.network, self.identity, relay, "forward", {"request": request},
                                    timeout=remaining, cap=cap - used)
            response, status = answer.get("response"), answer.get("status")
            operations += 1
        else:
            response, count, status = self.network._request(base + "/public/v1/experience/feedback", request,
                                                           cap=max(1, cap - used), timeout=remaining)
        if (not isinstance(response, dict) or response.get("v") != VERSION
                or response.get("author_did") != target or response.get("target_did") != self.identity.did
                or response.get("request_id") != request["request_id"]
                or not verifier_for()(response.get("proof"), unsigned(response))):
            raise ValueError("UNVERIFIED_EXPERIENCE_RESPONSE")
        issue, expiry = response.get("issued_at"), response.get("expires_at")
        if (any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in (issue, expiry))
                or not 0 < expiry - issue <= 60 or not issue - 30 <= time.time() < expiry):
            raise ValueError("EXPIRED_EXPERIENCE_RESPONSE")
        if response.get("op") == "ERROR":
            raise ValueError(str((response.get("body") or {}).get("code") or "SOURCE_ERROR"))
        if status != 200 or response.get("op") != "RESULT":
            raise ValueError("INVALID_EXPERIENCE_RESPONSE")
        return {**response["body"], "source_did": target, "bytes": count + used, "operations": operations}
