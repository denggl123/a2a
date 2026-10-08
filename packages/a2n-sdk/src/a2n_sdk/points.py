"""Issuer scoped service points. No trials, Agent execution, transport or wallet code."""
from __future__ import annotations

import time
from collections import defaultdict

from .experience import signed, unsigned
from .trade_facts import digest

METHOD = "a2n-points/1"
MAX_UNITS = 10**18


def units(value, *, positive=False):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not (int(positive) <= value <= MAX_UNITS):
        raise ValueError("INVALID_POINTS_UNITS")
    return value


def did(value):
    if not isinstance(value, str) or not value.startswith("did:a2n:ag_") or len(value) > 128:
        raise ValueError("INVALID_POINTS_IDENTITY")
    return value


def verify(record, version, verifier, author=None):
    if (not isinstance(record, dict) or record.get("v") != version
            or author is not None and record.get("author_did") != author
            or not verifier(record.get("proof"), unsigned(record))):
        raise ValueError("INVALID_POINTS_SIGNATURE")
    return record


def participants(plan):
    parties = {plan["author_did"]}
    offer = plan.get("offer")
    if offer:
        parties.update((offer["buyer_did"], offer["provider_did"]))
    for op in plan["operations"]:
        parties.add(op["issuer_did"])
        parties.update(op[k] for k in ("from_did", "to_did", "buyer_did") if k in op)
    return sorted(parties)


