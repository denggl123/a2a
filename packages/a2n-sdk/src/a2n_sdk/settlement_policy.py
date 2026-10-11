"""Local settlement preferences: pure matching, no wallet or network access."""
from __future__ import annotations

import copy
from .payments import minor

FIELDS = {"method", "network", "asset", "currency", "flow", "mode"}
LIMITS = {"max_amount_minor", "fee_cap_minor", "funding_issuer", "max_cost"}
METHODS = {"evm-native/1", "x402/2", "a2n-points/1"}


def matches(option, selector):
    return all(option.get(key) == value for key, value in selector.items() if key in FIELDS)


def selectors(value, *, buyer=False):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("INVALID_SETTLEMENT_METHOD_LIST")
    out = []
    for item in value:
        if (not isinstance(item, dict) or set(item) - (FIELDS | LIMITS if buyer else FIELDS)
                or item.get("method") not in METHODS):
            raise ValueError("INVALID_SETTLEMENT_METHOD")
        row = copy.deepcopy(item)
        for key in FIELDS | {"funding_issuer"}:
            if key in row and (not isinstance(row[key], str) or not 1 <= len(row[key]) <= 512):
                raise ValueError("INVALID_SETTLEMENT_METHOD")
        if row.get("mode") not in {None, "EARN", "DEBT", "PAY"}:
            raise ValueError("INVALID_POINTS_MODE")
        if row.get("method") != "a2n-points/1" and set(row) & {"mode", "funding_issuer", "max_cost"}:
            raise ValueError("POINTS_SETTINGS_REQUIRE_POINTS_METHOD")
        for key in LIMITS - {"funding_issuer"}:
            if key in row:
                value = row[key]
                if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 78:
                    value = int(value)
                minor(value)
                if value >= 2**256:
                    raise ValueError("SETTLEMENT_AMOUNT_TOO_LARGE")
                row[key] = value
        if row.get("method") != "evm-native/1" and row.get("fee_cap_minor", 0):
            raise ValueError("METHOD_BUYER_NETWORK_FEE_MUST_BE_ZERO")
        out.append(row)
    return out


class SettlementPolicy:
    """Receiver allowlist and payer ordered preferences remain private to the owner.

    None preserves existing manual configurations; [] explicitly disables a role.
    Automatic spending needs an explicit bounded rule, never the legacy default.
    """
    def __init__(self, store):
        self.store = store

    def get(self):
        return self.store.get("settlement_policy", "current") or {
            "revision": 0, "provider_methods": None, "buyer_preferences": None,
            "automatic": False}

    def public(self):
        row = copy.deepcopy(self.get())
        for rule in row["buyer_preferences"] or []:
            for key in LIMITS - {"funding_issuer"}:
                if key in rule:
                    rule[key] = str(rule[key])
        return row

    def configure(self, body):
        if not isinstance(body, dict) or set(body) != {
                "expected_revision", "provider_methods", "buyer_preferences", "automatic"}:
            raise ValueError("INVALID_SETTLEMENT_POLICY")
        if type(body["automatic"]) is not bool or type(body["expected_revision"]) is not int:
            raise ValueError("INVALID_SETTLEMENT_POLICY")
        provider = selectors(body["provider_methods"])
        buyer = selectors(body["buyer_preferences"], buyer=True)
        if body["automatic"] and (not buyer or any("max_amount_minor" not in row
                or row["method"] == "a2n-points/1" and "mode" not in row for row in buyer)):
            raise ValueError("AUTOMATIC_SETTLEMENT_REQUIRES_BOUNDED_PREFERENCES")
        with self.store.tx():
            previous = self.get()
            if previous["revision"] != body["expected_revision"]:
                raise ValueError("SETTLEMENT_POLICY_REVISION_CONFLICT")
            row = {"revision": previous["revision"] + 1, "provider_methods": provider,
                   "buyer_preferences": buyer, "automatic": body["automatic"]}
            self.store.put("settlement_policy", "current", row)
        return self.public()

    def allowed(self, descriptor, *, receiving=False):
        rules = self.get()["provider_methods" if receiving else "buyer_preferences"]
        if rules is None:
            return True
        # Modes and price limits are applied to actual offers, not protocol discovery.
        return any(matches(descriptor, {k: v for k, v in rule.items() if k != "mode"}) for rule in rules)

    def provider_option(self, option):
        rules = self.get()["provider_methods"]
        return rules is None or any(matches(option, rule) for rule in rules)

    def match(self, offer):
        policy = self.get()
        if offer.get("payment_state") == "NOT_REQUIRED":
            return {"state": "NOT_REQUIRED", "policy_revision": policy["revision"], "option_index": None}
        rules = policy["buyer_preferences"]
        if rules is None:
            rules = [{"method": method} for method in ("a2n-points/1", "x402/2", "evm-native/1")]
        candidates, excluded = [], []
        used = set()
        for rank, rule in enumerate(rules):
            for index, option in enumerate(offer.get("options", [])):
                if index in used or not matches(option, rule):
                    continue
                amount = option.get("amount_minor")
                minor(amount)
                if "max_amount_minor" in rule and amount > rule["max_amount_minor"]:
                    excluded.append({"option_index": index, "reason": "AMOUNT_EXCEEDS_PREFERENCE_LIMIT"})
                    continue
                if policy["automatic"] and "max_amount_minor" not in rule:
                    continue
                used.add(index)
                candidates.append({"option_index": index, "preference_index": rank,
                    "method": option["method"], "mode": option.get("mode"),
                    "fee_cap_minor": rule.get("fee_cap_minor", 0),
                    **{k: rule[k] for k in ("funding_issuer", "max_cost") if k in rule}})
        return {"state": "MATCHED" if candidates else "UNAVAILABLE",
            "automatic": policy["automatic"], "policy_revision": policy["revision"],
            "option_index": candidates[0]["option_index"] if candidates else None,
            "candidates": candidates, "excluded": excluded}
