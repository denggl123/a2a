"""Compose x402 with the node's existing encrypted payment and exposure books."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re

from a2n_sdk.trade_facts import digest
from a2n_sdk.x402 import X402Buyer, X402ResourceServer
from a2n_sdk.x402.protocol import (PAYMENT_SIGNATURE, PAYMENT_RESPONSE, address,
    bounded_json, decode_header, encode_header, header, validate_settlement)
from .http import HTTPClient, HTTPFacilitator


def environment_config():
    path = os.environ.get("A2N_X402_CONFIG")
    if not path:
        return None, None
    with Path(path).open("rb") as f:
        config = bounded_json(f.read(65537), limit=65536)
    signer = None
    keystore = os.environ.get("A2N_X402_KEYSTORE")
    if keystore:
        from .evm import EvmSigner
        password = os.environ.get("A2N_X402_KEYSTORE_PASSWORD")
        if not password:
            raise ValueError("X402_KEYSTORE_PASSWORD_REQUIRED")
        signer = EvmSigner.from_keystore(keystore, password)
    return config, signer


class X402Driver:
    def __init__(self, store, config, signer=None, *, http=None, observers=None):
        from .evm import EvmObserver, EvmRPC
        if not isinstance(config, dict) or set(config) - {"assets", "allow_http", "timeout"}:
            raise ValueError("X402_INVALID_CONFIG")
        assets = config.get("assets")
        if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
            raise ValueError("X402_INVALID_ASSET_CONFIG")
        self.store, self.signer = store, signer
        self.http = http or HTTPClient(timeout=config.get("timeout", 15), allow_http=config.get("allow_http", False))
        self.assets, self.observers = {}, {}
        for cfg in assets:
            if not isinstance(cfg, dict) or set(cfg) - {"currency", "network", "asset", "name", "version", "rpc_url", "confirmations"}:
                raise ValueError("X402_INVALID_ASSET_CONFIG")
            currency = cfg.get("currency")
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{2,12}", currency) or currency in self.assets:
                raise ValueError("X402_DUPLICATE_OR_INVALID_CURRENCY")
            if not isinstance(cfg.get("network"), str) or not re.fullmatch(r"eip155:[1-9][0-9]{0,19}", cfg["network"]):
                raise ValueError("X402_UNSUPPORTED_NETWORK")
            address(cfg.get("asset"))
            if not all(isinstance(cfg.get(k), str) and 1 <= len(cfg[k]) <= 128 for k in ("name", "version")):
                raise ValueError("X402_MISSING_EIP712_DOMAIN")
            if any(c["network"] == cfg["network"] and address(c["asset"]) == address(cfg["asset"]) for c in self.assets.values()):
                raise ValueError("X402_DUPLICATE_ASSET")
            self.http.validate_url(cfg.get("rpc_url"))
            self.assets[currency] = dict(cfg)
            self.observers[currency] = (observers or {}).get(currency) or EvmObserver(
                EvmRPC(cfg["rpc_url"], self.http), network=cfg["network"], asset=cfg["asset"],
                name=cfg["name"], version=cfg["version"], confirmations=cfg.get("confirmations", 3))
        self.driver_id = "x402-v2-eip3009:" + digest([config, signer.address if signer else None])
        # A lost process before persisting authorization never sent an HTTP
        # payment. A persisted authorization remains potentially spendable.
        for key, row in store.items("x402_attempts").items():
            if row.get("driver_id") == self.driver_id and row["state"] == "PREPARING":
                store.put("x402_attempts", key, {**row, "state": "PREPARATION_FAILED"})

    def capabilities(self):
        return {"driver_id": self.driver_id, "currencies": list(self.assets) if self.signer else [],
                "refund": False, "x402_version": 2, "scheme": "exact", "platform_commission_minor": 0}

    def validate_url(self, url):
        self.http.validate_url(url)

    def currency_for(self, requirement):
        for currency, cfg in self.assets.items():
            if (requirement["network"] == cfg["network"] and address(requirement["asset"]) == address(cfg["asset"])
                    and requirement["extra"]["name"] == cfg["name"] and requirement["extra"]["version"] == cfg["version"]):
                return currency
        raise ValueError("X402_UNTRUSTED_ASSET_OR_DOMAIN")

    def anchor(self, payload):
        return self.observers[self.currency_for(payload["accepted"])].anchor(payload)

    def observe(self, payload, anchor, hint=None):
        return self.observers[self.currency_for(payload["accepted"])].observe(payload, anchor, hint)

    def _observation(self, intent, result):
        observation = {"driver_id": self.driver_id, "verified_source": True,
                       "state": result.get("state", "UNKNOWN"), "reason": result.get("reason", "LEDGER_OBSERVATION")}
        if result.get("state") == "CONFIRMED":
            auth = self.store.get("x402_authorizations", intent["intent_id"])["payload"]
            receipt = validate_settlement(result["settlement"], auth)
            if not receipt["success"]:
                return {**observation, "state": "UNKNOWN"}
            observation.update(currency=intent["currency"], amount_minor=intent["amount_minor"],
                payee=intent["payee"], reference=receipt["transaction"], settlement=receipt)
        elif result.get("state") == "FAILED":
            observation["definitive"] = result.get("definitive") is True
        return observation

    def submit(self, intent):
        key = intent["intent_id"]
        plan = self.store.get("x402_plans", key)
        if not plan or not self.signer:
            raise ValueError("X402_PLAN_OR_WALLET_MISSING")
        req = plan["accepted"]
        if (self.currency_for(req) != intent["currency"] or int(req["amount"]) != intent["amount_minor"]
                or req["payTo"] != intent["payee"] or intent["driver_id"] != self.driver_id):
            raise ValueError("X402_INTENT_TERMS_MISMATCH")
        with self.store.tx():
            prior_attempt = self.store.get("x402_attempts", key)
            if not prior_attempt:
                self.store.put("x402_attempts", key, {"state": "PREPARING", "driver_id": self.driver_id})
        if prior_attempt:
            return self.query(intent)
        try:
            payload = self.signer.sign(plan["required"], req)
            anchor = self.anchor(payload)
            # Commit the only authorization BEFORE it can leave the node.
            with self.store.tx():
                self.store.put("x402_authorizations", key, {"payload": payload, "anchor": anchor})
                self.store.put("x402_attempts", key, {"state": "EXPOSED", "driver_id": self.driver_id})
        except Exception:
            self.store.put("x402_attempts", key, {"state": "PREPARATION_FAILED", "driver_id": self.driver_id})
            return self._observation(intent, {"state": "FAILED", "definitive": True, "reason": "AUTHORIZATION_NOT_EXPOSED"})
        hint = None
        try:
            response = self.http.request(plan["method"], plan["url"],
                body=base64.b64decode(plan["body_base64"]) if plan["method"] == "POST" else None,
                headers={PAYMENT_SIGNATURE: encode_header(payload), "Content-Type": "application/json",
                         "Idempotency-Key": plan["replay_token"]})
            self.store.put("x402_deliveries", key, {"state": "DELIVERED" if 200 <= response.status < 300 else "HTTP_ERROR",
                "status": response.status, "body_base64": base64.b64encode(response.body).decode()})
            encoded = header(response.headers, PAYMENT_RESPONSE)
            if encoded:
                hint = validate_settlement(decode_header(encoded), payload)
                self.store.put("x402_settlement_hints", key, hint)
        except Exception:
            # Both a timeout and a misleading payment response need ledger
            # reconciliation; the endpoint is not invoked a second time.
            if not self.store.get("x402_deliveries", key):
                self.store.put("x402_deliveries", key, {"state": "UNKNOWN"})
        return self._observation(intent, self.observe(payload, anchor, hint))

    def query(self, intent):
        key = intent["intent_id"]
        authorization = self.store.get("x402_authorizations", key)
        if not authorization:
            attempt = self.store.get("x402_attempts", key)
            return self._observation(intent, {"state": "FAILED" if attempt and attempt["state"] == "PREPARATION_FAILED" else "UNKNOWN",
                "definitive": True, "reason": "AUTHORIZATION_NOT_EXPOSED"})
        return self._observation(intent, self.observe(authorization["payload"], authorization["anchor"],
                self.store.get("x402_settlement_hints", key)))

    def refund(self, intent):
        raise ValueError("X402_REFUND_REQUIRES_SEPARATE_AUTHORIZATION")


class X402NodeService:
    def __init__(self, store, config=None, signer=None, *, http=None, observers=None):
        self.store = store
        self.driver = X402Driver(store, config, signer, http=http, observers=observers) if config is not None else None
        self.buyer = None

    def bind(self, payments):
        if self.driver and self.driver.driver_id in payments.drivers:
            self.buyer = X402Buyer(self.store, payments, self.driver)

    def status(self):
        return {"implemented": True, "x402_version": 2, "scheme": "exact", "asset_transfer_method": "eip3009",
            "payment_flow": "authorization", "wallet_configured": bool(self.driver and self.driver.signer),
            "state": "CONFIGURED" if self.buyer and self.driver.signer else "NOT_CONFIGURED",
            "assets": [{k: v for k, v in cfg.items() if k not in {"rpc_url"}}
                       for cfg in self.driver.assets.values()] if self.driver else [],
            "platform_commission_minor": 0, "automatic_payment": False, "refund": False}

    def command(self, path, body):
        if not self.buyer:
            raise ValueError("X402_NOT_CONFIGURED")
        if path == "/v1/x402/prepare":
            return 201, self.buyer.prepare(**body)
        prefix = "/v1/x402/intents/"
        if path.startswith(prefix):
            key, sep, action = path.removeprefix(prefix).rpartition("/")
            self.buyer.outcome(key)
            if action == "submit":
                self.buyer.payments.submit(key)
            elif action == "reconcile":
                self.buyer.payments.reconcile(key)
            else:
                raise ValueError("X402_UNKNOWN_COMMAND")
            return 200, self.buyer.outcome(key)
        raise ValueError("X402_UNKNOWN_COMMAND")

    def resource_server(self, facilitator_url):
        if not self.driver:
            raise ValueError("X402_ASSETS_NOT_CONFIGURED")
        from .evm import EvmVerifier
        return X402ResourceServer(self.store, HTTPFacilitator(facilitator_url, self.driver.http), EvmVerifier(), self.driver)
