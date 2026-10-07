"""Durable x402 authorization flow: verify -> execute -> settle -> respond.

The facade is framework independent. Stores must be node-owned encrypted
LocalStore instances. A nonce can execute one bound request only, even on restart.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hmac
import time

from ..trade_facts import digest
from .protocol import (MAX_BODY, PAYMENT_REQUIRED, PAYMENT_RESPONSE, address,
    check_extensions, decode_header, encode_header, validate_required, validate_requirement, validate_payload, validate_settlement)


@dataclass
class HTTPResult:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


class X402ResourceServer:
    def __init__(self, store, facilitator, verifier, observer, *, now=None):
        self.store, self.facilitator = store, facilitator
        self.verifier, self.observer, self.now = verifier, observer, now or time.time
        for key, row in store.items("x402_server_payments").items():
            if row["state"] in {"VERIFYING", "EXECUTING", "EXECUTED", "SETTLING"}:
                store.put("x402_server_payments", key, {**row, "state": "UNKNOWN", "reason": "PROCESS_RESTARTED"})

    def challenge(self, required, *, error="PAYMENT-SIGNATURE header is required"):
        required = validate_required(required)
        kinds = self.facilitator.supported().get("kinds", [])
        accepts = []
        for req in required["accepts"]:
            try:
                validate_requirement(req)
            except ValueError:
                continue
            if any(type(k.get("x402Version")) is int and k["x402Version"] == 2
                   and k.get("scheme") == req["scheme"] and k.get("network") == req["network"]
                   and (k.get("extra") or {}).get("assetTransferMethod", "eip3009") == "eip3009"
                   for k in kinds if isinstance(k, dict)):
                accepts.append(req)
        if not accepts:
            raise ValueError("X402_FACILITATOR_UNSUPPORTED")
        required = {**required, "accepts": accepts, "error": error}
        return HTTPResult(402, b"{}", {PAYMENT_REQUIRED: encode_header(required), "Content-Type": "application/json"})

    def handle(self, required, signature, handler, *, method="POST", body=b"", replay_token=None):
        required = validate_required(required)
        if not signature:
            return self.challenge(required)
        if method not in {"GET", "POST"} or not isinstance(body, bytes) or len(body) > MAX_BODY:
            raise ValueError("X402_INVALID_REQUEST")
        payload = validate_payload(decode_header(signature))
        check_extensions(required, payload)
        accepted = payload["accepted"]
        if accepted not in required["accepts"]:
            raise ValueError("X402_REQUIREMENT_MISMATCH")
        if payload.get("resource", required["resource"]).get("url") != required["resource"]["url"]:
            raise ValueError("X402_RESOURCE_MISMATCH")
        auth = payload["payload"]["authorization"]
        if replay_token is not None and (not isinstance(replay_token, str) or not 32 <= len(replay_token) <= 256):
            raise ValueError("X402_INVALID_REPLAY_TOKEN")
        if not self.verifier.verify_signature(payload):
            raise ValueError("X402_INVALID_SIGNATURE")
        key = digest([accepted["network"], address(accepted["asset"]), address(auth["from"]), auth["nonce"].lower()])
        fingerprint = digest([required["resource"]["url"], method, base64.b64encode(body).decode(), accepted])
        with self.store.tx():
            prior = self.store.get("x402_server_payments", key)
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ValueError("X402_AUTHORIZATION_REPLAY")
                # Signatures/nonces can become public calldata. Only a separate
                # private application credential may retrieve a cached result.
                if (not replay_token or not prior.get("replay_token_digest") or not hmac.compare_digest(
                        digest(replay_token), prior["replay_token_digest"])):
                    return HTTPResult(409, b'{"error":"X402_AUTHORIZATION_ALREADY_USED"}')
                return self._response(prior, required)
            validate_payload(payload, accepted, now=self.now())
            row = {"key": key, "state": "VERIFYING", "request_fingerprint": fingerprint,
                   "replay_token_digest": digest(replay_token) if replay_token else None,
                   "payload": payload, "created_at": self.now()}
            self.store.put("x402_server_payments", key, row)
        try:
            anchor = self.observer.anchor(payload)
            verification = self.facilitator.verify(payload, accepted)
            if (verification.get("isValid") is not True or verification.get("payer") is not None
                    and address(verification["payer"]) != address(auth["from"])):
                return self._finish(row, "FAILED", required, reason="VERIFICATION_REJECTED")
            row = {**row, "anchor": anchor, "state": "EXECUTING"}
            self.store.put("x402_server_payments", key, row)
            result = handler()
            if (not isinstance(result, HTTPResult) or type(result.status) is not int
                    or not 100 <= result.status <= 599 or not isinstance(result.body, bytes)
                    or len(result.body) > MAX_BODY):
                raise ValueError("X402_INVALID_HANDLER_RESULT")
            safe_headers = {k: v for k, v in result.headers.items()
                            if k.lower() not in {PAYMENT_REQUIRED.lower(), PAYMENT_RESPONSE.lower(), "payment-signature"}}
            encode_header({"headers": safe_headers})
            row = {**row, "result": {"status": result.status, "body_base64": base64.b64encode(result.body).decode(),
                                     "headers": safe_headers}, "state": "EXECUTED"}
            self.store.put("x402_server_payments", key, row)
            if not 200 <= result.status < 300:
                return self._finish(row, "EXECUTION_FAILED", required)
            row = {**row, "state": "SETTLING"}
            self.store.put("x402_server_payments", key, row)
            receipt = validate_settlement(self.facilitator.settle(payload, accepted), payload)
            row = {**row, "settlement_hint": receipt}
            self.store.put("x402_server_payments", key, row)
            observed = self.observer.observe(payload, anchor, receipt)
            if observed.get("state") == "CONFIRMED":
                receipt = validate_settlement(observed["settlement"], payload)
                if receipt["success"] is True:
                    return self._finish({**row, "settlement": receipt}, "CONFIRMED", required)
            return self._finish(row, "UNKNOWN", required, reason="SETTLEMENT_UNCONFIRMED")
        except Exception:
            # The resource may already have executed or the transaction may have
            # broadcast. Neither is retried merely because a response was lost.
            return self._finish(row, "UNKNOWN", required, reason="EXTERNAL_RESULT_UNKNOWN")

    def _finish(self, row, state, required, **extra):
        row = {**row, **extra, "state": state, "updated_at": self.now()}
        self.store.put("x402_server_payments", row["key"], row)
        return self._response(row, required)

    def _response(self, row, required):
        if row["state"] in {"CONFIRMED", "EXECUTION_FAILED"}:
            result = row["result"]
            headers = dict(result["headers"])
            if row["state"] == "CONFIRMED":
                headers[PAYMENT_RESPONSE] = encode_header(row["settlement"])
            return HTTPResult(result["status"], base64.b64decode(result["body_base64"]), headers)
        if row["state"] == "FAILED":
            return HTTPResult(402, b"{}", {PAYMENT_REQUIRED: encode_header({**required, "error": "payment verification failed"})})
        return HTTPResult(503, b'{"error":"X402_RESULT_UNKNOWN","retry_payment":false}', {"Content-Type": "application/json"})

    def reconcile(self, key):
        with self.store.tx():
            row = self.store.get("x402_server_payments", key)
            if not row:
                raise ValueError("X402_SERVER_PAYMENT_NOT_FOUND")
            if row["state"] != "UNKNOWN" or "anchor" not in row:
                return row
        observed = self.observer.observe(row["payload"], row["anchor"], row.get("settlement_hint"))
        if observed.get("state") == "CONFIRMED" and row.get("result"):
            receipt = validate_settlement(observed["settlement"], row["payload"])
            if receipt["success"]:
                with self.store.tx():
                    row = {**row, "state": "CONFIRMED", "settlement": receipt, "updated_at": self.now()}
                    self.store.put("x402_server_payments", key, row)
        return row
