"""Explicitly approved, durable x402 buyer plans backed by PaymentBook/RiskBook."""
from __future__ import annotations

import base64
import secrets

from ..trade_facts import digest
from .protocol import (MAX_BODY, decode_header, validate_required, validate_requirement,
                       resource_url)


class X402Buyer:
    def __init__(self, store, payments, driver):
        self.store, self.payments, self.driver = store, payments, driver

    def prepare(self, *, required_header, accepted, url, method, body_base64="",
                trade_uid, counterparty_did, command_id):
        required = validate_required(decode_header(required_header))
        accepted = validate_requirement(accepted)
        if accepted not in required["accepts"] or required["resource"]["url"] != resource_url(url):
            raise ValueError("X402_APPROVAL_MISMATCH")
        if method not in {"GET", "POST"} or not isinstance(body_base64, str) or len(body_base64) > (MAX_BODY + 2) // 3 * 4:
            raise ValueError("X402_INVALID_REQUEST")
        try:
            body = base64.b64decode(body_base64, validate=True)
        except ValueError as exc:
            raise ValueError("X402_INVALID_REQUEST_BODY") from exc
        if len(body) > MAX_BODY or method == "GET" and body:
            raise ValueError("X402_INVALID_REQUEST_BODY")
        currency = self.driver.currency_for(accepted)
        self.driver.validate_url(url)
        if not self.driver.capabilities()["currencies"]:
            raise ValueError("X402_WALLET_NOT_CONFIGURED")
        intent_id = "pi_" + digest([trade_uid, "payment"])
        plan = {"intent_id": intent_id, "required": required, "accepted": accepted,
                "url": url, "method": method, "body_base64": body_base64}
        plan["fingerprint"] = digest(plan)
        with self.payments.atomic():
            prior = self.store.get("x402_plans", intent_id)
            if prior and prior["fingerprint"] != plan["fingerprint"]:
                raise ValueError("X402_REQUEST_CONFLICT")
            row = self.payments.create(trade_uid=trade_uid, currency=currency,
                amount_minor=int(accepted["amount"]), payee=accepted["payTo"],
                counterparty_did=counterparty_did, command_id=command_id, driver_id=self.driver.driver_id)
            if not prior:
                # Application retry credential, distinct from the onchain nonce.
                # It never appears in the EIP-3009 transaction or public receipt.
                plan["replay_token"] = secrets.token_hex(32)
                self.store.put("x402_plans", intent_id, plan)
            return row

    def outcome(self, intent_id):
        row = self.payments.get(intent_id)
        if row is None or not self.store.get("x402_plans", intent_id):
            raise ValueError("X402_INTENT_NOT_FOUND")
        delivery = self.store.get("x402_deliveries", intent_id)
        return {"payment": row, "delivery": delivery,
                "platform_commission_minor": 0}
