"""Node payment coordination. Points ledger and business execution stay separate."""
from __future__ import annotations

import threading
import time

from a2n_sdk.points import METHOD, participants, units, verify
from a2n_sdk.points_coordination import PointsCoordinator, find_routes
from a2n_sdk.trade_facts import digest


class PointsPayment:
    def __init__(self, node):
        self.node, self.store = node, node.store
        self.coordinator = PointsCoordinator(node.store, node_did=node.did, signer=node.signer, verifier=node.verifier)
        self._lock = threading.RLock()
        self.business_guard = None

    def get(self, plan_id):
        return self.coordinator.get(plan_id)

    def holdings(self):
        rows, missing = [], []
        for issuer in sorted({self.node.did, *self.store.items("points_peers")}):
            try:
                record = self.node.request(issuer, "balance", {})
                verify(record, "a2n-points-balance/1", self.node.verifier, issuer)
                if record.get("holder_did") != self.node.did or record.get("issuer_did") != issuer:
                    raise ValueError("POINTS_BALANCE_MISMATCH")
                self.store.put("points_remote_balances", issuer, record)
                rows.append(record)
            except Exception:
                missing.append(issuer)
        return {"balances": rows, "missing_issuers": missing}

    def routes(self, offer, funding_issuer, *, max_cost=None):
        verify(offer, "a2n-points-offer/1", self.node.verifier, offer["provider_did"])
        catalogs, missing = self.node.catalogs()
        if offer["provider_did"] not in catalogs:
            raise ValueError("POINTS_PROVIDER_UNREACHABLE")
        balance = self.node.request(funding_issuer, "balance", {})["available"]
        if max_cost is not None:
            balance = min(balance, units(max_cost))
        paths = find_routes(buyer_did=self.node.did, funding_issuer=funding_issuer, balance=balance,
            provider_did=offer["provider_did"], price=offer["points_policy"]["amount"], catalogs=catalogs)
        return {"paths": paths, "missing_peers": missing, "funding_issuer": funding_issuer,
                "reason": "" if paths else "NO_ACCEPTED_PATH_OR_INSUFFICIENT_BALANCE"}

    def create_service(self, offer, option_index, *, command_id, funding_issuer=None, max_cost=None):
        verify(offer, "a2n-points-offer/1", self.node.verifier, offer["provider_did"])
        if type(option_index) is not int or not 0 <= option_index < len(offer["options"]):
            raise ValueError("POINTS_OPTION_UNAVAILABLE")
        terms = offer["options"][option_index]
        if terms["mode"] == "PAY":
            issuer = funding_issuer or offer["provider_did"]
            found = self.routes(offer, issuer, max_cost=max_cost)
            if not found["paths"]:
                raise ValueError(found["reason"])
            ops = found["paths"][0]["operations"]
        else:
            ops = [{"kind": "SERVICE_ISSUE", "issuer_did": offer["provider_did"], "amount": terms["amount_minor"]}]
            if terms["mode"] == "DEBT":
                ops.append({"kind": "DEBT", "issuer_did": offer["provider_did"], "buyer_did": self.node.did, "amount": terms["amount_minor"]})
        stub = {"author_did": self.node.did, "offer": offer, "operations": ops}
        return self.coordinator.create(operations=ops, routes=self.node.routes(participants(stub)),
            command_id=command_id, offer=offer, terms=terms)

    def prepare(self, plan_id):
        with self._lock:
            row = self.get(plan_id)
            if row["decision"]:
                return self.reconcile(plan_id)
            plan = row["record"]
            try:
                for stage, action in (("consents", "consent"), ("prepared", "prepare")):
                    for party in participants(plan):
                        row = self.get(plan_id)
                        if party in row[stage]:
                            continue
                        if stage == "consents" and party == self.node.did and self.business_guard:
                            self.business_guard(plan)
                        payload = {"plan": plan}
                        if stage == "prepared":
                            payload["consents"] = row["consents"]
                        record = self.node.request(party, action, payload, routes=plan["routes"])
                        self.coordinator.remember(plan_id, stage, party, record)
            except ValueError as exc:
                # This leader has not dispatched business execution or decided commit.
                self.coordinator.decide(plan_id, "ABORT", reason=str(exc))
                self.reconcile(plan_id)
                raise
            except Exception:
                return self.coordinator.state(plan_id, "UNKNOWN", reason="PREPARATION_RESPONSE_UNKNOWN")
            return self.coordinator.state(plan_id, "PREPARED")

    def finish(self, plan_id, delivery):
        with self._lock:
            row = self.get(plan_id)
            offer = row["record"].get("offer")
            if offer:
                verify(delivery, "a2n-points-delivery/1", self.node.verifier, offer["provider_did"])
                if delivery.get("plan_id") != plan_id or delivery.get("trade_uid") != offer["trade_uid"]:
                    raise ValueError("POINTS_DELIVERY_MISMATCH")
                if delivery["execution"] != "DELIVERED":
                    if delivery["execution"] in {"FAILED", "CANCELED"}:
                        self.coordinator.decide(plan_id, "ABORT", delivery=delivery, reason="SERVICE_NOT_DELIVERED")
                        return self.reconcile(plan_id)
                    return self.coordinator.state(plan_id, "UNKNOWN", delivery_proof=delivery)
            self.coordinator.decide(plan_id, "COMMIT", delivery=delivery)
            return self.reconcile(plan_id)

    def reconcile(self, plan_id, *, limit=None):
        with self._lock:
            row = self.get(plan_id)
            if not row["decision"]:
                return row
            plan = row["record"]
            attempted = 0
            for party in participants(plan):
                row = self.get(plan_id)
                if party in row["receipts"]:
                    continue
                if limit is not None and attempted >= limit:
                    break
                attempted += 1
                try:
                    receipt = self.node.request(party, "apply", {"plan": plan, "decision": row["decision"]}, routes=plan["routes"])
                    self.coordinator.remember(plan_id, "receipts", party, receipt)
                except Exception:
                    continue
            row = self.get(plan_id)
            complete = set(row["receipts"]) == set(participants(plan))
            state = ("CONFIRMED" if row["decision"]["decision"] == "COMMIT" else "FAILED") if complete else row["state"]
            return self.coordinator.state(plan_id, state)

    def swap(self, body):
        ops = body.get("operations")
        if not isinstance(ops, list) or any(o.get("kind") != "TRANSFER" for o in ops):
            raise ValueError("POINTS_SWAP_TRANSFERS_REQUIRED")
        stub = {"author_did": self.node.did, "operations": ops}
        with self._lock:
            row = self.coordinator.create(operations=ops, routes=self.node.routes(participants(stub)), command_id=body.get("command_id"))
            row = self.prepare(row["record"]["plan_id"])
            return self.finish(row["record"]["plan_id"], None) if row["state"] == "PREPARED" else row

    def repay(self, body):
        issuer, amount = body["issuer_did"], units(body["amount"], positive=True)
        ops = [{"kind": "TRANSFER", "issuer_did": issuer, "from_did": self.node.did, "to_did": issuer, "amount": amount},
               {"kind": "REPAY", "issuer_did": issuer, "buyer_did": self.node.did, "amount": amount}]
        stub = {"author_did": self.node.did, "operations": ops}
        with self._lock:
            row = self.coordinator.create(operations=ops, routes=self.node.routes(participants(stub)), command_id=body.get("command_id"))
            row = self.prepare(row["record"]["plan_id"])
            return self.finish(row["record"]["plan_id"], None) if row["state"] == "PREPARED" else row

    def command(self, path, body):
        if path == "/v1/points/configure":
            return 200, self.node.book.configure(body)
        if path == "/v1/points/peers":
            return 200, self.node.add_peer(body["endpoint"])
        if path == "/v1/points/acceptance":
            return 200, self.node.book.set_acceptance(body["issuer_did"], body["policy"], expected_revision=body["expected_revision"])
        if path == "/v1/points/grants":
            return 200, self.node.book.grant(body["holder_did"], body["amount"], command_id=body["command_id"],
                mode=body.get("mode", "ISSUE"), memo=body.get("memo", ""))
        if path == "/v1/points/holdings":
            return 200, self.holdings()
        if path == "/v1/points/swap":
            return 200, self.swap(body)
        if path == "/v1/points/repay":
            return 200, self.repay(body)
        if path == "/v1/points/recover-participants":
            return 200, {"recovered": self.node.recover_participants()}
        if path.startswith("/v1/points/orders/"):
            plan_id, _, action = path.removeprefix("/v1/points/orders/").rpartition("/")
            if action == "reconcile":
                return 200, self.reconcile(plan_id)
            if action == "cancel":
                row = self.get(plan_id)
                if row.get("execution_started") or row["decision"] and row["decision"]["decision"] == "COMMIT":
                    raise ValueError("POINTS_EXECUTION_OR_COMMIT_ALREADY_EXPOSED")
                self.coordinator.decide(plan_id, "ABORT", reason="OWNER_CANCELED")
                return 200, self.reconcile(plan_id)
        raise ValueError("UNKNOWN_POINTS_COMMAND")

    def summary(self):
        return {**self.node.book.summary(), "peers": [{"did": r["did"], "endpoint": r["endpoint"]} for r in self.store.items("points_peers").values()],
            "holdings": list(self.store.items("points_remote_balances").values()),
            "orders": [{k: v for k, v in row.items() if k not in {"request", "consents", "prepared", "decision"}}
                       for row in list(self.store.items("points_orders").values())[-100:]]}
