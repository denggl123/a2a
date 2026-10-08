"""Business decisions and Agent execution, above payment coordination."""
from __future__ import annotations

from dataclasses import asdict
import json
from .card import card_did, verify_card
import time

from a2n_sdk.contracts import input_digest
from a2n_sdk.experience import signed, unsigned
from a2n_sdk.payment_coordination import QUERY_VERSION, OFFER_VERSION
from a2n_sdk.points import METHOD as POINTS, participants, verify
from a2n_sdk.ports import CallRequest, CallOutcome
from a2n_sdk.pricing import is_free, price_book
from a2n_sdk.trade_facts import digest, trade_uid, axes, delivered
from .feedback_identity import signer_for, verifier_for


def points_method(issuer):
    return {"method": POINTS, "network": "a2n", "asset": issuer, "currency": "points:" + issuer, "flow": "after_delivery"}


class TradeService:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.signer, self.verifier = signer_for(daemon.identity), verifier_for()

    def voluntary_free(self, scope, card):
        policy = self.daemon.points_node.book.service(scope)
        return is_free(card) and not (policy and policy["enabled"])

    def quote(self, body):
        payments = self.daemon.payment_coordination
        scope = body.get("projection_id")
        item = self.daemon.runtime.imported.get(scope)
        if not item or not verify_card(item.network_card)[0]:
            raise ValueError("SIGNED_NODE_AGENT_REQUIRED")
        request = CallRequest(**body.get("request", {}))
        if len(json.dumps(asdict(request),ensure_ascii=False,allow_nan=False).encode())>65536:
            raise ValueError("PAYMENT_COORDINATION_INPUT_TOO_LARGE: 单次输入上限 64 KiB")
        if request.metadata.get("a2nPaymentPlan"):
            raise ValueError("NEW_QUOTE_REQUIRES_UNBOUND_REQUEST")
        card = item.network_card
        sid = ((card.get("x-a2n") or {}).get("projection") or {}).get("service_id")
        if not sid or "/a2a/" not in str(card.get("url")):
            raise ValueError("PAYMENT_ENDPOINT_UNAVAILABLE")
        if not request.skill:
            request.skill = next((str(s.get("id") or "") for s in card.get("skills", []) if isinstance(s, dict)), "")
        request.metadata["a2nResolutionEndpoint"] = self.daemon.management.discovery_public_base or self.daemon.runtime.local_base_url
        request.metadata["a2nSettlementRoute"] = self.daemon.settlement_transport.own_route()
        query = self.query(request, provider_did=card_did(card), service_id=sid, capabilities=payments.capability(card_did(card)))
        base = card["url"].rsplit("/a2a/", 1)[0]
        route = self.daemon.settlement_transport.card_route(card)
        offer = payments._remote(base, "quote", {"query": query, "skill": request.skill}, target_did=card_did(card), route=route)
        payments.coordinator.verify(offer, OFFER_VERSION, card_did(card), fresh=True)
        if (offer.get("query_digest") != digest(query) or offer.get("service_version") != str(card.get("version") or "")
                or any(offer.get(k)!=query[k] for k in ("buyer_did","provider_did","service_id","task_id","trade_uid","input_digest","skill"))
                or offer.get("platform_commission_minor") != 0):
            raise ValueError("PAYMENT_QUOTE_MISMATCH")
        self.store.put("payment_buyer_quotes", offer["offer_id"], {"offer": offer, "request": asdict(request), "scope": scope, "base": base, "settlement_route": route})
        self.store.put("payment_offer_channels", offer["offer_id"], payments.channel_id)
        return offer

    def quote_context(self, query):
        scope = query["service_id"]
        binding = self.daemon.runtime.bindings.get(scope)
        if not binding or not binding.enabled or not binding.metadata.get("listed", True):
            raise ValueError("AGENT_NOT_FOUND")
        card = binding.source_card
        free = ("FREE_INITIAL" if not self.daemon.trials.status(scope)["ended"] else
                "FREE_RECONNECT" if self.daemon.reconnect.available(scope) else
                "FREE_VOLUNTARY" if self.voluntary_free(scope, card) else "")
        return {"card": card, "free_reason": free, "prices": price_book(card)}

    def public_quote(self, body):
        query = verify(body["query"], QUERY_VERSION, self.verifier)
        verify(query["capabilities"], "a2n-payment-capabilities/1", self.verifier, query["author_did"])
        if (query.get("buyer_did") != query["author_did"] or query.get("provider_did") != self.daemon.identity.did
                or query.get("trade_uid") != trade_uid(query["buyer_did"], self.daemon.identity.did, query["service_id"], query["task_id"])
                or not 0 < query["expires_at"] - query["issued_at"] <= 300
                or not query["issued_at"] - 30 <= time.time() < query["expires_at"]):
            raise ValueError("BUSINESS_QUERY_MISMATCH")
        context = self.quote_context(query)
        if query.get("settlement_route"):
            self.store.put("settlement_counterparty_routes", query["author_did"], query["settlement_route"])
        if context["free_reason"]:
            return 200, self.free_offer(query, context)
        return self.daemon.payment_coordination.public("quote", body, quote_context=context)

    def command(self, path, body):
        if path == "/v1/trades/quote":
            return 200, self.quote(body)
        if path == "/v1/trades/free-execute":
            return 200, self.free_execute(body["offer_id"])
        if path == "/v1/trades/prepare":
            return 201, self.daemon.payment_coordination.prepare(body)
        prefix = "/v1/trades/plans/"
        if path.startswith(prefix):
            return self.daemon.payment_coordination.command(path.replace(prefix, "/v1/payment-coordination/plans/", 1), body)
        raise ValueError("UNKNOWN_TRADE_COMMAND")

    def query(self, request, *, provider_did, service_id, capabilities):
        now = time.time()
        return signed({"v": QUERY_VERSION, "author_did": self.daemon.identity.did, "buyer_did": self.daemon.identity.did,
            "provider_did": provider_did, "service_id": service_id, "task_id": request.task_id, "skill": request.skill,
            "trade_uid": trade_uid(self.daemon.identity.did, provider_did, service_id, request.task_id),
            "input_digest": input_digest(request), "capabilities": capabilities,
            "settlement_route": request.metadata.get("a2nSettlementRoute"),
            "issued_at": now, "expires_at": now + 300}, self.signer)

    def free_offer(self, query, context):
        now = time.time()
        active = [r for r in self.store.items("business_free_offers").values() if r["expires_at"] > now]
        for old in active:
            if old["query_digest"] == digest(query) and old["source_card_digest"] == digest(context["card"]) and old["free_reason"] == context["free_reason"]:
                return old
        pending = []
        for offer in active:
            task = self.daemon.peer_exchange.journal_task_id(offer["service_id"], offer["task_id"], offer["buyer_did"])
            call = self.store.task(offer["service_id"], task)
            if not call or not call.get("outcome") or axes(call["outcome"])["execution"] not in {"DELIVERED", "FAILED", "CANCELED"}:
                pending.append(offer)
        active = pending
        if len(active) >= 512 or sum(r["buyer_did"] == query["buyer_did"] for r in active) >= 16:
            raise ValueError("BUSINESS_QUOTE_CAPACITY_LIMIT")
        core = {"v": OFFER_VERSION, "author_did": self.daemon.identity.did, "provider_did": self.daemon.identity.did,
            **{k: query[k] for k in ("buyer_did", "service_id", "task_id", "trade_uid", "input_digest", "skill")},
            "source_card_digest": digest(context["card"]), "service_version": str(context["card"].get("version") or ""),
            "query_digest": digest(query), "buyer_capabilities": query["capabilities"], "options": [],
            "free_reason": context["free_reason"], "payment_state": "NOT_REQUIRED", "platform_commission_minor": 0,
            "issued_at": now, "expires_at": now + 300}
        core["offer_id"] = "bf_" + digest(core)
        record = signed(core, self.signer)
        self.store.put("business_free_offers", core["offer_id"], record)
        return record

    def points_offer(self, query, context):
        policy = self.daemon.points_node.book.service(query["service_id"])
        if not policy or not policy["enabled"]:
            return None
        modes = policy["modes"]
        if not self.daemon.points_node.book.settings()["issuance_enabled"]:
            modes = [m for m in modes if m == "PAY"]
        now = time.time()
        descriptor = points_method(self.daemon.identity.did)
        core = {"v": "a2n-points-offer/1", "author_did": self.daemon.identity.did,
            "provider_did": self.daemon.identity.did, **{k: query[k] for k in ("buyer_did", "service_id", "task_id", "trade_uid", "input_digest", "skill")},
            "source_card_digest": digest(context["card"]), "service_version": str(context["card"].get("version") or ""),
            "points_policy": policy, "issued_at": now, "expires_at": now + 300,
            "options": [{**descriptor, "mode": mode, "amount_minor": policy["amount"], "amount_minor_decimal": str(policy["amount"]),
                         "payee": self.daemon.identity.did, "platform_commission_minor": 0} for mode in modes]}
        core["offer_id"] = "pf_" + digest(core)
        record = signed(core, self.signer)
        self.store.put("business_points_offers", record["offer_id"], record)
        return record

    def admit_offer(self, offer):
        binding = self.daemon.runtime.bindings.get(offer["service_id"])
        if not binding or not binding.enabled or not binding.metadata.get("listed", True) or digest(binding.source_card) != offer["source_card_digest"]:
            raise ValueError("AGENT_PRICE_CHANGED")
        task = self.daemon.peer_exchange.journal_task_id(offer["service_id"], offer["task_id"], offer["buyer_did"])
        admission = self.daemon.trials.admit(offer["service_id"], task, caller_did=offer["buyer_did"],
            provider_did=self.daemon.identity.did, voluntary_free=self.voluntary_free(offer["service_id"], binding.source_card))
        if admission["free"]:
            raise ValueError("FREE_SERVICE_REQUIRES_NEW_QUOTE")
        return admission

    def accept_points(self, plan):
        offer = plan.get("offer")
        if not offer or offer["provider_did"] != self.daemon.identity.did:
            return
        verify(offer, "a2n-points-offer/1", self.verifier, self.daemon.identity.did)
        with self.store.tx():
            existing = self.store.get("points_consents", plan["plan_id"])
            if existing:
                return
            if self.store.get("business_points_offers", offer["offer_id"]) != offer:
                raise ValueError("POINTS_BUSINESS_OFFER_REQUIRED")
            self.admit_offer(offer)
            native = self.store.get("payment_provider_orders", offer["trade_uid"])
            if native:
                previous = self.store.get("payment_acceptances", native)
                if previous and previous["state"] not in {"FAILED", "REVOKED"}:
                    raise ValueError("PAYMENT_ORDER_LOCKED")
            prior = self.store.get("business_points_orders", offer["trade_uid"])
            if prior and prior != plan["plan_id"]:
                raise ValueError("PAYMENT_ORDER_LOCKED")
            self.store.put("business_points_orders", offer["trade_uid"], plan["plan_id"])

    def prepare_points(self, quote, option, body):
        inner = option["points_offer"]
        command = body.get("command_id")
        old = self.store.get("points_plan_commands", command or "")
        fingerprint = digest(body)
        prior = self.store.get("business_points_prepare", command or "")
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return self.daemon.points.prepare(prior["plan_id"])
        if self.store.task(quote["scope"], quote["request"]["task_id"]):
            raise ValueError("ORIGINAL_TRADE_ALREADY_EXECUTED")
        with self.daemon.points._lock:
            provider = inner["provider_did"]
            peer_row = self.store.get("points_peers", provider) or {}
            quoted_route = quote.get("settlement_route", quote["base"])
            if provider != self.daemon.identity.did and (not peer_row or quoted_route != (peer_row.get("route") or peer_row.get("endpoint"))):
                route = quote.get("settlement_route", quote["base"])
                peer = (self.daemon.points_node.add_route(provider, route) if isinstance(route, dict) else
                    self.daemon.points_node.add_peer(route))
                if peer["did"] != provider:
                    raise ValueError("POINTS_PROVIDER_IDENTITY_MISMATCH")
            row = self.daemon.points.create_service(inner, option["points_option_index"], command_id=command,
                funding_issuer=body.get("funding_issuer"), max_cost=body.get("max_cost"))
            key = row["record"]["plan_id"]
            self.store.put("points_orders", key, {**row, "scope": quote["scope"], "request": quote["request"], "base": quote["base"], "settlement_route": quote.get("settlement_route", quote["base"])})
            self.store.put("business_points_prepare", command, {"fingerprint": fingerprint, "plan_id": key})
            return self.daemon.points.prepare(key)

    def free_execute(self, offer_id):
        row = self.store.get("payment_buyer_quotes", offer_id)
        if not row or row["offer"]["payment_state"] != "NOT_REQUIRED":
            raise ValueError("FREE_QUOTE_REQUIRED")
        offer = row["offer"]
        verify(offer, OFFER_VERSION, self.verifier, offer["provider_did"])
        if not offer["issued_at"] - 30 <= time.time() < offer["expires_at"]:
            raise ValueError("FREE_QUOTE_EXPIRED")
        return self.daemon.calls.invoke(row["scope"], CallRequest(**row["request"])).to_dict()

    def invoke_provider(self, scope, request):
        return self.daemon.calls.invoke(scope, request).to_dict()

    def execute(self, plan_id):
        if plan_id.startswith("pt_"):
            return self.execute_points(plan_id)
        payments = self.daemon.payment_coordination
        row = payments._row(plan_id)
        intent = self.daemon.payments.get(row["intent_id"])
        if row["record"]["terms"]["flow"] == "upfront" and intent["state"] != "CONFIRMED":
            raise ValueError("CONFIRMED_UPFRONT_PAYMENT_REQUIRED")
        if intent["state"] == "READY":
            payments.coordinator.verify(row["record"], "a2n-payment-plan/1", fresh=True)
        request = CallRequest(**row["request"])
        request.metadata["a2nPaymentPlan"] = row["record"]
        previous = self.store.task(row["scope"], request.task_id)
        previous_plan = ((previous or {}).get("request", {}).get("metadata") or {}).get("a2nPaymentPlan")
        if previous_plan and previous_plan["plan_id"] != plan_id:
            if not delivered(previous.get("outcome") or {}) or row["record"]["terms"]["flow"] != "upfront":
                raise ValueError("RECORDED_CALL_RECOVERY_REQUIRED")
            original = CallOutcome(**previous["outcome"])
            original.settlement = {"state": intent["state"], "reference": intent.get("reference")}
            original.metadata["supplemental_payment_acceptance"] = row["acceptance"]
            self.daemon.calls.observe_external_delivery(row["scope"], request.task_id, original)
            return original.to_dict()
        return self.daemon.calls.invoke(row["scope"], request).to_dict()

    def validate_points(self, scope, request, buyer):
        plan = request.metadata["a2nPaymentPlan"]
        offer = plan["offer"]
        row = self.store.get("points_transactions", plan["plan_id"])
        if (not row or row["plan"] != plan or row["state"] not in {"PREPARED", "COMMITTED"}
                or self.store.get("business_points_canceled", plan["plan_id"])):
            raise ValueError("POINTS_PREPARED_SERVICE_REQUIRED")
        if (buyer != offer["buyer_did"] or scope != offer["service_id"] or request.skill != offer["skill"]
                or (request.metadata.get("_a2n_wire_task_id") or request.task_id) != offer["task_id"]
                or input_digest(request) != offer["input_digest"]):
            raise ValueError("PAID_REQUEST_MISMATCH")
        proofs = request.metadata.get("a2nPointsPrepared") or {}
        if set(proofs) != set(participants(plan)):
            raise ValueError("POINTS_ALL_PREPARED_REQUIRED")
        for party, proof in proofs.items():
            verify(proof, "a2n-points-prepared/1", self.verifier, party)
            if proof.get("plan_digest") != digest(plan) or proof.get("consents_digest") != digest(row["consents"]):
                raise ValueError("POINTS_PREPARE_MISMATCH")
        return plan

    def delivery_proof(self, request, outcome, facts):
        plan = request.metadata.get("a2nPaymentPlan")
        if not plan or plan["terms"]["method"] != POINTS:
            return
        execution = axes(outcome.to_dict())["execution"]
        proof = signed({"v": "a2n-points-delivery/1", "author_did": self.daemon.identity.did,
            "plan_id": plan["plan_id"], "trade_uid": facts["trade_uid"], "execution": execution,
            "result_digest": digest(outcome.result), "at": time.time()}, self.signer)
        outcome.metadata["points_delivery"] = proof

    def execute_points(self, plan_id, *, reconcile=False):
        with self.daemon.points._lock:
            row = self.daemon.points.get(plan_id)
            if row["decision"]:
                row = self.daemon.points.reconcile(plan_id)
                self.daemon.trade_facts.financial(row["record"]["offer"]["trade_uid"], self.payment_state(row))
                return self.points_result(row)
            if row.get("execution_started") or reconcile:
                return self.recover_points(plan_id)
            row = self.daemon.points.prepare(plan_id)
            if row["state"] != "PREPARED":
                return self.points_result(row)
            request = CallRequest(**row["request"])
            request.metadata["a2nPaymentPlan"] = row["record"]
            request.metadata["a2nPointsPrepared"] = row["prepared"]
            self.daemon.points.coordinator.state(plan_id, "EXECUTING", execution_started=True)
        outcome = self.daemon.calls.invoke(row["scope"], request)
        proof = outcome.metadata.get("points_delivery")
        if proof:
            row = self.daemon.points.finish(plan_id, proof)
        else:
            row = self.daemon.points.coordinator.state(plan_id, "UNKNOWN", reason="SERVICE_DELIVERY_UNKNOWN")
        self.daemon.trade_facts.financial(row["record"]["offer"]["trade_uid"], self.payment_state(row))
        return self.points_result(row)

    @staticmethod
    def payment_state(row):
        return row["state"] if row["state"] in {"CONFIRMED", "FAILED", "UNKNOWN"} else "PENDING"

    def points_result(self, row):
        original = self.store.task(row.get("scope", ""), (row.get("request") or {}).get("task_id", ""))
        delivery = (original or {}).get("outcome")
        state = self.payment_state(row)
        if delivery:
            delivery = {**delivery, "settlement": {"state": state, "method": POINTS, "reference": row["record"]["plan_id"], "platform_commission_minor": 0}}
        return {"payment": {"state": state, "reference": row["record"]["plan_id"], "mode": row["record"]["terms"]["mode"]},
                "delivery": delivery, "order_state": row["state"]}

    def recover_points(self, plan_id):
        row = self.daemon.points.get(plan_id)
        if not row.get("execution_started"):
            return self.points_result(self.daemon.points.prepare(plan_id))
        plan = row["record"]
        payload = {"plan": plan}
        route = row.get("settlement_route", row["base"])
        body = {"payload": payload, "auth": self.daemon.points_node.auth(plan["offer"]["provider_did"], "trade-recover", payload)}
        result = (self.daemon.settlement_transport.json(plan["offer"]["provider_did"], route, "trades", "points-recover", body)
            if isinstance(route, dict) else self.daemon.points_node.http().json("POST", route.rstrip("/") + "/public/v1/trades/points-recover", body))
        verify(result, "a2n-points-business-recovery/1", self.verifier, plan["offer"]["provider_did"])
        if result.get("plan_id") != plan_id:
            raise ValueError("POINTS_RECOVERY_MISMATCH")
        delivery = result.get("delivery")
        if delivery and delivery.get("metadata", {}).get("points_delivery"):
            request = CallRequest(**row["request"])
            request.metadata["a2nPaymentPlan"] = plan
            request.metadata["a2nPointsPrepared"] = row["prepared"]
            self.daemon._prepare_trade_request(row["scope"], request)
            proof = delivery["metadata"]["points_delivery"]
            verify(proof, "a2n-points-delivery/1", self.verifier, plan["offer"]["provider_did"])
            if proof["result_digest"] != digest(delivery.get("result")):
                raise ValueError("POINTS_RECOVERY_RESULT_MISMATCH")
            outcome = CallOutcome(**delivery)
            outcome.task_id = request.task_id
            if proof["execution"] == "DELIVERED":
                self.daemon.contracts.verify_delivery_contract(outcome.metadata["admission_contract"], request,
                    provider_did=plan["offer"]["provider_did"], service_id=plan["offer"]["service_id"], trade_uid=plan["offer"]["trade_uid"])
            self.daemon.calls.observe_external_delivery(row["scope"], request.task_id, outcome)
            row = self.daemon.points.finish(plan_id, proof)
        else:
            row = self.daemon.points.coordinator.state(plan_id, "UNKNOWN", reason="SERVICE_DELIVERY_UNKNOWN")
        self.daemon.trade_facts.financial(plan["offer"]["trade_uid"], self.payment_state(row))
        return self.points_result(row)

    def public_recover(self, body):
        payload, auth = body["payload"], body["auth"]
        verify(auth, "a2n-points-request/1", self.verifier)
        plan = payload["plan"]
        from a2n_sdk.points import validate_plan
        validate_plan(plan, self.verifier)
        if (auth["author_did"] != plan["offer"]["buyer_did"] or auth["target_did"] != self.daemon.identity.did
                or auth["action"] != "trade-recover" or auth["payload_digest"] != digest(payload)
                or not auth["issued_at"] - 30 <= time.time() < auth["expires_at"]):
            raise ValueError("POINTS_RECOVERY_AUTHORIZATION_REQUIRED")
        offer = plan["offer"]
        task = self.daemon.peer_exchange.journal_task_id(offer["service_id"], offer["task_id"], offer["buyer_did"])
        outcome = self.daemon.calls.get(offer["service_id"], task, refresh_remote=True)
        if outcome is None:
            with self.store.tx():
                # A tombstone and admission use the same store transaction lock.
                # A late message cannot start after this definitive cancellation.
                if not self.store.task(offer["service_id"], task):
                    local = self.store.get("points_transactions", plan["plan_id"])
                    if not local or local.get("plan") != plan:
                        raise ValueError("POINTS_LOCAL_PREPARATION_REQUIRED")
                    self.store.put("business_points_canceled", plan["plan_id"], True)
                    outcome = CallOutcome(False, offer["task_id"], "CANCELED", error="原调用未接单，已禁止迟到请求执行")
                    self.delivery_proof(CallRequest(task_id=offer["task_id"], metadata={"a2nPaymentPlan": plan}), outcome,
                        {"trade_uid": offer["trade_uid"]})
        return signed({"v": "a2n-points-business-recovery/1", "author_did": self.daemon.identity.did,
            "plan_id": plan["plan_id"], "delivery": outcome.to_dict() if outcome else None}, self.signer)

    def refund_points(self, body, *, reconcile=False):
        proposal_id = body["proposal_id"]
        agreement = self.store.get("resolution_agreements", proposal_id)
        if not agreement or agreement["state"] != "BOTH_ACCEPTED" or agreement["proposal"]["body"]["action"] != "REFUND":
            raise ValueError("BILATERAL_REFUND_AGREEMENT_REQUIRED")
        proposal = agreement["proposal"]
        original_id = self.store.get("business_points_orders", proposal["trade_uid"])
        original = self.store.get("points_transactions", original_id or "")
        if not original or original["state"] != "COMMITTED" or original["plan"]["terms"]["mode"] != "PAY":
            raise ValueError("ORIGINAL_POINTS_PAYMENT_REQUIRED")
        plan = original["plan"]
        buyer = plan["offer"]["buyer_did"]
        last = plan["operations"][-1]
        amount = proposal["body"]["amount_minor"]
        if proposal["body"]["currency"] != "points:" + last["issuer_did"]:
            raise ValueError("REFUND_REQUIRES_ORIGINAL_RECEIVED_POINTS")
        prior = self.store.get("business_points_refunds", proposal_id)
        if not prior:
            if reconcile:
                raise ValueError("POINTS_REFUND_NOT_STARTED")
            consumed = sum(r["amount"] for r in self.store.items("business_points_refunds").values()
                if r["trade_uid"] == proposal["trade_uid"] and self.daemon.points.get(r["plan_id"])["state"] != "FAILED")
            if amount + consumed > last["amount"]:
                raise ValueError("POINTS_REFUND_EXCEEDS_RECEIVED_AMOUNT")
            ops = [{"kind": "TRANSFER", "issuer_did": last["issuer_did"], "from_did": self.daemon.identity.did,
                    "to_did": buyer, "amount": amount}]
            parties = participants({"author_did": self.daemon.identity.did, "operations": ops})
            row = self.daemon.points.coordinator.create(operations=ops, routes={p: plan["routes"][p] for p in parties},
                command_id="refund_" + proposal_id, agreement={"proposal": proposal, "acceptances": agreement["acceptances"]})
            prior = {"plan_id": row["record"]["plan_id"], "trade_uid": proposal["trade_uid"], "amount": amount}
            self.store.put("business_points_refunds", proposal_id, prior)
        row = self.daemon.points.get(prior["plan_id"])
        if row["decision"]:
            row = self.daemon.points.reconcile(prior["plan_id"])
        else:
            row = self.daemon.points.prepare(prior["plan_id"])
            if row["state"] == "PREPARED":
                row = self.daemon.points.finish(prior["plan_id"], None)
        state = self.payment_state(row)
        self.daemon.trade_facts.financial(proposal["trade_uid"], state, refund=True)
        self.store.put("resolution_agreements", proposal_id, {**agreement, "execution": "REFUND_" + state})
        return {"state": state, "reference": prior["plan_id"], "amount_minor_decimal": str(amount), "currency": proposal["body"]["currency"]}
