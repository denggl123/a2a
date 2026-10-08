"""Signed bounded admission conditions, frozen before dispatch."""
from __future__ import annotations

import time

from .experience import signed, unsigned
from .trade_facts import digest

AUTH_VERSION = "a2n-admission-authorization/1"
VERSION = "a2n-admission-contract/1"


def input_digest(request):
    from .messages import canonical_message
    message = canonical_message(request)
    if isinstance(message, dict):
        message = {k: v for k, v in message.items() if k != "messageId"}
    excluded = {"a2nPeerRequest", "a2nTradeAuthorization", "a2nAdmissionAuthorization",
                "_a2n_verified_peer", "_a2n_wire_task_id", "_a2n_transport", "_a2n_local_admission"}
    excluded.update({"_a2n_owner_invocation", "_a2n_anonymous", "a2nPaymentPlan", "a2nPointsPrepared", "_a2n_payment_verified"})
    metadata = {k: v for k, v in request.metadata.items() if k not in excluded}
    return digest({"skill": request.skill, "message": message,
                   "context_id": request.context_id, "metadata": metadata})


class ContractBook:
    def __init__(self, store, *, node_did, signer, verifier, now=None):
        self.store, self.node_did, self.signer, self.verifier = store, node_did, signer, verifier
        self.now = now or time.time

    def authorize_free(self, request, *, trade_uid, provider_did, service_id, version):
        now = self.now()
        return signed({"v": AUTH_VERSION, "author_did": self.node_did, "buyer_did": self.node_did,
            "provider_did": provider_did, "service_id": service_id, "service_version": version,
            "trade_uid": trade_uid, "input_digest": input_digest(request), "maximum_minor": 0,
            "payment_mode": "FREE_ONLY", "commission_minor": 0, "issued_at": now, "expires_at": now + 60}, self.signer)

    def admit_free(self, request, context, *, source_card):
        uid = context["trade_uid"]
        old = self.store.get("admission_contracts", uid)
        if old:
            return old
        auth = request.metadata.get("a2nAdmissionAuthorization")
        if auth:
            expected = {"buyer_did": context["buyer_did"], "author_did": context["buyer_did"],
                "provider_did": self.node_did, "service_id": context["service_id"],
                "service_version": context.get("version", ""), "trade_uid": uid,
                "input_digest": input_digest(request), "maximum_minor": 0, "payment_mode": "FREE_ONLY", "commission_minor": 0}
            if (auth.get("v") != AUTH_VERSION or not self.verifier(auth.get("proof"), unsigned(auth))
                    or any(auth.get(k) != v for k, v in expected.items())
                    or not auth.get("issued_at", 0) - 30 <= self.now() < auth.get("expires_at", 0)
                    or not 0 < auth.get("expires_at", 0) - auth.get("issued_at", 0) <= 60):
                raise ValueError("ADMISSION_CONDITIONS_MISMATCH")
        elif context.get("buyer_authorization"):
            raise ValueError("ADMISSION_AUTHORIZATION_REQUIRED")
        if not context.get("free"):
            raise ValueError("PAYMENT_UNAVAILABLE: 免费条件不能授权收费")
        core = {"v": VERSION, "author_did": self.node_did, "provider_did": self.node_did,
            "buyer_did": context.get("buyer_did", ""), "service_id": context["service_id"],
            "service_version": context.get("version", ""), "trade_uid": uid, "input_digest": input_digest(request),
            "source_card_digest": digest(source_card), "free_reason": context["free_reason"], "amount_minor": 0,
            "currency": "", "payment_mode": "FREE", "platform_commission_minor": 0, "channel_cost_minor": 0,
            "cancellation": "BEST_EFFORT_WITH_SEPARATE_ACK", "quality_disagreement": "BILATERAL_NEGOTIATION",
            "refund": "REQUIRES_EXPLICIT_AUTHORIZATION_AND_VERIFIED_CHANNEL", "delivery_window": "BEST_EFFORT",
            "buyer_authorization": auth, "bilateral_conditions_verified": bool(auth), "admitted_at": self.now()}
        record = signed(core, self.signer)
        self.store.put("admission_contracts", uid, record)
        return record

    def verify_delivery_contract(self, record, request, *, provider_did, service_id, trade_uid):
        if (not isinstance(record, dict) or record.get("v") != VERSION
                or not self.verifier(record.get("proof"), unsigned(record))
                or record.get("author_did") != provider_did or record.get("provider_did") != provider_did
                or record.get("buyer_did") != self.node_did or record.get("trade_uid") != trade_uid
                or record.get("service_id") != service_id or record.get("input_digest") != input_digest(request)
                or record.get("buyer_authorization") != request.metadata.get("a2nAdmissionAuthorization")
                or record.get("platform_commission_minor") != 0):
            raise ValueError("INVALID_ADMISSION_CONTRACT")
        plan = request.metadata.get("a2nPaymentPlan")
        if plan:
            if (record.get("payment_plan_id") != plan.get("plan_id") or record.get("amount_minor") != plan["terms"]["amount_minor"]
                    or record.get("currency") != plan["terms"]["currency"] or record.get("payment_mode") != plan["terms"]["method"]):
                raise ValueError("INVALID_PAID_ADMISSION_CONTRACT")
        elif record.get("amount_minor") != 0 or record.get("payment_mode") != "FREE":
            raise ValueError("INVALID_ADMISSION_CONTRACT")
        self.store.put("received_contracts", trade_uid, record)
        return record

    def authorize_paid(self, request, plan):
        now = self.now()
        offer, terms = plan["offer"], plan["terms"]
        return signed({"v": AUTH_VERSION, "author_did": self.node_did, "buyer_did": self.node_did,
            "provider_did": offer["provider_did"], "service_id": offer["service_id"],
            "service_version": offer["service_version"], "trade_uid": offer["trade_uid"],
            "input_digest": input_digest(request), "maximum_minor": terms["amount_minor"],
            "payment_mode": terms["method"], "payment_plan_id": plan["plan_id"],
            "commission_minor": 0, "issued_at": now, "expires_at": now + 60}, self.signer)

    def admit_paid(self, request, context, *, source_card, plan):
        offer, terms = plan["offer"], plan["terms"]
        auth = request.metadata.get("a2nAdmissionAuthorization")
        expected = {"author_did": context["buyer_did"], "buyer_did": context["buyer_did"],
            "provider_did": self.node_did, "trade_uid": context["trade_uid"], "input_digest": input_digest(request),
            "service_id": context["service_id"], "service_version": context["version"],
            "maximum_minor": terms["amount_minor"], "payment_mode": terms["method"],
            "payment_plan_id": plan["plan_id"], "commission_minor": 0}
        if (not isinstance(auth, dict) or auth.get("v") != AUTH_VERSION
                or not self.verifier(auth.get("proof"), unsigned(auth))
                or any(auth.get(k) != v for k, v in expected.items())
                or not auth.get("issued_at", 0) - 30 <= self.now() < auth.get("expires_at", 0)
                or not 0 < auth.get("expires_at", 0) - auth.get("issued_at", 0) <= 60
                or offer["source_card_digest"] != digest(source_card)):
            raise ValueError("PAID_ADMISSION_CONDITIONS_MISMATCH")
        record = signed({"v": VERSION, "author_did": self.node_did, "provider_did": self.node_did,
            "buyer_did": context["buyer_did"], "trade_uid": context["trade_uid"], "service_id": context["service_id"],
            "service_version": context["version"], "source_card_digest": digest(source_card),
            "input_digest": input_digest(request), "amount_minor": terms["amount_minor"], "currency": terms["currency"],
            "payment_mode": terms["method"], "payment_plan_id": plan["plan_id"], "free_reason": "PAID",
            "platform_commission_minor": 0, "channel_cost_minor": 0, "buyer_fee_cap_minor": plan["fee_cap_minor"],
            "buyer_authorization": auth, "bilateral_conditions_verified": True,
            "quality_disagreement": "BILATERAL_NEGOTIATION", "refund": "EXPLICIT_SEPARATE_PAYMENT",
            "cancellation": "BEST_EFFORT_WITH_SEPARATE_ACK", "admitted_at": self.now()}, self.signer)
        self.store.put("admission_contracts", context["trade_uid"], record)
        return record
