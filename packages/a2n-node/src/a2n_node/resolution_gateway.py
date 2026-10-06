"""Private signed negotiation transport. Never performs the proposed action."""
from __future__ import annotations

import threading
import time
import uuid

from a2n_sdk.experience import signed, unsigned
from a2n_sdk.projection import canonical_json

from .feedback_identity import signer_for
from .metadata_mailbox import verified, verify_lease, exchange, PREFIX as MAILBOX_PREFIX
from .secure_metadata import PrivateInbox
from .peer import ReplayGuard

VERSION = "a2n-resolution/1"
PREFIX = "/public/v1/resolution/"
MAX_BODY = 16384


def envelope(identity, op, target, body, request_id=None):
    now = time.time()
    return signed({"v": VERSION, "op": op, "request_id": request_id or uuid.uuid4().hex,
        "author_did": identity.did, "target_did": target, "issued_at": now,
        "expires_at": now + 30, "body": body}, signer_for(identity))


class PublicResolution:
    def __init__(self, identity, book):
        self.identity, self.book = identity, book
        self.inbox = PrivateInbox(book.store, identity.did)
        self.guard = ReplayGuard()
        self._lock, self._rates = threading.Lock(), []

    def _response(self, request, body, error=False):
        return envelope(self.identity, "ERROR" if error else "RESULT", request["author_did"], body, request["request_id"])

    def handle(self, action, request):
        operations = {"messages": "DELIVER_MESSAGE", "sealed": "DELIVER_SEALED",
                      "delivery": "GET_DELIVERY", "capabilities": "GET_CAPABILITIES"}
        if action not in operations:
            return 404, {"code": "UNSUPPORTED_OPERATION"}
        if not verified(request, target=self.identity.did, version=VERSION) or request["op"] != operations[action]:
            return 401, {"code": "UNVERIFIED_IDENTITY"}
        def reply(body, error=False):
            return self._response(request, body, error)
        if len(canonical_json(request).encode()) > MAX_BODY:
            return 413, reply({"code": "REQUEST_TOO_LARGE"}, True)
        ok, _ = self.guard.check(request["request_id"], request["issued_at"])
        if not ok:
            return 409, reply({"code": "REPLAYED_REQUEST"}, True)
        with self._lock:
            now = time.monotonic()
            self._rates = [(at, author) for at, author in self._rates if now - at < 60]
            if len(self._rates) >= 64 or sum(author == request["author_did"] for _, author in self._rates) >= 16:
                return 429, reply({"code": "RATE_LIMITED"}, True)
            self._rates.append((now, request["author_did"]))
        try:
            if action == "capabilities":
                result = {"protocol": VERSION, "private_encryption": "a2n-private-resolution/1",
                          "encryption_key": self.inbox.public_key}
            elif action in {"messages", "sealed"}:
                record = self.inbox.open(request) if action == "sealed" else request["body"]["message"]
                if record.get("author_did") != request["author_did"]:
                    raise ValueError("ENVELOPE_AUTHOR_MISMATCH")
                self.book.receive(record)
                result = {"message_id": record["message_id"], "received": True}
            else:
                message_id = request["body"].get("message_id")
                if not isinstance(message_id, str) or len(message_id) > 128:
                    raise ValueError("INVALID_MESSAGE_ID")
                record = self.book.store.get("resolution_messages", message_id)
                if not record or request["author_did"] not in {record["author_did"], record["target_did"]}:
                    raise ValueError("MESSAGE_NOT_FOUND")
                result = {"message_id": message_id, "received": True}
            return 200, reply(result)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return 400, reply({"code": str(exc)[:128]}, True)