def validate_plan(plan, verifier, *, now=None, fresh=False):
    verify(plan, "a2n-points-plan/1", verifier)
    did(plan["author_did"])
    core = {k: v for k, v in unsigned(plan).items() if k != "plan_id"}
    if plan.get("plan_id") != "pt_" + digest(core):
        raise ValueError("POINTS_PLAN_MISMATCH")
    start, end = plan.get("issued_at"), plan.get("expires_at")
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (float, int)) or not isinstance(end, (float, int))
            or not 0 < end - start <= 300
            or fresh and not start - 30 <= (now if now is not None else time.time()) < end):
        raise ValueError("POINTS_PLAN_EXPIRED")
    ops = plan.get("operations")
    if not isinstance(ops, list) or not 1 <= len(ops) <= 9:
        raise ValueError("INVALID_POINTS_OPERATIONS")
    for op in ops:
        if op.get("kind") not in {"TRANSFER", "SERVICE_ISSUE", "DEBT", "REPAY"}:
            raise ValueError("INVALID_POINTS_OPERATION")
        did(op["issuer_did"])
        units(op["amount"], positive=True)
        if op["kind"] == "TRANSFER":
            if set(op) != {"kind", "issuer_did", "from_did", "to_did", "amount"}:
                raise ValueError("INVALID_POINTS_TRANSFER")
            did(op["from_did"]); did(op["to_did"])
            if op["from_did"] == op["to_did"]:
                raise ValueError("POINTS_SELF_TRANSFER")
        else:
            if not plan.get("offer") and op["kind"] != "REPAY":
                raise ValueError("POINTS_SERVICE_BINDING_REQUIRED")
            if op["kind"] in {"DEBT", "REPAY"}:
                did(op["buyer_did"])
    if len(participants(plan)) > 12 or len([o for o in ops if o["kind"] == "TRANSFER"]) > 3:
        raise ValueError("POINTS_PATH_LIMIT")
    offer = plan.get("offer")
    if offer:
        verify(offer, "a2n-points-offer/1", verifier, offer["provider_did"])
        terms = plan.get("terms") or {}
        if (offer["buyer_did"] != plan["author_did"] or terms not in offer["options"]
                or terms.get("method") != METHOD or terms.get("mode") not in {"PAY", "EARN", "DEBT"}):
            raise ValueError("POINTS_OFFER_MISMATCH")
        issuer, buyer, amount = offer["provider_did"], offer["buyer_did"], terms["amount_minor"]
        issue = {"kind": "SERVICE_ISSUE", "issuer_did": issuer, "amount": amount}
        expected = [issue] if terms["mode"] == "EARN" else [issue, {"kind": "DEBT", "issuer_did": issuer, "buyer_did": buyer, "amount": amount}]
        if terms["mode"] != "PAY" and ops != expected:
            raise ValueError("POINTS_ISSUANCE_RULE_MISMATCH")
        if terms["mode"] == "PAY":
            if any(o["kind"] != "TRANSFER" for o in ops) or ops[0]["from_did"] != buyer or ops[-1]["to_did"] != issuer:
                raise ValueError("POINTS_PAYMENT_PATH_MISMATCH")
            for previous, following in zip(ops, ops[1:]):
                if previous["to_did"] != following["from_did"] or following["issuer_did"] != following["from_did"]:
                    raise ValueError("POINTS_PAYMENT_PATH_MISMATCH")
    elif any(o["kind"] == "REPAY" for o in ops):
        if len(ops) != 2 or ops[0]["kind"] != "TRANSFER" or ops[1]["kind"] != "REPAY":
            raise ValueError("INVALID_POINTS_REPAYMENT")
        transfer, repayment = ops
        if (transfer["from_did"] != plan["author_did"] or transfer["to_did"] != transfer["issuer_did"]
                or repayment != {"kind": "REPAY", "issuer_did": transfer["issuer_did"],
                    "buyer_did": transfer["from_did"], "amount": transfer["amount"]}):
            raise ValueError("INVALID_POINTS_REPAYMENT")
    agreement = plan.get("agreement")
    if agreement:
        proposal = verify(agreement.get("proposal"), "a2n-resolution-message/1", verifier)
        people = {proposal["author_did"], proposal["target_did"]}
        accepts = agreement.get("acceptances") or []
        if proposal["kind"] != "PROPOSAL" or proposal["body"]["action"] != "REFUND" or len(accepts) != 2:
            raise ValueError("POINTS_REFUND_AGREEMENT_REQUIRED")
        for acceptance in accepts:
            verify(acceptance, "a2n-resolution-message/1", verifier)
            if (acceptance["kind"] != "ACCEPT" or acceptance["parent_id"] != proposal["message_id"]
                    or acceptance["body"]["proposal_hash"] != digest(proposal)
                    or acceptance["trade_uid"] != proposal["trade_uid"]
                    or {acceptance["author_did"], acceptance["target_did"]} != people):
                raise ValueError("POINTS_REFUND_AGREEMENT_REQUIRED")
        if {a["author_did"] for a in accepts} != people or len(ops) != 1 or ops[0]["kind"] != "TRANSFER":
            raise ValueError("POINTS_REFUND_AGREEMENT_REQUIRED")
        op = ops[0]
        if ({op["from_did"], op["to_did"]} != people or op["from_did"] != plan["author_did"]
                or proposal["body"]["currency"] != "points:" + op["issuer_did"]
                or proposal["body"]["amount_minor"] != op["amount"]):
            raise ValueError("POINTS_REFUND_TERMS_MISMATCH")
    if set(plan.get("routes", {})) != set(participants(plan)):
        raise ValueError("POINTS_PARTICIPANT_ROUTES_REQUIRED")
    return plan


