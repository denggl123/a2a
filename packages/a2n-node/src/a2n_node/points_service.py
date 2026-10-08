"""Signed network adapter for the isolated points module. Never executes Agents."""
from __future__ import annotations

import time

from a2n_sdk.experience import signed
from a2n_sdk.points import PointsBook, participants, validate_plan, verify
from a2n_sdk.trade_facts import digest
from .feedback_identity import signer_for, verifier_for
from .x402.http import HTTPClient


class PointsNode:
    def __init__(self, store, identity, *, endpoint):
        self.store, self.did, self.endpoint = store, identity.did, endpoint
        self.signer, self.verifier = signer_for(identity), verifier_for()
        self.book = PointsBook(store, node_did=self.did, signer=self.signer, verifier=self.verifier)
        self.transport = self.business_guard = self.financial_observer = None

    def catalog(self):
        return signed({"v": "a2n-points-catalog/1", "author_did": self.did, "endpoint": self.endpoint(),
            "acceptance": list(self.store.items("points_acceptance").values()),
            "exchange_available": self.book.balance(self.did)["available"],
            "issuance_enabled": self.book.settings()["issuance_enabled"],
            "issued_at": time.time(), "expires_at": time.time() + 120}, self.signer)

    def http(self):
        return HTTPClient(timeout=4, allow_http=self.book.settings()["allow_http"])

    def request(self, target, action, payload, *, routes=None):
        if target == self.did:
            return self.public(action, {"payload": payload, "auth": self.auth(target, action, payload)})[1]
        peer = self.store.get("points_peers", target)
        base = (routes or {}).get(target) or (peer or {}).get("route") or (peer or {}).get("endpoint")
        if not base:
            raise ValueError("POINTS_PEER_ROUTE_MISSING")
        body = {"payload": payload, "auth": self.auth(target, action, payload)}
        if isinstance(base, dict):
            if not self.transport:
                raise ValueError("POINTS_SETTLEMENT_TRANSPORT_REQUIRED")
            result = self.transport.json(target, base, "points", action, body)
        else:
            result = self.http().json("POST", base.rstrip("/") + "/public/v1/points/" + action, body)
        if isinstance(result, dict) and result.get("author_did"):
            if not self.verifier(result.get("proof"), {k: v for k, v in result.items() if k != "proof"}) or result["author_did"] != target:
                raise ValueError("POINTS_PEER_RESPONSE_MISMATCH")
        else:
            raise ValueError("POINTS_SIGNED_RESPONSE_REQUIRED")
        return result

    def auth(self, target, action, payload):
        now = time.time()
        return signed({"v": "a2n-points-request/1", "author_did": self.did, "target_did": target,
            "action": action, "payload_digest": digest(payload), "issued_at": now, "expires_at": now + 60}, self.signer)

    def add_peer(self, endpoint):
        self.http().validate_url(endpoint)
        catalog = self.http().json("POST", endpoint.rstrip("/") + "/public/v1/points/catalog", {})
        verify(catalog, "a2n-points-catalog/1", self.verifier)
        if not catalog["issued_at"] - 30 <= time.time() < catalog["expires_at"]:
            raise ValueError("POINTS_CATALOG_EXPIRED")
        if catalog["author_did"] != self.did and not self.store.get("points_peers", catalog["author_did"]) and self.store.count("points_peers") >= 64:
            raise ValueError("POINTS_PEER_LIMIT")
        # Keep the route explicitly selected by the owner, not an advertised redirect.
        row = {"did": catalog["author_did"], "endpoint": endpoint.rstrip("/"), "catalog": catalog}
        self.store.put("points_peers", row["did"], row)
        return row

    def catalogs(self):
        self.discover_peers()
        out = {self.did: self.catalog()}
        failures = []
        for peer in self.store.items("points_peers").values():
            try:
                record = self.request(peer["did"], "catalog", {})
                verify(record, "a2n-points-catalog/1", self.verifier, peer["did"])
                if not record["issued_at"] - 30 <= time.time() < record["expires_at"]:
                    raise ValueError("POINTS_CATALOG_EXPIRED")
                out[peer["did"]] = record
            except Exception:
                failures.append(peer["did"])
        return out, failures

    def routes(self, parties):
        out = {}
        for party in parties:
            peer = self.store.get("points_peers", party)
            out[party] = (self.transport.own_route() if self.transport else self.endpoint()) if party == self.did else (peer or {}).get("route") or (peer or {}).get("endpoint")
            if not out[party]:
                raise ValueError("POINTS_PEER_ROUTE_MISSING: " + party)
        return out

    def add_route(self, target, route):
        if not self.transport:
            raise ValueError("POINTS_SETTLEMENT_TRANSPORT_REQUIRED")
        catalog = self.transport.json(target, route, "points", "catalog", {})
        verify(catalog, "a2n-points-catalog/1", self.verifier, target)
        if not catalog["issued_at"] - 30 <= time.time() < catalog["expires_at"]:
            raise ValueError("POINTS_CATALOG_EXPIRED")
        if not self.store.get("points_peers", target) and self.store.count("points_peers") >= 64:
            raise ValueError("POINTS_PEER_LIMIT")
        row = {"did": target, "endpoint": route if isinstance(route, str) else route["endpoint"], "route": route, "catalog": catalog}
        self.store.put("points_peers", target, row)
        return row

    def discover_peers(self, limit=8):
        """Import bounded verified node routes, without accepting any currency."""
        if not self.transport:
            return []
        from .settlement_transport import MAILBOX_PREFIX
        result = []
        attempted = 0
        for record in self.transport.daemon.public_coordination.known_records():
            target = record["node_did"]
            if target == self.did or self.store.get("points_peers", target):
                continue
            if attempted >= limit or self.store.count("points_peers") >= 64:
                break
            attempted += 1
            for candidate in record["coord_routes"][:1]:
                endpoint = candidate["endpoint"]
                if candidate["channel_type"] == "coord_mailbox" and endpoint.endswith("/public/v1/coord/mailbox"):
                    route = {"kind": "settlement_mailbox", "endpoint": endpoint.removesuffix("/public/v1/coord/mailbox") + MAILBOX_PREFIX.rstrip("/"),
                        "relay_did": candidate["relay_did"]}
                elif endpoint.endswith("/public/v1/coord"):
                    route = endpoint.removesuffix("/public/v1/coord")
                else:
                    continue
                try:
                    self.add_route(target, route)
                    result.append(target)
                except Exception:
                    continue
        return result

    def public(self, action, body):
        if action == "catalog":
            return 200, self.catalog()
        payload, auth = body.get("payload"), body.get("auth")
        verify(auth, "a2n-points-request/1", self.verifier)
        if (not isinstance(payload, dict) or auth.get("target_did") != self.did or auth.get("action") != action
                or auth.get("payload_digest") != digest(payload)
                or not 0 < auth.get("expires_at", 0) - auth.get("issued_at", 0) <= 60
                or not auth.get("issued_at", 0) - 30 <= time.time() < auth.get("expires_at", 0)):
            raise ValueError("POINTS_REQUEST_MISMATCH")
        author = auth["author_did"]
        if action == "balance":
            return 200, signed({"v": "a2n-points-balance/1", "author_did": self.did,
                **self.book.balance(author), "debt": self.book.debt(author), "observed_at": time.time()}, self.signer)
        if action == "decision":
            row = self.store.get("points_orders", payload.get("plan_id", ""))
            if not row or author not in participants(row["record"]):
                raise ValueError("POINTS_ORDER_NOT_FOUND")
            return 200, signed({"v": "a2n-points-decision-status/1", "author_did": self.did,
                "plan_id": payload["plan_id"], "decision": row["decision"], "state": row["state"]}, self.signer)
        plan = validate_plan(payload.get("plan"), self.verifier)
        if author not in participants(plan) or self.did not in participants(plan):
            raise ValueError("POINTS_NOT_PARTICIPANT")
        if action == "consent":
            if author != plan["author_did"]:
                raise ValueError("POINTS_LEADER_REQUIRED")
            with self.store.tx():
                if self.business_guard:
                    self.business_guard(plan)
                return 200, self.book.consent(plan)
        if action == "prepare":
            if author != plan["author_did"]:
                raise ValueError("POINTS_LEADER_REQUIRED")
            return 200, self.book.prepare(plan, payload["consents"])
        if action == "apply":
            result = self.book.apply(plan, payload["decision"])
            if self.financial_observer:
                if plan.get("offer"):
                    self.financial_observer(plan["offer"]["trade_uid"], "CONFIRMED" if result["state"] == "COMMITTED" else "FAILED")
                elif plan.get("agreement"):
                    self.financial_observer(plan["agreement"]["proposal"]["trade_uid"], "CONFIRMED" if result["state"] == "COMMITTED" else "FAILED", refund=True)
            return 200, result
        if action == "status":
            row = self.store.get("points_transactions", plan["plan_id"])
            return 200, signed({"v": "a2n-points-status/1", "author_did": self.did,
                "plan_id": plan["plan_id"], "state": (row or {}).get("state", "NOT_PREPARED"),
                "receipt": (row or {}).get("receipt"), "decision": (row or {}).get("decision")}, self.signer)
        raise ValueError("UNKNOWN_POINTS_OPERATION")

    def recover_participants(self, limit=4):
        """Pull the original leader's durable decision; never invent a timeout abort."""
        result = []
        attempted = 0
        for row in self.store.items("points_transactions").values():
            if row["state"] != "PREPARED":
                continue
            if attempted >= limit:
                break
            attempted += 1
            plan = row["plan"]
            try:
                status = self.request(plan["author_did"], "decision", {"plan_id": plan["plan_id"]}, routes=plan["routes"])
                decision = status.get("decision")
                if decision:
                    self.book.apply(plan, decision)
                    result.append(plan["plan_id"])
            except Exception:
                continue
        return result
