"""Bounded, end-to-end encrypted coordination for independent payment methods.

This channel transports method messages. It neither executes ledger decisions
nor shares discovery, experience, resolution or file mailbox queues.
"""
from __future__ import annotations

import math
import base64
import threading
import time

from a2n_sdk.experience import signed, unsigned
from a2n_sdk.projection import canonical_json

from .feedback_identity import signer_for, verifier_for
from .metadata_mailbox import PublicMetadataMailbox, MetadataMailboxClient, message, verified
from .peer import ReplayGuard
from .secure_metadata import PrivateInbox, decode

VERSION = "a2n-settlement/1"
MAILBOX_VERSION = "a2n-settlement-mailbox/1"
LEASE_VERSION = "a2n-settlement-lease/1"
DOMAIN = b"a2n-private-settlement/1"
PREFIX = "/public/v1/settlement/"
MAILBOX_PREFIX = "/public/v1/settlement-mailbox/"
MAX_PLAIN = 90000
MAX_BODY = 196608
OPERATIONS = {
    "points": {"catalog", "balance", "decision", "consent", "prepare", "apply", "status"},
    "trades": {"quote", "points-recover"},
    "payments": {"accept", "observe", "execute", "recover", "revoke", "refund-observe"},
}


def verify_lease(lease, *, fresh=True):
    try:
        issue, expiry = lease["issued_at"], lease["expires_at"]
        return (set(lease) == {"v", "author_did", "relay_did", "endpoint", "domains", "encryption_keys", "issued_at", "expires_at", "proof"}
            and lease["v"] == LEASE_VERSION and lease["domains"] == [VERSION]
            and set(lease["encryption_keys"]) == {VERSION}
            and len(decode(lease["encryption_keys"][VERSION], 32)) == 32
            and isinstance(lease["endpoint"], str) and len(lease["endpoint"]) <= 2048
            and all(type(n) in (int, float) and math.isfinite(n) for n in (issue, expiry))
            and 0 < expiry - issue <= 90 and (not fresh or issue - 30 <= time.time() < expiry)
            and verifier_for()(lease["proof"], unsigned(lease)))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def valid_sealed(body):
    try:
        sealed = body["sealed_message"]
        return (set(body) == {"sealed_message"} and set(sealed) == {"v", "ephemeral_key", "nonce", "ciphertext"}
            and sealed["v"] == DOMAIN.decode() and len(decode(sealed["ephemeral_key"], 32)) == 32
            and len(decode(sealed["nonce"], 12)) == 12
            and 16 <= len(decode(sealed["ciphertext"], maximum=MAX_PLAIN * 2)) <= MAX_PLAIN + 16)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


class PublicSettlementMailbox(PublicMetadataMailbox):
    version, prefix, domains = MAILBOX_VERSION, MAILBOX_PREFIX, [VERSION]
    lease_validator = staticmethod(verify_lease)
    global_rate, author_rate, request_cap = 512, 120, MAX_BODY
    forward_timeout = 18

    def operations(self, domain):
        return {"DISPATCH"} if domain == VERSION else ()

    def reserve(self, inner):
        if not valid_sealed(inner["body"]):
            raise ValueError("ENCRYPTED_SETTLEMENT_REQUIRED")

    def valid_response(self, response):
        return valid_sealed(response.get("body"))


class SettlementMailboxClient(MetadataMailboxClient):
    version, prefix, name, poll_interval = MAILBOX_VERSION, MAILBOX_PREFIX, "a2n-settlement-mailbox", .1

    def make_lease(self, relay, capability):
        if capability.get("protocol") != self.version or capability.get("domains") != [VERSION]:
            raise ValueError("SETTLEMENT_MAILBOX_UNSUPPORTED")
        now = time.time()
        return signed({"v": LEASE_VERSION, "author_did": self.identity.did, **relay,
            "domains": [VERSION], "encryption_keys": {VERSION: self.experience.inbox.public_key},
            "issued_at": now, "expires_at": now + 90}, signer_for(self.identity))

    def dispatch(self, request):
        return self.experience.handle("dispatch", request)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=22)


