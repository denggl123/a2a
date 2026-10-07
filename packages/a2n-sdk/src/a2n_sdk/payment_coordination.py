"""Bilateral payment terms. Crypto, wallet custody and transports are injected."""
from __future__ import annotations

import time
from .contracts import input_digest
from .experience import signed, unsigned
from .payments import minor
from .trade_facts import digest, trade_uid

CAP_VERSION = "a2n-payment-capabilities/1"
QUERY_VERSION = "a2n-payment-query/1"
OFFER_VERSION = "a2n-payment-offer/1"
PLAN_VERSION = "a2n-payment-plan/1"
ACCEPT_VERSION = "a2n-payment-acceptance/1"


class PaymentCoordinator:
    def __init__(self, store, payments, *, node_did, signer, verifier, now=None):
        self.store, self.payments = store, payments
        self.node_did, self.signer, self.verifier = node_did, signer, verifier
        self.now = now or time.time

    def verify(self, record, version, author=None, *, fresh=False):
        if (not isinstance(record, dict) or record.get("v") != version
                or author is not None and record.get("author_did") != author
                or not self.verifier(record.get("proof"), unsigned(record))):
            raise ValueError("INVALID_PAYMENT_SIGNATURE")
        if fresh:
            start, end = record.get("issued_at"), record.get("expires_at")
            if (isinstance(start, bool) or not isinstance(start, (int, float))
                    or isinstance(end, bool) or not isinstance(end, (int, float))
                    or not 0 < end - start <= 300 or not start - 30 <= self.now() < end):
                raise ValueError("PAYMENT_TERMS_EXPIRED")
        return record

    def capabilities(self, methods, wallet_binding=None):
        now = self.now()
        return signed({"v": CAP_VERSION, "author_did": self.node_did, "methods": methods,
            "wallet_binding": wallet_binding, "platform_commission_minor": 0,
            "issued_at": now, "expires_at": now + 300}, self.signer)

    def query(self, request, *, provider_did, service_id, capabilities):
        now = self.now()
        return signed({"v": QUERY_VERSION, "author_did": self.node_did, "buyer_did": self.node_did,
            "provider_did": provider_did, "service_id": service_id, "task_id": request.task_id,
            "skill": request.skill,
            "trade_uid": trade_uid(self.node_did, provider_did, service_id, request.task_id),
            "input_digest": input_digest(request), "capabilities": capabilities,
            "issued_at": now, "expires_at": now + 300}, self.signer)

    def offer(self, query, *, source_card, options, free_reason=""):
        self.verify(query, QUERY_VERSION, fresh=True)
        buyer = query["author_did"]
        if (query.get("buyer_did") != buyer or query.get("provider_did") != self.node_did
                or query.get("trade_uid") != trade_uid(buyer, self.node_did, query["service_id"], query["task_id"])):
            raise ValueError("PAYMENT_QUERY_PARTIES_MISMATCH")
        cap = self.verify(query["capabilities"], CAP_VERSION, buyer, fresh=True)
        common = []
        for option in options:
            minor(option["amount_minor"])
            if not option["amount_minor"] or option.get("platform_commission_minor") != 0:
                raise ValueError("INVALID_PAYMENT_PRICE")
            if any(all(option.get(k) == m.get(k) for k in ("method", "network", "asset", "currency", "flow"))
                   for m in cap.get("methods", [])):
                common.append(option)
        now = self.now()
        active=[r for r in self.store.items("payment_offers").values() if r["expires_at"]>now]
        for old in active:
            if old["query_digest"]==digest(query) and old["source_card_digest"]==digest(source_card) and old["free_reason"]==free_reason:
                return old
        if len(active)>=512 or sum(r["buyer_did"]==buyer for r in active)>=16:
            raise ValueError("PAYMENT_QUOTE_CAPACITY_LIMIT")
        core = {"v": OFFER_VERSION, "author_did": self.node_did, "provider_did": self.node_did,
            **{k: query[k] for k in ("buyer_did", "service_id", "task_id", "trade_uid", "input_digest", "skill")},
            "source_card_digest": digest(source_card), "service_version": str(source_card.get("version") or ""),
            "query_digest": digest(query), "buyer_capabilities": cap, "options": [] if free_reason else common,
            "free_reason": free_reason, "payment_state": "NOT_REQUIRED" if free_reason else "NEGOTIABLE" if common else "UNAVAILABLE",
            "platform_commission_minor": 0, "issued_at": now, "expires_at": now + 300}
        core["offer_id"] = "po_" + digest(core)
        record = signed(core, self.signer)
        self.store.put("payment_offers", record["offer_id"], record)
        return record

    def plan(self, offer, *, option_index, fee_cap_minor, driver_id):
        self.verify(offer, OFFER_VERSION, fresh=True)
        if offer.get("buyer_did") != self.node_did or offer.get("provider_did") != offer["author_did"]:
            raise ValueError("PAYMENT_PLAN_PARTIES_MISMATCH")
        business=digest({k:offer[k] for k in ("buyer_did","provider_did","service_id","task_id","trade_uid","input_digest","skill","source_card_digest","service_version")})
        previous=self.store.get("payment_business_terms",offer["trade_uid"])
        if previous and previous!=business:
            raise ValueError("PAYMENT_ORDER_BUSINESS_CONFLICT")
        if isinstance(option_index, bool) or not isinstance(option_index, int) or not 0 <= option_index < len(offer["options"]):
            raise ValueError("PAYMENT_OPTION_UNAVAILABLE")
        option = offer["options"][option_index]
        minor(fee_cap_minor)
        if option["flow"] == "authorization" and fee_cap_minor:
            raise ValueError("X402_BUYER_FEE_MUST_BE_ZERO")
        driver = self.payments.drivers.get(driver_id)
        if not driver or option["currency"] not in driver.capabilities()["currencies"]:
            raise ValueError("PAYMENT_DRIVER_UNAVAILABLE")
        payer = (offer["buyer_capabilities"].get("wallet_binding") or {}).get("address")
        if not payer:
            raise ValueError("PAYMENT_WALLET_BINDING_REQUIRED")
        core = {"v": PLAN_VERSION, "author_did": self.node_did, "offer": offer,
            "terms": option, "fee_cap_minor": fee_cap_minor, "payer": payer, "option_index": option_index,
            "issued_at": self.now(), "expires_at": offer["expires_at"]}
        core["plan_id"] = "pp_" + digest(core)
        record = signed(core, self.signer)
        self.store.put("payment_plans", record["plan_id"], {"record": record, "driver_id": driver_id, "state": "PROPOSED"})
        self.store.put("payment_business_terms",offer["trade_uid"],business)
        return record

    def accept(self, plan):
        self.verify(plan, PLAN_VERSION)
        old = self.store.get("payment_acceptances", plan["plan_id"])
        if old:
            if old["plan"]!=plan:
                raise ValueError("PAYMENT_PLAN_MISMATCH")
            return old["record"]
        self.verify(plan, PLAN_VERSION, fresh=True)
        offer = self.verify(plan["offer"], OFFER_VERSION, self.node_did, fresh=True)
        if (self.store.get("payment_offers", offer["offer_id"]) != offer
                or plan["author_did"] != offer["buyer_did"]
                or plan.get("terms") not in offer["options"]
                or plan.get("payer") != (offer["buyer_capabilities"].get("wallet_binding") or {}).get("address")
                or plan.get("plan_id") != "pp_" + digest({k: v for k, v in unsigned(plan).items() if k != "plan_id"})):
            raise ValueError("PAYMENT_PLAN_MISMATCH")
        minor(plan["fee_cap_minor"])
        if plan["terms"]["flow"] == "authorization" and plan["fee_cap_minor"]:
            raise ValueError("X402_BUYER_FEE_MUST_BE_ZERO")
        prior = self.store.get("payment_provider_orders", offer["trade_uid"])
        if prior and prior != plan["plan_id"]:
            state = self.store.get("payment_acceptances", prior) or {}
            if state.get("state") not in {"FAILED", "REVOKED"}:
                raise ValueError("PAYMENT_ORDER_LOCKED")
            old_offer=state["plan"]["offer"]
            if any(offer[k]!=old_offer[k] for k in ("input_digest","skill","source_card_digest","service_version","buyer_did","provider_did","service_id","task_id")):
                raise ValueError("PAYMENT_ORDER_BUSINESS_CONFLICT")
        record = signed({"v": ACCEPT_VERSION, "author_did": self.node_did, "plan_id": plan["plan_id"],
            "plan_digest": digest(plan), "trade_uid": offer["trade_uid"], "accepted_at": self.now()}, self.signer)
        self.store.put("payment_acceptances", plan["plan_id"], {"record": record, "plan": plan, "state": "ACCEPTED"})
        self.store.put("payment_provider_orders", offer["trade_uid"], plan["plan_id"])
        return record

    def activate(self, plan_id, acceptance, *, command_id):
        row = self.store.get("payment_plans", plan_id)
        if not row:
            raise ValueError("PAYMENT_PLAN_NOT_FOUND")
        plan = row["record"]
        offer, terms = plan["offer"], plan["terms"]
        self.verify(acceptance, ACCEPT_VERSION, offer["provider_did"])
        if acceptance.get("plan_digest") != digest(plan) or acceptance.get("plan_id") != plan_id:
            raise ValueError("PAYMENT_ACCEPTANCE_MISMATCH")
        with self.payments.atomic():
            intent = self.payments.create(trade_uid=offer["trade_uid"], currency=terms["currency"],
                amount_minor=terms["amount_minor"], payee=terms["payee"], counterparty_did=offer["provider_did"],
                command_id=command_id, driver_id=row["driver_id"], plan_id=plan_id, fee_cap_minor=plan["fee_cap_minor"])
            self.store.put("payment_plans", plan_id, {**row, "state": "ACCEPTED", "acceptance": acceptance, "intent_id": intent["intent_id"]})
            return intent

    def summary(self):
        orders = []
        for order in self.store.items("payment_orders").values():
            attempts = []
            for k in order["attempts"]:
                intent=self.payments.get(k)
                plan=self.store.get("payment_plans",intent.get("plan_id") or "") or {}
                attempts.append({**intent,"payment_flow":((plan.get("record") or {}).get("terms") or {}).get("flow")})
            last = attempts[-1]
            orders.append({**order, "state": last["state"], "attempts": attempts})
        return {"implemented": True, "version": PLAN_VERSION, "platform_commission_minor": 0,
            "capabilities": self.payments.capabilities(), "orders": orders[-100:],
            "automatic_payment": False, "unknown_locks_order": True}