class ResolutionDelivery:
    def __init__(self, identity, network, book):
        self.identity, self.network, self.book = identity, network, book
        self._lock = threading.Lock()

    def _check(self, response, request, status):
        if (not verified(response, target=self.identity.did, version=VERSION)
                or response["author_did"] != request["target_did"] or response["request_id"] != request["request_id"]
                or status != 200 or response["op"] != "RESULT"):
            raise ValueError("UNVERIFIED_DELIVERY_RESPONSE")
        return response["body"]

    def _deliver(self, row, record):
        deadline = time.monotonic() + 6
        def timeout(maximum):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RESOLUTION_DEADLINE_REACHED")
            return min(maximum, remaining)
        if row["endpoint"]:
            try:
                base = row["endpoint"].rstrip("/")
                query = envelope(self.identity, "GET_CAPABILITIES", row["target_did"], {})
                response, _, status = self.network._request(base + PREFIX + "capabilities", query, cap=4096, timeout=timeout(1))
                capability = self._check(response, query, status)
                if capability.get("protocol") != VERSION or capability.get("private_encryption") != "a2n-private-resolution/1":
                    raise ValueError("PRIVATE_RESOLUTION_UNSUPPORTED")
                request = envelope(self.identity, "DELIVER_SEALED", row["target_did"],
                    {"sealed_message": PrivateInbox.seal(record, capability["encryption_key"])})
                response, _, status = self.network._request(base + PREFIX + "sealed", request, cap=4096, timeout=timeout(1.5))
                return self._check(response, request, status), "DIRECT_ENCRYPTED"
            except (ValueError, OSError, PermissionError, TypeError, KeyError):
                pass
        for base in list(self.network.root_provider())[:2]:
            try:
                session, _ = self.network.handshake({"endpoint": base.rstrip("/") + "/public/v1/coord"}, 16384, timeout(1))
                relay = {"endpoint": base.rstrip("/") + MAILBOX_PREFIX.rstrip("/"), "relay_did": session["node_did"]}
                answer, _ = exchange(self.network, self.identity, relay, "lookup", {"target_did": row["target_did"]}, timeout=timeout(.8))
                lease = answer.get("lease")
                if (not verify_lease(lease) or lease["author_did"] != row["target_did"] or lease["relay_did"] != relay["relay_did"]
                        or lease["endpoint"] != relay["endpoint"] or VERSION not in lease["domains"]):
                    raise ValueError("PRIVATE_MAILBOX_UNAVAILABLE")
                request = envelope(self.identity, "DELIVER_SEALED", row["target_did"],
                    {"sealed_message": PrivateInbox.seal(record, lease["encryption_keys"][VERSION])})
                answer, _ = exchange(self.network, self.identity, relay, "forward", {"request": request}, timeout=timeout(3))
                if answer.get("domain") != VERSION:
                    raise ValueError("METADATA_DOMAIN_MISMATCH")
                return self._check(answer["response"], request, answer["status"]), "MAILBOX_ENCRYPTED"
            except (ValueError, OSError, PermissionError, TypeError, KeyError):
                continue
        raise ValueError("COUNTERPARTY_ROUTE_UNAVAILABLE")

    def drain(self, limit=4):
        if not self._lock.acquire(blocking=False):
            return
        try:
            rows = [r for r in self.book.store.items("resolution_outbox").values()
                    if r["state"] in {"READY", "PENDING"} and r["next_attempt"] <= time.time()][:limit]
            for row in rows:
                record = self.book.store.get("resolution_messages", row["message_id"])
                updated = {**row, "attempts": row["attempts"] + 1}
                self.book.store.put("resolution_outbox", row["message_id"], {**updated, "state": "PENDING"})
                try:
                    body, route = self._deliver(row, record)
                    if body != {"message_id": record["message_id"], "received": True}:
                        raise ValueError("TARGET_HAS_NOT_CONFIRMED_RECEIPT")
                    updated.update(state="DELIVERED", received_at=time.time(), route=route)
                except (ValueError, OSError, PermissionError, TypeError, KeyError):
                    updated.update(state="MANUAL_RETRY" if updated["attempts"] >= 5 else "PENDING",
                        reason="尚未取得对方签名收件确认", next_attempt=time.time() + min(64, 2 ** updated["attempts"]))
                self.book.store.put("resolution_outbox", row["message_id"], updated)
        finally:
            self._lock.release()