class SettlementGateway:
    def __init__(self, daemon):
        self.daemon, self.identity = daemon, daemon.identity
        self.inbox = PrivateInbox(daemon.store, self.identity.did, domain=DOMAIN, name="settlement", max_size=MAX_PLAIN)
        self.guard = ReplayGuard()
        self._lock, self._rates = threading.Lock(), []

    def _response(self, request, body):
        return message(self.identity, "RESULT", request["author_did"], body, request["request_id"], version=VERSION)

    def handle(self, action, request):
        if action not in {"capabilities", "dispatch"}:
            return 404, {"code": "UNSUPPORTED_OPERATION"}
        if (not verified(request, target=self.identity.did, version=VERSION)
                or request["op"] != action.upper() or len(canonical_json(request).encode()) > MAX_BODY):
            return 401, {"code": "UNVERIFIED_IDENTITY"}
        ok, _ = self.guard.check(request["request_id"], request["issued_at"])
        if not ok:
            return 409, {"code": "REPLAYED_REQUEST"}
        if action == "capabilities":
            return 200, self._response(request, {"protocol": VERSION, "encryption_key": self.inbox.public_key,
                "methods": list(OPERATIONS), "max_plain_bytes": MAX_PLAIN})
        with self._lock:
            now = time.monotonic()
            self._rates = [(at, did) for at, did in self._rates if at > now - 60]
            if len(self._rates) >= 512 or sum(did == request["author_did"] for _, did in self._rates) >= 120:
                return 429, {"code": "RATE_LIMITED"}
            self._rates.append((now, request["author_did"]))
        record = self.inbox.open(request)
        if (record.get("author_did") != request["author_did"] or record.get("target_did") != self.identity.did
                or record.get("request_id") != request["request_id"]):
            raise ValueError("SETTLEMENT_ENVELOPE_MISMATCH")
        decode(record["reply_key"], 32)
        try:
            result = self.dispatch(record)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            result = {"status": 400, "body": {"code": str(exc)[:200]}, "headers": {}}
        except Exception:
            result = {"status": 503, "body": {"code": "SETTLEMENT_SERVICE_UNAVAILABLE"}, "headers": {}}
        result.update(author_did=self.identity.did, target_did=request["author_did"], request_id=request["request_id"])
        sealed = PrivateInbox.seal(result, record["reply_key"], domain=DOMAIN, max_size=MAX_PLAIN)
        return 200, self._response(request, {"sealed_message": sealed})

    def dispatch(self, record):
        method, action, body = record["method"], record["action"], record["payload"]
        if action not in OPERATIONS.get(method, ()) or not isinstance(body, dict):
            raise ValueError("UNSUPPORTED_SETTLEMENT_OPERATION")
        author = record["author_did"]
        auth = body.get("auth") or body.get("query") or body.get("record") or body.get("plan")
        if auth and auth.get("author_did") != author:
            raise ValueError("SETTLEMENT_AUTHOR_MISMATCH")
        if method == "points":
            status, response = self.daemon.points_node.public(action, body)
        elif method == "trades":
            status, response = (self.daemon.trades.public_quote(body) if action == "quote" else
                (200, self.daemon.trades.public_recover(body)))
        else:
            headers = record.get("headers", {})
            if (not isinstance(headers, dict) or set(headers) - {"PAYMENT-SIGNATURE", "Idempotency-Key"}
                    or any(not isinstance(v, str) or len(v) > 24000 for v in headers.values())):
                raise ValueError("INVALID_SETTLEMENT_HEADERS")
            result = self.daemon.payment_coordination.public(action, body,
                signature=headers.get("PAYMENT-SIGNATURE"), replay_token=headers.get("Idempotency-Key"))
            if hasattr(result, "status"):
                return {"status": result.status, "body_base64": base64.b64encode(result.body).decode(), "headers": dict(result.headers)}
            status, response = result
        return {"status": status, "body": response, "headers": {}}


