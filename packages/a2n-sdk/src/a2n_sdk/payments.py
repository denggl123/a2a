"""Durable payment intentions and bounded exposure; drivers own external verification."""
from __future__ import annotations

import math
import re
from contextlib import contextmanager
import threading
import time
from typing import Protocol

from .trade_facts import digest


class PaymentDriverPort(Protocol):
    def capabilities(self) -> dict: ...
    def submit(self, intent: dict) -> dict: ...
    def query(self, intent: dict) -> dict: ...
    def refund(self, intent: dict) -> dict: ...


def minor(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**256:
        raise ValueError("INVALID_MINOR_AMOUNT")
    return value


def parse_minor(value):
    """Accept exact UI decimal strings without floats or exponent coercion."""
    if isinstance(value,str) and re.fullmatch(r"0|[1-9][0-9]{0,77}",value):
        value=int(value)
    return minor(value)


class RiskBook:
    def __init__(self, store, *, now=None):
        self.store, self.now = store, now or time.time

    def policy(self):
        return self.store.get("risk_policy", "owner") or {"revision": 0, "limits": {}, "max_pending": 2}

    def configure(self, limits, *, max_pending=2, expected_revision):
        if not isinstance(limits, dict) or len(limits) > 16:
            raise ValueError("INVALID_RISK_LIMITS")
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or not 1 <= max_pending <= 64:
            raise ValueError("INVALID_PENDING_LIMIT")
        for currency, cfg in limits.items():
            if not isinstance(currency, str) or not currency.isascii() or not currency.isalpha() or not 2 <= len(currency) <= 12 or currency.upper() != currency:
                raise ValueError("INVALID_CURRENCY")
            if not isinstance(cfg, dict) or set(cfg) != {"per_trade", "total_exposure", "daily_spend", "per_counterparty"}:
                raise ValueError("INVALID_RISK_LIMIT_FIELDS")
            for amount in cfg.values():
                minor(amount)
        with self.store.tx():
            if expected_revision != self.policy()["revision"] or isinstance(expected_revision, bool):
                raise ValueError("REV_CONFLICT")
            policy = {"revision": expected_revision + 1, "limits": limits, "max_pending": max_pending, "at": self.now()}
            self.store.put("risk_policy", "owner", policy)
            self.store.put("risk_policy_versions", str(policy["revision"]), policy)
            return policy

    def reserve(self, intent_id, *, currency, amount, counterparty):
        minor(amount)
        with self.store.tx():
            existing = self.store.get("risk_reservations", intent_id)
            if existing:
                if (existing["currency"], existing["amount_minor"], existing["counterparty_did"]) != (currency, amount, counterparty):
                    raise ValueError("RESERVATION_CONFLICT")
                return existing
            policy = self.policy()
            cfg = policy["limits"].get(currency)
            if cfg is None or amount > cfg["per_trade"]:
                raise ValueError("RISK_LIMIT")
            all_rows = list(self.store.items("risk_reservations").values())
            active = [r for r in all_rows if r["state"] == "ACTIVE"]
            if len(active) >= policy["max_pending"]:
                raise ValueError("RISK_CAPACITY_LIMIT")
            current = [r for r in active if r["currency"] == currency]
            if (sum(r["amount_minor"] for r in current) + amount > cfg["total_exposure"]
                    or sum(r["amount_minor"] for r in current if r["counterparty_did"] == counterparty) + amount > cfg["per_counterparty"]):
                raise ValueError("RISK_LIMIT")
            period = max(int(self.now() // 86400), self.store.get("risk_clock", "period") or 0)
            spent = sum(r["amount_minor"] for r in all_rows if r["currency"] == currency
                        and (r["state"] == "ACTIVE" or r["state"] == "CONSUMED" and r["period"] == period))
            if spent + amount > cfg["daily_spend"]:
                raise ValueError("RISK_LIMIT")
            row = {"intent_id": intent_id, "currency": currency, "amount_minor": amount,
                "counterparty_did": counterparty, "state": "ACTIVE", "period": period,
                "policy_revision": policy["revision"], "at": self.now()}
            self.store.put("risk_clock", "period", period)
            self.store.put("risk_reservations", intent_id, row)
            return row

    def finish(self, intent_id, *, confirmed, spent_minor=None):
        row = self.store.get("risk_reservations", intent_id)
        if row and row["state"] == "ACTIVE":
            spent = row["amount_minor"] if confirmed else 0
            if spent_minor is not None:
                minor(spent_minor)
                if spent_minor > row["amount_minor"]:
                    raise ValueError("SPEND_EXCEEDS_RESERVATION")
                spent = spent_minor
            self.store.put("risk_reservations", intent_id, {**row, "reserved_minor": row["amount_minor"],
                "amount_minor": spent, "state": "CONSUMED" if confirmed or spent else "RELEASED"})


class PaymentBook:
    def __init__(self, store, risk, driver=None, *, now=None):
        self.store, self.risk, self.driver = store, risk, driver
        self.now = now or time.time
        self._lock = threading.RLock()
        self.on_change = None
        self.drivers = {}
        if driver is not None:
            self.register(driver)
        # A submit interrupted by process death is never submitted a second time.
        for key, row in store.items("payment_intents").items():
            if row["state"] in {"SUBMITTING", "QUERYING"}:
                store.put("payment_intents", key, {**row, "state": "UNKNOWN", "reason": "PROCESS_RESTARTED"})

    def register(self, driver):
        cap = driver.capabilities()
        if not isinstance(cap, dict) or not cap.get("driver_id") or not isinstance(cap.get("currencies"), list):
            raise ValueError("INVALID_PAYMENT_DRIVER_CAPABILITIES")
        old = self.drivers.get(cap["driver_id"])
        if old is not None and old is not driver:
            raise ValueError("PAYMENT_DRIVER_CONFLICT")
        self.drivers[cap["driver_id"]] = driver

    def order(self, trade_uid):
        return self.store.get("payment_orders", trade_uid)

    def cancel(self, intent_id):
        with self.atomic():
            row = self.get(intent_id)
            if row and row["state"] == "FAILED":
                return row
            if not row or row["state"] != "READY":
                raise ValueError("PAYMENT_ALREADY_EXPOSED")
            row = {**row, "state": "FAILED", "reason": "OWNER_CANCELED_BEFORE_SUBMIT", "revision": row["revision"] + 1}
            self.risk.finish(intent_id, confirmed=False)
            self.store.put("payment_intents", intent_id, row)
            if self.on_change:
                self.on_change(row)
            return row

    def capabilities(self):
        if not self.drivers:
            return {"available": False, "state": "NOT_CONFIGURED", "currencies": [], "refund": False}
        cap = (self.driver or next(iter(self.drivers.values()))).capabilities()
        if not isinstance(cap, dict) or not isinstance(cap.get("driver_id"), str) or not cap["driver_id"] or not isinstance(cap.get("currencies"), list):
            raise ValueError("INVALID_PAYMENT_DRIVER_CAPABILITIES")
        methods = [d.capabilities() for d in self.drivers.values()]
        available = any(m["currencies"] for m in methods)
        return {"available": available, "state": "CONFIGURED" if available else "NOT_CONFIGURED", "driver_id": cap["driver_id"],
                "currencies": sorted({c for m in methods for c in m["currencies"]}),
                "refund": any(m.get("refund") is True for m in methods), "methods": methods}

    @contextmanager
    def atomic(self):
        """Bind adapter context and an intent with the same lock order as create."""
        with self._lock, self.store.tx():
            yield

    def create(self, *, trade_uid, currency, amount_minor, payee, counterparty_did, command_id, driver_id=None, plan_id="", fee_cap_minor=0):
        minor(amount_minor)
        minor(fee_cap_minor)
        if not amount_minor or not all(isinstance(v, str) and 1 <= len(v) <= 256 for v in (trade_uid, currency, payee, counterparty_did, command_id)):
            raise ValueError("INVALID_PAYMENT_INTENT")
        fingerprint = digest([trade_uid, currency, amount_minor, payee, counterparty_did, driver_id, plan_id, fee_cap_minor])
        key = "pi_" + digest([trade_uid, "payment"])
        selected = self.drivers.get(driver_id) if driver_id else self.driver
        cap = selected.capabilities() if selected else {"currencies": []}
        with self._lock, self.store.tx():
            order = self.order(trade_uid)
            if order:
                key = order["attempts"][-1]
            old = self.get(key)
            prior = self.store.get("payment_commands", command_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(prior["intent_id"])
            if old:
                if old["fingerprint"] == fingerprint:
                    return old
                if old["state"] != "FAILED":
                    raise ValueError("PAYMENT_INTENT_CONFLICT")
                key = "pi_" + digest([trade_uid, "payment", len(order["attempts"]) if order else 1])
            if not selected or currency not in cap["currencies"]:
                raise ValueError("PAYMENT_UNAVAILABLE")
            if order and order["counterparty_did"] != counterparty_did:
                raise ValueError("PAYMENT_COUNTERPARTY_CONFLICT")
            reservation = self.risk.reserve(key, currency=currency, amount=amount_minor + fee_cap_minor, counterparty=counterparty_did)
            row = {"intent_id": key, "trade_uid": trade_uid, "currency": currency, "amount_minor": amount_minor,
                "amount_minor_decimal":str(amount_minor),
                "payee": payee, "counterparty_did": counterparty_did, "driver_id": cap["driver_id"],
                "state": "READY", "reference": None, "fingerprint": fingerprint, "plan_id": plan_id,
                "fee_cap_minor": fee_cap_minor,
                "policy_revision": reservation["policy_revision"], "created_at": self.now(), "revision": 1}
            self.store.put("payment_intents", key, row)
            self.store.put("payment_commands", command_id, {"intent_id": key, "fingerprint": fingerprint})
            self.store.put("payment_orders", trade_uid, {"trade_uid": trade_uid, "counterparty_did": counterparty_did,
                "attempts": [*(order["attempts"] if order else ([old["intent_id"]] if old else [])), key]})
            if self.on_change:
                self.on_change(row)
            return row

    def get(self, intent_id):
        return self.store.get("payment_intents", intent_id)

    def submit(self, intent_id):
        return self._execute(intent_id, submit=True)

    def reconcile(self, intent_id):
        return self._execute(intent_id, submit=False)

    def _execute(self, intent_id, *, submit):
        with self._lock, self.store.tx():
            row = self.get(intent_id)
            if not row:
                raise ValueError("PAYMENT_INTENT_NOT_FOUND")
            if row["state"] in {"CONFIRMED", "FAILED", "SUBMITTING", "QUERYING"} or submit and row["state"] != "READY" or not submit and row["state"] == "READY":
                return row
            driver = self.drivers.get(row["driver_id"])
            if not driver:
                raise ValueError("PAYMENT_DRIVER_UNAVAILABLE")
            self.store.put("payment_intents", intent_id, {**row, "state": "SUBMITTING" if submit else "QUERYING"})
        try:
            observation = driver.submit(dict(row)) if submit else driver.query(dict(row))
        except Exception:
            observation = {"state": "UNKNOWN", "reason": "DRIVER_RESULT_UNKNOWN"}
        with self._lock, self.store.tx():
            state, reason = "UNKNOWN", str((observation or {}).get("reason") or "UNVERIFIED_DRIVER_RESULT")[:200] if isinstance(observation, dict) else "INVALID_DRIVER_RESULT"
            source = observation.get("verified_source") if isinstance(observation,dict) else None
            valid = isinstance(observation, dict) and (source is True or isinstance(source,str) and 1 <= len(source) <= 200) and observation.get("driver_id") == row["driver_id"]
            fee = observation.get("fee_minor") if isinstance(observation, dict) else None
            if fee is not None and (isinstance(fee,bool) or not isinstance(fee,int) or not 0 <= fee <= row.get("fee_cap_minor",0)):
                valid = False
            if valid and observation.get("state") == "CONFIRMED":
                if (observation.get("currency") == row["currency"] and observation.get("amount_minor") == row["amount_minor"]
                        and observation.get("payee") == row["payee"] and isinstance(observation.get("reference"), str) and observation["reference"]):
                    state, reason = "CONFIRMED", "VERIFIED_BY_DRIVER"
            elif valid and observation.get("state") == "FAILED" and observation.get("definitive") is True:
                state, reason = "FAILED", "DEFINITIVE_DRIVER_FAILURE"
            current = self.get(intent_id)
            result = {**current, "state": state, "reason": reason, "revision": current["revision"] + 1,
                "last_observation": observation, "updated_at": self.now()}
            if valid and observation.get("reference"):
                result["reference"] = observation["reference"]
            if state in {"CONFIRMED", "FAILED"}:
                spent = (row["amount_minor"] if state == "CONFIRMED" else 0) + fee if fee is not None else None
                self.risk.finish(intent_id, confirmed=state == "CONFIRMED",spent_minor=spent)
            self.store.put("payment_observations", f"{intent_id}:{result['revision']}", observation)
            self.store.put("payment_intents", intent_id, result)
            if self.on_change:
                self.on_change(result)
            return result
