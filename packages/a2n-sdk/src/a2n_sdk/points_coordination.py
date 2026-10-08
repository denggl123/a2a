"""Payment coordination for independently signed points participants."""
from __future__ import annotations

import time

from .experience import signed
from .points import METHOD, participants, units, validate_plan, verify
from .trade_facts import digest


def ceil_ratio(amount, numerator, denominator):
    return units((amount * denominator + numerator - 1) // numerator, positive=True)


def find_routes(*, buyer_did, funding_issuer, balance, provider_did, price, catalogs, max_hops=3):
    """Compare costs only in the selected issuer's units, never across currencies."""
    units(balance); units(price, positive=True)
    if type(max_hops) is not int or not 1 <= max_hops <= 3 or len(catalogs) > 64:
        raise ValueError("POINTS_SEARCH_LIMIT")
    seller = catalogs.get(provider_did, {})
    results = []

    def rule(node, issuer):
        if node == issuer:
            return {"numerator": 1, "denominator": 1, "per_trade": 10**18}
        r = next((r for r in catalogs.get(node, {}).get("acceptance", []) if r["issuer_did"] == issuer and r["enabled"]), None)
        return r

    def visit(asset, chain, seen):
        accept = rule(provider_did, asset)
        if accept:
            amount = ceil_ratio(price, accept["numerator"], accept["denominator"])
            amounts = [amount]
            valid = amount <= accept["per_trade"]
            for node, previous_asset in reversed(chain):
                r = rule(node, previous_asset)
                valid = valid and amount <= catalogs[node].get("exchange_available", 0)
                amount = ceil_ratio(amount, r["numerator"], r["denominator"])
                valid = valid and amount <= r["per_trade"]
                amounts.insert(0, amount)
            if valid and amount <= balance:
                holders = [buyer_did, *[n for n, _ in chain], provider_did]
                assets = [funding_issuer, *[n for n, _ in chain]]
                if all(a != b for a, b in zip(holders, holders[1:])):
                    ops = [{"kind": "TRANSFER", "issuer_did": asset_id, "from_did": source, "to_did": target, "amount": count}
                           for asset_id, source, target, count in zip(assets, holders, holders[1:], amounts)]
                    results.append({"funding_issuer": funding_issuer, "cost": amount, "hops": len(ops), "operations": ops})
        if len(chain) >= max_hops - 1:
            return
        for node in sorted(catalogs):
            if node in seen or node in {buyer_did, provider_did} or not rule(node, asset):
                continue
            if catalogs[node].get("exchange_available", 0) <= 0:
                continue
            visit(node, [*chain, (node, asset)], {*seen, node})

    visit(funding_issuer, [], {funding_issuer})
    return sorted(results, key=lambda r: (r["cost"], r["hops"], [o["to_did"] for o in r["operations"]]))[:32]


class PointsCoordinator:
    """A durable transaction leader; a timeout is not an abort decision."""

    def __init__(self, store, *, node_did, signer, verifier, now=None):
        self.store, self.node_did, self.signer, self.verifier = store, node_did, signer, verifier
        self.now = now or time.time

    def create(self, *, operations, routes, command_id, offer=None, terms=None, agreement=None):
        operations = [{**op, "amount": units(op["amount"], positive=True)} for op in operations]
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("POINTS_COMMAND_ID_REQUIRED")
        fingerprint = digest([operations, routes, offer, terms, agreement])
        with self.store.tx():
            old = self.store.get("points_plan_commands", command_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(old["plan_id"])
            if offer:
                prior = self.store.get("points_trade_orders", offer["trade_uid"])
                if prior:
                    raise ValueError("POINTS_TRADE_ALREADY_HAS_ORDER")
            now = self.now()
            core = {"v": "a2n-points-plan/1", "author_did": self.node_did, "operations": operations,
                    "routes": routes, "offer": offer, "terms": terms, "agreement": agreement, "command_id": command_id, "fee_cap_minor": 0,
                    "funding_cost_decimal": str(operations[0]["amount"]) if operations[0]["kind"] == "TRANSFER" else "0",
                    "issued_at": now, "expires_at": min(now + 300, offer["expires_at"]) if offer else now + 300}
            core["plan_id"] = "pt_" + digest(core)
            plan = signed(core, self.signer)
            validate_plan(plan, self.verifier, now=now, fresh=True)
            row = {"record": plan, "state": "READY", "consents": {}, "prepared": {}, "receipts": {}, "decision": None}
            self.store.put("points_orders", plan["plan_id"], row)
            self.store.put("points_plan_commands", command_id, {"fingerprint": fingerprint, "plan_id": plan["plan_id"]})
            if offer:
                self.store.put("points_trade_orders", offer["trade_uid"], plan["plan_id"])
            return row

    def get(self, plan_id):
        row = self.store.get("points_orders", plan_id)
        if not row:
            raise ValueError("POINTS_ORDER_NOT_FOUND")
        return row

    def remember(self, plan_id, stage, party, record):
        with self.store.tx():
            row = self.get(plan_id)
            version = {"consents": "a2n-points-consent/1", "prepared": "a2n-points-prepared/1", "receipts": "a2n-points-receipt/1"}[stage]
            verify(record, version, self.verifier, party)
            if party not in participants(row["record"]) or record.get("plan_id") != plan_id:
                raise ValueError("POINTS_PARTICIPANT_MISMATCH")
            if stage != "receipts" and record.get("plan_digest") != digest(row["record"]):
                raise ValueError("POINTS_PLAN_MISMATCH")
            if stage == "prepared" and record.get("consents_digest") != digest(row["consents"]):
                raise ValueError("POINTS_CONSENT_MISMATCH")
            if stage == "receipts" and (not row["decision"] or record.get("decision_digest") != digest(row["decision"])
                    or record.get("state") != ("COMMITTED" if row["decision"]["decision"] == "COMMIT" else "ABORTED")):
                raise ValueError("POINTS_RECEIPT_MISMATCH")
            previous = row[stage].get(party)
            if previous and previous != record:
                raise ValueError("POINTS_PARTICIPANT_CONFLICT")
            updated = {**row, stage: {**row[stage], party: record}}
            self.store.put("points_orders", plan_id, updated)
            return updated

    def state(self, plan_id, state, **fields):
        with self.store.tx():
            row = self.get(plan_id)
            if row["state"] in {"CONFIRMED", "FAILED"} or row["decision"] and state not in {"COMMITTING", "ABORTING", "CONFIRMED", "FAILED"}:
                return row
            row = {**row, **fields, "state": state}
            self.store.put("points_orders", plan_id, row)
            return row

    def decide(self, plan_id, decision, *, delivery=None, reason=""):
        with self.store.tx():
            row = self.get(plan_id)
            if row["decision"]:
                if row["decision"]["decision"] != decision:
                    raise ValueError("POINTS_DECISION_CONFLICT")
                return row
            if decision not in {"COMMIT", "ABORT"}:
                raise ValueError("INVALID_POINTS_DECISION")
            if decision == "COMMIT" and set(row["prepared"]) != set(participants(row["record"])):
                raise ValueError("POINTS_ALL_PREPARED_REQUIRED")
            if decision == "COMMIT" and row["record"].get("offer"):
                offer = row["record"]["offer"]
                verify(delivery, "a2n-points-delivery/1", self.verifier, offer["provider_did"])
                if delivery.get("plan_id") != plan_id or delivery.get("trade_uid") != offer["trade_uid"] or delivery.get("execution") != "DELIVERED":
                    raise ValueError("POINTS_REAL_DELIVERY_REQUIRED")
            record = signed({"v": "a2n-points-decision/1", "author_did": self.node_did,
                "plan_id": plan_id, "plan_digest": digest(row["record"]), "decision": decision,
                "prepared": row["prepared"] if decision == "COMMIT" else {}, "delivery": delivery,
                "reason": str(reason)[:200], "at": self.now()}, self.signer)
            row = {**row, "decision": record, "state": "COMMITTING" if decision == "COMMIT" else "ABORTING"}
            self.store.put("points_orders", plan_id, row)
            return row