class SettlementTransport:
    def __init__(self, daemon, gateway):
        self.daemon, self.identity, self.gateway = daemon, daemon.identity, gateway
        self.network = daemon.coord_network

    def own_route(self, base=None):
        lease = getattr(getattr(self.daemon, "settlement_mailbox", None), "lease", None)
        if verify_lease(lease):
            return {"kind": "settlement_mailbox", "endpoint": lease["endpoint"], "relay_did": lease["relay_did"]}
        return base or self.daemon.management.discovery_public_base or self.daemon.runtime.local_base_url

    def declaration(self, base):
        return {"protocol": VERSION, "route": self.own_route(base)}

    def card_route(self, card):
        declaration = (card.get("x-a2n") or {}).get("settlement", {})
        if declaration.get("protocol") == VERSION:
            return declaration["route"]
        if (card.get("x-a2n") or {}).get("relay"):
            relay = (card["x-a2n"]["relay"] or {}).get("node")
            session, _ = self.network.handshake({"endpoint": relay.rstrip("/") + "/public/v1/coord"}, 16384, 3)
            return {"kind": "settlement_mailbox", "endpoint": relay.rstrip("/") + MAILBOX_PREFIX.rstrip("/"),
                "relay_did": session["node_did"]}
        return card["url"].rsplit("/a2a/", 1)[0]

    def exchange(self, relay, action, body, *, timeout=3):
        request = message(self.identity, action.upper(), relay["relay_did"], body, version=MAILBOX_VERSION)
        response, _, status = self.network._request(relay["endpoint"].rstrip("/") + "/" + action,
            request, timeout=timeout, timeout_limit=25, cap=MAX_BODY)
        if (not verified(response, target=self.identity.did, version=MAILBOX_VERSION)
                or response["author_did"] != relay["relay_did"] or response["request_id"] != request["request_id"]):
            raise ValueError("UNVERIFIED_SETTLEMENT_MAILBOX")
        if status != 200 or response["op"] != "RESULT":
            raise ValueError((response.get("body") or {}).get("code", "SETTLEMENT_MAILBOX_REJECTED"))
        return response["body"]

    def request(self, target, route, method, action, payload, *, headers=None):
        if isinstance(route, str):
            query = message(self.identity, "CAPABILITIES", target, {}, version=VERSION)
            response, _, status = self.network._request(route.rstrip("/") + PREFIX + "capabilities", query, timeout=3, cap=8192)
            self.check(response, query, status)
            key = response["body"]["encryption_key"]
        elif (isinstance(route, dict) and set(route) == {"kind", "endpoint", "relay_did"}
                and route["kind"] == "settlement_mailbox"):
            answer = self.exchange(route, "lookup", {"target_did": target})
            lease = answer.get("lease")
            if (not verify_lease(lease) or lease["author_did"] != target
                    or lease["endpoint"] != route["endpoint"] or lease["relay_did"] != route["relay_did"]):
                raise ValueError("SETTLEMENT_LEASE_MISMATCH")
            key = lease["encryption_keys"][VERSION]
        else:
            raise ValueError("INVALID_SETTLEMENT_ROUTE")
        request = message(self.identity, "DISPATCH", target, {}, version=VERSION)
        record = {"author_did": self.identity.did, "target_did": target, "request_id": request["request_id"],
            "method": method, "action": action, "payload": payload, "headers": headers or {}, "reply_key": self.gateway.inbox.public_key}
        request = message(self.identity, "DISPATCH", target,
            {"sealed_message": PrivateInbox.seal(record, key, domain=DOMAIN, max_size=MAX_PLAIN)}, request["request_id"], version=VERSION)
        # Once dispatch starts, a missing or unverifiable response is UNKNOWN.
        # There is no transport fallback or fresh business/payment attempt.
        try:
            if isinstance(route, str):
                response, _, status = self.network._request(route.rstrip("/") + PREFIX + "dispatch", request,
                    timeout=20, timeout_limit=25, cap=MAX_BODY)
            else:
                answer = self.exchange(route, "forward", {"request": request}, timeout=20)
                if answer.get("domain") != VERSION:
                    raise ValueError("SETTLEMENT_DOMAIN_MISMATCH")
                response, status = answer["response"], answer["status"]
            self.check(response, request, status)
            result = self.gateway.inbox.open(response)
            if (result.get("author_did") != target or result.get("target_did") != self.identity.did
                    or result.get("request_id") != request["request_id"]):
                raise ValueError("SETTLEMENT_RESPONSE_MISMATCH")
        except Exception as exc:
            raise TimeoutError("SETTLEMENT_RESPONSE_UNKNOWN") from exc
        if result["status"] >= 500:
            raise TimeoutError("SETTLEMENT_RESPONSE_UNKNOWN")
        return result

    def check(self, response, request, status):
        if (status != 200 or not verified(response, target=self.identity.did, version=VERSION)
                or response["author_did"] != request["target_did"] or response["request_id"] != request["request_id"]
                or response["op"] != "RESULT"):
            raise ValueError("UNVERIFIED_SETTLEMENT_RESPONSE")

    def json(self, target, route, method, action, payload):
        result = self.request(target, route, method, action, payload)
        if result["status"] != 200:
            raise ValueError("SETTLEMENT_REJECTED: " + str(result["body"].get("code", "INVALID_REQUEST")))
        return result["body"]
