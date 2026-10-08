"""Version 2 metadata mailboxes; isolated from the six coordination operations."""
from __future__ import annotations

from collections import deque
import math
import threading
import time
import uuid

from a2n_sdk.experience import signed, unsigned
from a2n_sdk.projection import canonical_json

from .experience_gateway import PATHS, VERSION as EXPERIENCE_VERSION
from .feedback_identity import signer_for, verifier_for
from .peer import ReplayGuard

VERSION = "a2n-metadata-mailbox/2"
RESOLUTION_VERSION = "a2n-resolution/1"
DOMAINS = [EXPERIENCE_VERSION, RESOLUTION_VERSION]
RESOLUTION_OPS = {"GET_CAPABILITIES", "DELIVER_SEALED", "GET_DELIVERY"}
LEASE_VERSION = "a2n-metadata-lease/2"
PREFIX = "/public/v2/metadata-mailbox/"
BODY_CAP = 196608
INNER_CAP = 131072
FIELDS = {"v", "op", "request_id", "author_did", "target_did", "issued_at", "expires_at", "body", "proof"}


def message(identity, op, target, body, request_id=None, *, version=VERSION):
    now = time.time()
    return signed({"v": version, "op": op, "request_id": request_id or uuid.uuid4().hex,
        "author_did": identity.did, "target_did": target, "issued_at": now,
        "expires_at": now + 30, "body": body}, signer_for(identity))


def verified(record, *, target="", version=VERSION):
    try:
        issue, expiry = record["issued_at"], record["expires_at"]
        return (set(record) == FIELDS and record["v"] == version
            and (not target or record["target_did"] == target)
            and isinstance(record["request_id"], str) and 1 <= len(record["request_id"]) <= 128
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (issue, expiry))
            and 0 < expiry - issue <= 60 and issue - 30 <= time.time() < expiry
            and verifier_for()(record["proof"], unsigned(record)))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def verify_lease(lease):
    try:
        issue, expiry = lease["issued_at"], lease["expires_at"]
        fields = {"v", "author_did", "relay_did", "endpoint", "domains", "issued_at", "expires_at", "proof"}
        domains = lease["domains"]
        keys = lease.get("encryption_keys", {})
        if not isinstance(domains, list) or not isinstance(keys, dict):
            return False
        if RESOLUTION_VERSION in domains:
            from .secure_metadata import decode
            decode(keys[RESOLUTION_VERSION], 32)
        return (set(lease) in (fields, fields | {"encryption_keys"})
                and lease["v"] == LEASE_VERSION and domains in ([EXPERIENCE_VERSION], DOMAINS)
                and set(keys) == ({RESOLUTION_VERSION} if RESOLUTION_VERSION in domains else set())
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (issue, expiry))
                and 0 < expiry - issue <= 90 and issue - 30 <= time.time() < expiry
                and verifier_for()(lease["proof"], unsigned(lease)))
    except (ValueError, KeyError, TypeError):
        return False