class PointsBook:
    """This node is authoritative only for points it issues; no global balance."""

    def __init__(self, store, *, node_did, signer, verifier, now=None):
        self.store, self.node_did, self.signer, self.verifier = store, node_did, signer, verifier
        self.now = now or time.time

    def balance(self, holder):
        did(holder)
        amount = self.store.get("points_balances", holder, 0)
        reserved = sum(r.get("debits", {}).get(holder, 0) for r in self.store.items("points_transactions").values()
                       if r["state"] == "PREPARED")
        return {"issuer_did": self.node_did, "holder_did": holder, "amount": amount,
                "reserved": reserved, "available": amount - reserved, "amount_decimal": str(amount),
                "reserved_decimal": str(reserved), "available_decimal": str(amount - reserved)}

    def debt(self, buyer):
        return self.store.get("points_debts", buyer, 0)

    def settings(self):
        return self.store.get("points_settings", "config", {"issuance_enabled": True, "allow_http": False})

    def configure(self, body):
        if set(body) != {"issuance_enabled", "allow_http"} or any(type(v) is not bool for v in body.values()):
            raise ValueError("INVALID_POINTS_SETTINGS")
        with self.store.tx():
            self.store.put("points_settings", "config", body)
        return body

    def service(self, service_id):
        return self.store.get("points_services", service_id)

    def set_service(self, service_id, body, *, expected_revision):
        if set(body) - {"enabled", "amount", "modes", "debt_limit"} or type(body.get("enabled")) is not bool:
            raise ValueError("INVALID_POINTS_SERVICE_POLICY")
        amount = units(body.get("amount"), positive=True)
        debt_limit = units(body.get("debt_limit", 0))
        modes = body.get("modes")
        if (not isinstance(modes, list) or not modes or len(set(modes)) != len(modes)
                or set(modes) - {"EARN", "DEBT", "PAY"} or "DEBT" in modes and not debt_limit):
            raise ValueError("INVALID_POINTS_SERVICE_MODES")
        with self.store.tx():
            old = self.service(service_id)
            if type(expected_revision) is not int or expected_revision != (old or {}).get("revision", 0):
                raise ValueError("POINTS_POLICY_REVISION_CONFLICT")
            row = {"service_id": service_id, "enabled": body["enabled"], "amount": amount,
                   "modes": sorted(modes), "debt_limit": debt_limit, "revision": expected_revision + 1}
            self.store.put("points_services", service_id, row)
            return row

    def acceptance(self, issuer):
        return self.store.get("points_acceptance", issuer)

    def set_acceptance(self, issuer, body, *, expected_revision):
        did(issuer)
        if issuer == self.node_did or set(body) != {"enabled", "numerator", "denominator", "per_trade", "daily", "max_holding"}:
            raise ValueError("INVALID_POINTS_ACCEPTANCE")
        if type(body["enabled"]) is not bool:
            raise ValueError("INVALID_POINTS_ACCEPTANCE")
        row = {k: units(v, positive=True) for k, v in body.items() if k != "enabled"}
        with self.store.tx():
            old = self.acceptance(issuer)
            if type(expected_revision) is not int or expected_revision != (old or {}).get("revision", 0):
                raise ValueError("POINTS_POLICY_REVISION_CONFLICT")
            row.update(issuer_did=issuer, enabled=body["enabled"], revision=expected_revision + 1)
            self.store.put("points_acceptance", issuer, row)
            return row

    def grant(self, holder, amount, *, command_id, mode="ISSUE", memo=""):
        did(holder); amount = units(amount, positive=True)
        if mode not in {"ISSUE", "TRANSFER"} or not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("INVALID_POINTS_GRANT")
        fingerprint = digest([holder, amount, mode, memo])
        with self.store.tx():
            old = self.store.get("points_grant_commands", command_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return old["receipt"]
            if mode == "ISSUE" and not self.settings()["issuance_enabled"]:
                raise ValueError("POINTS_ISSUANCE_STOPPED")
            if mode == "TRANSFER":
                if holder == self.node_did or self.balance(self.node_did)["available"] < amount:
                    raise ValueError("POINTS_INSUFFICIENT_BALANCE")
                self.store.put("points_balances", self.node_did, self.balance(self.node_did)["amount"] - amount)
            pending = sum(max(0, r.get("deltas", {}).get(holder, 0))
                + sum(o["amount"] for o in r["plan"]["operations"] if o["kind"] == "SERVICE_ISSUE" and o["issuer_did"] == self.node_did and holder == self.node_did)
                for r in self.store.items("points_transactions").values() if r["state"] == "PREPARED")
            units(self.balance(holder)["amount"] + amount + pending)
            value = units(self.balance(holder)["amount"] + amount)
            self.store.put("points_balances", holder, value)
            receipt = signed({"v": "a2n-points-grant/1", "author_did": self.node_did,
                "issuer_did": self.node_did, "holder_did": holder, "amount": amount, "mode": mode,
                "command_id": command_id, "memo": str(memo)[:256], "balance_after": value,
                "at": self.now()}, self.signer)
            self.store.put("points_grant_commands", command_id, {"fingerprint": fingerprint, "receipt": receipt})
            self.store.put("points_journal", "grant_" + digest([self.node_did, command_id]), receipt)
            return receipt

    def consent(self, plan):
        validate_plan(plan, self.verifier)
        key = plan["plan_id"]
        with self.store.tx():
            old = self.store.get("points_consents", key)
            if old:
                if old["plan"] != plan:
                    raise ValueError("POINTS_PLAN_MISMATCH")
                return old["record"]
            validate_plan(plan, self.verifier, now=self.now(), fresh=True)
            if sum(r.get("state") == "OPEN" for r in self.store.items("points_consents").values()) >= 64:
                raise ValueError("POINTS_PENDING_LIMIT")
            if self.node_did not in participants(plan):
                raise ValueError("POINTS_NOT_PARTICIPANT")
            incoming, outgoing = defaultdict(int), defaultdict(int)
            for op in plan["operations"]:
                if op["kind"] == "TRANSFER":
                    if op["to_did"] == self.node_did:
                        incoming[op["issuer_did"]] += op["amount"]
                    if op["from_did"] == self.node_did:
                        outgoing[op["issuer_did"]] += op["amount"]
            rules, value = {}, 0
            today = int(self.now() // 86400)
            for issuer, amount in incoming.items():
                if issuer == self.node_did:
                    value += amount
                    continue
                rule = self.acceptance(issuer)
                if not rule or not rule["enabled"]:
                    if plan.get("agreement"):
                        # The exact bilateral refund is an explicit, signed acceptance.
                        continue
                    raise ValueError("POINTS_ISSUER_NOT_ACCEPTED")
                used = self.store.get("points_accept_usage", digest([today, issuer]), 0)
                held = sum(r["incoming"].get(issuer, 0) for r in self.store.items("points_consents").values()
                           if r.get("state") == "OPEN" and r.get("day") == today)
                if amount > rule["per_trade"] or amount + used + held > rule["daily"]:
                    raise ValueError("POINTS_ACCEPTANCE_LIMIT")
                rules[issuer] = rule
                value += amount * rule["numerator"] // rule["denominator"]
            if self.node_did != plan["author_did"] and outgoing:
                if set(outgoing) != {self.node_did} or value < outgoing[self.node_did]:
                    raise ValueError("POINTS_OWNER_AUTHORIZATION_REQUIRED")
            offer = plan.get("offer")
            if offer and offer["provider_did"] == self.node_did:
                policy = self.service(offer["service_id"])
                mode = plan["terms"]["mode"]
                if (policy != offer.get("points_policy") or not policy or not policy["enabled"]
                        or mode not in policy["modes"] or plan["terms"]["amount_minor"] != policy["amount"]):
                    raise ValueError("POINTS_SERVICE_POLICY_CHANGED")
                if mode == "PAY" and value < policy["amount"]:
                    raise ValueError("POINTS_SERVICE_UNDERPAID")
                if mode in {"EARN", "DEBT"} and not self.settings()["issuance_enabled"]:
                    raise ValueError("POINTS_ISSUANCE_STOPPED")
            record = signed({"v": "a2n-points-consent/1", "author_did": self.node_did,
                "plan_id": key, "plan_digest": digest(plan), "acceptance": rules, "at": self.now()}, self.signer)
            self.store.put("points_consents", key, {"plan": plan, "record": record, "incoming": dict(incoming),
                "day": today, "state": "OPEN"})
            return record

    def prepare(self, plan, consents):
        validate_plan(plan, self.verifier)
        parties = participants(plan)
        if set(consents) != set(parties):
            raise ValueError("POINTS_ALL_CONSENTS_REQUIRED")
        for party, record in consents.items():
            verify(record, "a2n-points-consent/1", self.verifier, party)
            if record["plan_id"] != plan["plan_id"] or record["plan_digest"] != digest(plan):
                raise ValueError("POINTS_CONSENT_MISMATCH")
        key = plan["plan_id"]
        with self.store.tx():
            old = self.store.get("points_transactions", key)
            if old:
                if old["plan"] != plan:
                    raise ValueError("POINTS_PLAN_MISMATCH")
                if old["state"] == "ABORTED":
                    raise ValueError("POINTS_TRANSACTION_ABORTED")
                return old["prepared"]
            validate_plan(plan, self.verifier, now=self.now(), fresh=True)
            own = self.store.get("points_consents", key)
            if not own or own["record"] != consents.get(self.node_did) or own["state"] != "OPEN":
                raise ValueError("POINTS_LOCAL_CONSENT_REQUIRED")
            if sum(r["state"] == "PREPARED" for r in self.store.items("points_transactions").values()) >= 32:
                raise ValueError("POINTS_PENDING_LIMIT")
            agreement = plan.get("agreement")
            if agreement and plan["operations"][0]["issuer_did"] == self.node_did:
                proposal_id = agreement["proposal"]["message_id"]
                prior = self.store.get("points_refund_claims", proposal_id)
                if prior and prior != key:
                    original = self.store.get("points_transactions", prior)
                    if not original or original["state"] != "ABORTED":
                        raise ValueError("POINTS_REFUND_ALREADY_RESERVED")
                self.store.put("points_refund_claims", proposal_id, key)
            deltas, debts = defaultdict(int), defaultdict(int)
            for op in plan["operations"]:
                if op["issuer_did"] != self.node_did:
                    continue
                kind, amount = op["kind"], op["amount"]
                if kind == "TRANSFER":
                    deltas[op["from_did"]] -= amount; deltas[op["to_did"]] += amount
                elif kind in {"DEBT", "REPAY"}:
                    debts[op["buyer_did"]] += amount if kind == "DEBT" else -amount
            debits = {holder: -delta for holder, delta in deltas.items() if delta < 0}
            for holder, amount in debits.items():
                if self.balance(holder)["available"] < amount:
                    raise ValueError("POINTS_INSUFFICIENT_BALANCE")
            for holder, delta in deltas.items():
                pending_credit = sum(max(0, r.get("deltas", {}).get(holder, 0))
                    + sum(o["amount"] for o in r["plan"]["operations"] if o["kind"] == "SERVICE_ISSUE" and o["issuer_did"] == self.node_did and holder == self.node_did)
                    for r in self.store.items("points_transactions").values() if r["state"] == "PREPARED")
                units(self.balance(holder)["amount"] + pending_credit + delta)
                if holder != self.node_did and delta > 0:
                    rule = consents[holder].get("acceptance", {}).get(self.node_did)
                    if rule and self.balance(holder)["amount"] + pending_credit + delta > rule["max_holding"]:
                        raise ValueError("POINTS_MAX_HOLDING")
            for buyer, delta in debts.items():
                reserved = sum((max(0, r.get("debts", {}).get(buyer, 0)) if delta > 0 else min(0, r.get("debts", {}).get(buyer, 0)))
                    for r in self.store.items("points_transactions").values() if r["state"] == "PREPARED")
                future = self.debt(buyer) + reserved + delta
                policy = (plan.get("offer") or {}).get("points_policy", {})
                if future < 0 or delta > 0 and future > policy.get("debt_limit", 0):
                    raise ValueError("POINTS_DEBT_LIMIT")
            issued = sum(o["amount"] for o in plan["operations"] if o["kind"] == "SERVICE_ISSUE" and o["issuer_did"] == self.node_did)
            if issued:
                pending = sum(sum(o["amount"] for o in r["plan"]["operations"] if o["kind"] == "SERVICE_ISSUE" and o["issuer_did"] == self.node_did)
                    + max(0, r.get("deltas", {}).get(self.node_did, 0))
                    for r in self.store.items("points_transactions").values() if r["state"] == "PREPARED")
                units(self.balance(self.node_did)["amount"] + pending + issued)
            prepared = signed({"v": "a2n-points-prepared/1", "author_did": self.node_did,
                "plan_id": key, "plan_digest": digest(plan), "consents_digest": digest(consents), "at": self.now()}, self.signer)
            self.store.put("points_transactions", key, {"plan": plan, "prepared": prepared,
                "consents": consents, "state": "PREPARED", "debits": debits, "deltas": dict(deltas), "debts": dict(debts)})
            return prepared

    def apply(self, plan, decision):
        validate_plan(plan, self.verifier)
        verify(decision, "a2n-points-decision/1", self.verifier, plan["author_did"])
        key = plan["plan_id"]
        if decision.get("plan_id") != key or decision.get("plan_digest") != digest(plan) or decision.get("decision") not in {"COMMIT", "ABORT"}:
            raise ValueError("POINTS_DECISION_MISMATCH")
        if decision["decision"] == "COMMIT":
            prepared = decision.get("prepared", {})
            if set(prepared) != set(participants(plan)):
                raise ValueError("POINTS_ALL_PREPARED_REQUIRED")
            for party, record in prepared.items():
                verify(record, "a2n-points-prepared/1", self.verifier, party)
                if record["plan_id"] != key or record["plan_digest"] != digest(plan):
                    raise ValueError("POINTS_PREPARE_MISMATCH")
            offer = plan.get("offer")
            if offer:
                proof = verify(decision.get("delivery"), "a2n-points-delivery/1", self.verifier, offer["provider_did"])
                if proof.get("plan_id") != key or proof.get("trade_uid") != offer["trade_uid"] or proof.get("execution") != "DELIVERED":
                    raise ValueError("POINTS_REAL_DELIVERY_REQUIRED")
        with self.store.tx():
            row = self.store.get("points_transactions", key)
            if row and row.get("decision"):
                if row["decision"] != decision:
                    raise ValueError("POINTS_DECISION_CONFLICT")
                return row["receipt"]
            if decision["decision"] == "COMMIT" and (not row or row["state"] != "PREPARED"):
                raise ValueError("POINTS_LOCAL_PREPARATION_REQUIRED")
            if row and decision["decision"] == "COMMIT" and decision["prepared"][self.node_did] != row["prepared"]:
                raise ValueError("POINTS_PREPARE_MISMATCH")
            if row and decision["decision"] == "COMMIT" and any(p.get("consents_digest") != digest(row["consents"]) for p in decision["prepared"].values()):
                raise ValueError("POINTS_CONSENT_MISMATCH")
            changes = dict((row or {}).get("deltas", {}))
            if decision["decision"] == "COMMIT":
                for op in plan["operations"]:
                    if op["issuer_did"] == self.node_did and op["kind"] == "SERVICE_ISSUE":
                        changes[self.node_did] = changes.get(self.node_did, 0) + op["amount"]
                for holder, delta in changes.items():
                    self.store.put("points_balances", holder, units(self.balance(holder)["amount"] + delta))
                for buyer, delta in row["debts"].items():
                    self.store.put("points_debts", buyer, units(self.debt(buyer) + delta))
            own = self.store.get("points_consents", key)
            if own:
                if decision["decision"] == "COMMIT":
                    for issuer, amount in own["incoming"].items():
                        usage_key = digest([own["day"], issuer])
                        self.store.put("points_accept_usage", usage_key, self.store.get("points_accept_usage", usage_key, 0) + amount)
                self.store.put("points_consents", key, {**own, "state": "COMMITTED" if decision["decision"] == "COMMIT" else "ABORTED"})
            receipt = signed({"v": "a2n-points-receipt/1", "author_did": self.node_did, "plan_id": key,
                "decision_digest": digest(decision), "state": "COMMITTED" if decision["decision"] == "COMMIT" else "ABORTED",
                "applied_at": self.now()}, self.signer)
            self.store.put("points_transactions", key, {**(row or {}), "plan": plan, "state": receipt["state"],
                "decision": decision, "receipt": receipt})
            self.store.put("points_journal", key, {"plan_id": key, "decision": decision, "receipt": receipt})
            return receipt

    def summary(self):
        decimals = lambda r: {**r, **{k + "_decimal": str(v) for k, v in r.items() if type(v) is int and k != "revision"}}
        return {"method": METHOD, "issuer_did": self.node_did, "settings": self.settings(),
                "balances": [self.balance(h) for h in self.store.items("points_balances")],
                "debts": self.store.items("points_debts"), "debt_decimals": {k: str(v) for k, v in self.store.items("points_debts").items()},
                "services": [decimals(r) for r in self.store.items("points_services").values()],
                "acceptance": [decimals(r) for r in self.store.items("points_acceptance").values()],
                "transactions": [{"plan_id": k, "state": r["state"]} for k, r in self.store.items("points_transactions").items()]}