class PublicMetadataMailbox:
    # Each instance owns its queues, replay guard and budgets. Other protocols
    # reuse transport mechanics without sharing metadata or coordination quota.
    version, prefix, domains = VERSION, PREFIX, DOMAINS
    lease_validator = staticmethod(verify_lease)
    global_rate, author_rate, request_cap = 64, 8, 16384
    forward_timeout = 2.5

    def __init__(self, identity, *, endpoint):
        self.identity, self.endpoint = identity, endpoint
        self._lock = threading.RLock()
        self._boxes, self._rates = {}, deque()
        self.guard = ReplayGuard()

    def _response(self, request, body, error=False):
        return message(self.identity, "ERROR" if error else "RESULT", request["author_did"], body, request["request_id"], version=self.version)

    def enabled(self):
        return True

    def operations(self, domain):
        return PATHS.values() if domain == EXPERIENCE_VERSION else RESOLUTION_OPS if domain == RESOLUTION_VERSION else ()

    def reserve(self, inner):
        pass

    def valid_response(self, response):
        return True

    def handle(self, action, request):
        if action not in {"capabilities", "register", "lookup", "poll", "reply", "forward"}:
            return 404, {"code": "UNSUPPORTED_OPERATION"}
        try:
            if len(canonical_json(request).encode()) > BODY_CAP:
                return 413, {"code": "REQUEST_TOO_LARGE"}
        except (ValueError, TypeError):
            return 401, {"code": "UNVERIFIED_IDENTITY"}
        if not verified(request, target=self.identity.did, version=self.version) or request["op"] != action.upper():
            return 401, {"code": "UNVERIFIED_IDENTITY"}
        ok, _ = self.guard.check(request["request_id"], request["issued_at"])
        if not ok:
            return 409, self._response(request, {"code": "REPLAYED_REQUEST"}, True)
        if not self.enabled():
            with self._lock:
                self._boxes.clear()
            return 403, self._response(request, {"code": "OPTIONAL_SERVICE_DISABLED"}, True)
        author, body = request["author_did"], request["body"]
        try:
            if not isinstance(body, dict):
                raise ValueError("INVALID_REQUEST")
            with self._lock:
                now = time.time()
                self._boxes = {k: v for k, v in self._boxes.items() if v["lease"]["expires_at"] > now}
                if action == "capabilities":
                    result = {"protocol": self.version, "domains": self.domains, "separate_queues": True,
                        "body_cap": BODY_CAP, "inner_cap": INNER_CAP, "lease_seconds": 90,
                        "max_nodes": 32, "pending_per_node": 2, "pending_global": 8}
                elif action == "register":
                    lease = body.get("lease")
                    if (not self.lease_validator(lease) or lease["author_did"] != author
                            or lease["relay_did"] != self.identity.did
                            or lease["endpoint"] != self.endpoint().rstrip("/") + self.prefix.rstrip("/")):
                        raise ValueError("INVALID_LEASE")
                    if author not in self._boxes and len(self._boxes) >= 32:
                        raise ValueError("MAILBOX_CAPACITY_LIMIT")
                    box = self._boxes.setdefault(author, {"queue": deque(), "pending": {}})
                    box["lease"] = lease
                    result = {"lease": lease, "domains": lease["domains"]}
                elif action == "lookup":
                    box = self._boxes.get(body.get("target_did"))
                    if box is None:
                        raise ValueError("LEASE_UNAVAILABLE")
                    result = {"lease": box["lease"]}
                elif action in {"poll", "reply"}:
                    box = self._boxes.get(author)
                    if not box:
                        raise ValueError("LEASE_EXPIRED")
                    if action == "poll":
                        result = {"requests": [box["queue"].popleft()] if box["queue"] else []}
                    else:
                        response = body.get("response")
                        if not isinstance(response, dict):
                            raise ValueError("UNVERIFIED_TARGET_REPLY")
                        pending = box["pending"].get((response or {}).get("request_id"))
                        if (not pending or not verified(response, target=pending["request"]["author_did"], version=pending["request"]["v"])
                                or response["author_did"] != author or len(canonical_json(response).encode()) > INNER_CAP
                                or response["op"] not in {"RESULT", "ERROR"} or not self.valid_response(response)):
                            raise ValueError("UNVERIFIED_TARGET_REPLY")
                        status = body.get("status")
                        if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 599:
                            raise ValueError("INVALID_REPLY_STATUS")
                        pending.update(response=response, status=status)
                        pending["event"].set()
                        result = {"ok": True}
                else:
                    result = None
            if action == "forward":
                result = self._forward(author, body)
            return 200, self._response(request, result)
        except (ValueError, TypeError, KeyError) as exc:
            return 400, self._response(request, {"code": str(exc)[:128]}, True)

    def _forward(self, author, body):
        inner = body.get("request")
        if not isinstance(inner, dict):
            raise ValueError("UNSUPPORTED_METADATA_DOMAIN")
        domain = (inner or {}).get("v")
        operations = self.operations(domain)
        if (domain not in self.domains or not verified(inner, version=domain) or inner["author_did"] != author
                or inner["op"] not in operations or len(canonical_json(inner).encode()) > self.request_cap):
            raise ValueError("UNSUPPORTED_METADATA_DOMAIN")
        target, rid = inner["target_did"], inner["request_id"]
        with self._lock:
            box = self._boxes.get(target)
            if not box or domain not in box["lease"]["domains"]:
                raise ValueError("LEASE_UNAVAILABLE")
            now = time.monotonic()
            while self._rates and self._rates[0][0] < now - 60:
                self._rates.popleft()
            if len(self._rates) >= self.global_rate or sum(a == author for _, a in self._rates) >= self.author_rate:
                raise ValueError("RATE_LIMITED")
            per_node = sum(p["request"]["v"] == domain for p in box["pending"].values())
            global_pending = sum(p["request"]["v"] == domain for b in self._boxes.values() for p in b["pending"].values())
            if per_node >= 2 or global_pending >= 8 or rid in box["pending"]:
                raise ValueError("MAILBOX_CAPACITY_LIMIT")
            self.reserve(inner)
            self._rates.append((now, author))
            pending = {"request": inner, "event": threading.Event(), "response": None}
            box["pending"][rid] = pending
            box["queue"].append(inner)
        try:
            if not pending["event"].wait(self.forward_timeout):
                raise ValueError("TARGET_DEADLINE_REACHED")
            return {"response": pending["response"], "status": pending["status"], "domain": domain}
        finally:
            with self._lock:
                box["pending"].pop(rid, None)
                box["queue"] = deque(r for r in box["queue"] if r["request_id"] != rid)


def exchange(network, identity, relay, action, body, *, timeout=3, cap=262144, version=VERSION):
    request = message(identity, action.upper(), relay["relay_did"], body, version=version)
    response, count, status = network._request(relay["endpoint"].rstrip("/") + "/" + action,
                                              request, timeout=timeout, cap=cap)
    if (not verified(response, target=identity.did, version=version) or response["author_did"] != relay["relay_did"]
            or response["request_id"] != request["request_id"]):
        raise ValueError("UNVERIFIED_MAILBOX_RESPONSE")
    if response["op"] == "ERROR":
        raise ValueError(response["body"]["code"])
    if status != 200 or response["op"] != "RESULT":
        raise ValueError("INVALID_MAILBOX_RESPONSE")
    return response["body"], count


class MetadataMailboxClient:
    version, prefix, name, poll_interval = VERSION, PREFIX, "a2n-metadata-mailbox", .4

    def __init__(self, identity, network, experience, *, roots, resolution=None):
        self.identity, self.network, self.experience, self.roots = identity, network, experience, roots
        self.resolution = resolution
        self._stop = threading.Event()
        self._thread = None
        self.lease = None
        self.last_error = ""

    def start(self):
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)

    def exchange(self, relay, action, body):
        return exchange(self.network, self.identity, relay, action, body, version=self.version)

    def make_lease(self, relay, capability):
        if capability.get("protocol") != self.version or EXPERIENCE_VERSION not in capability.get("domains", []):
            raise ValueError("METADATA_V2_UNSUPPORTED")
        now = time.time()
        domains = DOMAINS if self.resolution and RESOLUTION_VERSION in capability.get("domains", []) else [EXPERIENCE_VERSION]
        encryption = {"encryption_keys": {RESOLUTION_VERSION: self.resolution.inbox.public_key}} if RESOLUTION_VERSION in domains else {}
        return signed({"v": LEASE_VERSION, "author_did": self.identity.did, **relay,
            "domains": domains, **encryption, "issued_at": now, "expires_at": now + 90}, signer_for(self.identity))

    def dispatch(self, request):
        if request.get("v") == RESOLUTION_VERSION and self.resolution:
            path = {"GET_CAPABILITIES": "capabilities", "DELIVER_SEALED": "sealed", "GET_DELIVERY": "delivery"}.get(request.get("op"), "")
            handler = self.resolution
        else:
            path = next((k for k, v in PATHS.items() if v == request.get("op")), "")
            handler = self.experience
        status, response = handler.handle(path, request)
        if len(canonical_json(response).encode()) > INNER_CAP:
            response = handler._response(request, {"code": "MAILBOX_RESPONSE_TOO_LARGE"}, error=True)
            status = 413
        return status, response

    def _run(self):
        relay, base, root_index = None, "", 0
        while not self._stop.is_set():
            try:
                roots = list(self.roots())
                if not roots:
                    self.lease = None
                    self._stop.wait(1)
                    continue
                if relay is None or base not in roots or not self.lease or self.lease["expires_at"] - time.time() < 45:
                    base = roots[root_index % len(roots)].rstrip("/")
                    session, _ = self.network.handshake({"endpoint": base + "/public/v1/coord"}, 16384, 3)
                    relay = {"endpoint": base + self.prefix.rstrip("/"), "relay_did": session["node_did"]}
                    capability, _ = self.exchange(relay, "capabilities", {})
                    lease = self.make_lease(relay, capability)
                    answer, _ = self.exchange(relay, "register", {"lease": lease})
                    if answer.get("lease") != lease:
                        raise ValueError("LEASE_MISMATCH")
                    self.lease = lease
                answer, _ = self.exchange(relay, "poll", {})
                for request in answer.get("requests", [])[:1]:
                    status, response = self.dispatch(request)
                    self.exchange(relay, "reply", {"status": status, "response": response})
                self.last_error = ""
            except Exception as exc:
                self.last_error, self.lease, relay = str(exc)[:200], None, None
                root_index += 1
                self._stop.wait(2)
            self._stop.wait(self.poll_interval)
