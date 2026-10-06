"""Local opportunity and discovery policy, distinct from public obligations."""
from __future__ import annotations

import math
import time

from .coordination import CandidateKey, PolicyDecision
from .coordination_service import CandidatePolicy
from .pricing import is_free, price_book, quote_card
from .trade_facts import digest

DEFAULT_POLICY = {"mode": "SHADOW", "exploration_fraction": .2, "allow_unknown": True,
    "observe_groups": 3, "observe_mass": 2.0, "observe_below": .4,
    "limit_groups": 5, "limit_mass": 3.0, "limit_below": .35,
    "recover_above": .6, "recover_mass": 2.0, "unknown_below_mass": .25}
MODES = {"DISABLED", "SHADOW", "SUGGEST", "ENFORCE_LOCAL"}


def validate_policy(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULT_POLICY):
        raise ValueError("INVALID_POLICY_FIELDS")
    if values["mode"] not in MODES or not isinstance(values["allow_unknown"], bool):
        raise ValueError("INVALID_POLICY_MODE")
    for key, value in values.items():
        if key in {"mode", "allow_unknown"}:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("INVALID_POLICY_VALUE")
        if (key.endswith("groups") and (not isinstance(value, int) or not 1 <= value <= 1000)
                or key.endswith("mass") and not 0 < value <= 1000
                or key in {"exploration_fraction", "observe_below", "limit_below", "recover_above"} and value > 1):
            raise ValueError("INVALID_POLICY_VALUE")
    if (values["limit_groups"] < values["observe_groups"] or values["limit_mass"] < values["observe_mass"]
            or values["limit_below"] > values["observe_below"] or values["recover_above"] <= values["observe_below"]):
        raise ValueError("INCONSISTENT_POLICY_THRESHOLDS")
    return dict(values)


class PolicyBook:
    def __init__(self, store, *, now=None):
        self.store = store
        self.now = now or time.time

    def get(self, policy_id="local-business/1"):
        return self.store.get("local_policies", policy_id) or {"policy_id": policy_id, "revision": 0,
            "values": dict(DEFAULT_POLICY), "updated_at": None}

    def update(self, policy_id, values, *, expected_revision):
        if policy_id != "local-business/1":
            raise ValueError("UNKNOWN_POLICY")
        validate_policy(values)
        with self.store.tx():
            current = self.get(policy_id)
            if isinstance(expected_revision, bool) or expected_revision != current["revision"]:
                raise ValueError("REV_CONFLICT")
            row = {"policy_id": policy_id, "revision": current["revision"] + 1,
                   "values": dict(values), "updated_at": self.now()}
            self.store.put("local_policy_versions", f"{policy_id}:{row['revision']}", row)
            self.store.put("local_policies", policy_id, row)
            return row

    def assess(self, view):
        policy = self.get()
        cfg = policy["values"]
        key = digest(view["subject"])
        with self.store.tx():
            previous = self.store.get("local_opportunities", key) or {"state": "NORMAL", "seen_trades": []}
            # Revisions, repeated recomputations and copies cannot create new independent trades.
            trades = {r["trade_uid"] for dimension in view["dimensions"].values()
                      for group in dimension["contributions"] for r in group["records"]}
            new = sorted(trades - set(previous["seen_trades"]))
            state, reasons = previous["state"], []
            dimensions = [d for d in view["dimensions"].values() if d["effective_mass"] > 0]
            def bad(prefix):
                return any(d["counterparty_groups"] >= cfg[prefix + "_groups"]
                           and d["effective_mass"] >= cfg[prefix + "_mass"]
                           and d["theta"] < cfg[prefix + "_below"] for d in dimensions)
            if previous.get("manual_block"):
                state, reasons = "BLOCKED_LOCAL", ["OWNER_BLOCK"]
            elif cfg["mode"] == "DISABLED":
                state, reasons = "NORMAL", ["POLICY_DISABLED"]
            elif not dimensions or max(d["effective_mass"] for d in dimensions) < cfg["unknown_below_mass"]:
                state, reasons = "NORMAL", ["SUPPORT_DECAYED_TO_UNKNOWN"]
            elif all(d["effective_mass"] >= cfg["recover_mass"] and d["theta"] >= cfg["recover_above"] for d in dimensions):
                state, reasons = "NORMAL", ["SUPPORTED_RECOVERY"]
            elif state == "NORMAL" and bad("observe"):
                state, reasons = "OBSERVE", ["SUPPORTED_LOW_OPINIONS"]
            elif new and bad("limit") and state in {"OBSERVE", "LIMITED"}:
                state, reasons = ("LIMITED" if state == "OBSERVE" else "MANUAL_ONLY"), ["NEW_SUPPORTED_LOW_TRADES"]
            else:
                reasons = ["NO_NEW_QUALIFIED_TRADE" if not new else "SUPPORT_INSUFFICIENT_FOR_CHANGE"]
            row = {"subject": view["subject"], "state": state,
                "effective_state": state if (cfg["mode"] == "ENFORCE_LOCAL" and view["coverage"].get("known_sources_complete")) or previous.get("manual_block") else "NORMAL",
                "mode": cfg["mode"], "policy_revision": policy["revision"], "algorithm": view["algorithm"],
                "run_id": view.get("run_id"), "reasons": reasons, "seen_trades": sorted(set(previous["seen_trades"]) | trades),
                "new_trades": new, "manual_block": bool(previous.get("manual_block")), "at": self.now()}
            if not view["coverage"].get("known_sources_complete"):
                row["reasons"].append("SOURCE_COVERAGE_INCOMPLETE")
            if state != previous["state"]:
                self.store.put("local_opportunity_events", digest([key, row["at"], state]),
                               {**row, "previous_state": previous["state"]})
            self.store.put("local_opportunities", key, row)
            return row

    def opportunity(self, subject):
        return self.store.get("local_opportunities", digest(subject))

    def block(self, subject, *, blocked):
        from .experience import validate_subject
        validate_subject(subject)
        if not isinstance(blocked, bool):
            raise ValueError("blocked 必须为布尔值")
        with self.store.tx():
            row = self.opportunity(subject) or {"subject": subject, "seen_trades": []}
            row.update(manual_block=blocked, state="BLOCKED_LOCAL" if blocked else "NORMAL",
                       effective_state="BLOCKED_LOCAL" if blocked else "NORMAL", at=self.now())
            self.store.put("local_opportunities", digest(subject), row)
            return row


class BusinessCandidatePolicy:
    def __init__(self, spec, reputation, policies):
        self.spec, self.reputation, self.policies = spec, reputation, policies

    def evaluate(self, candidates, policy_version, budget_remaining):
        if self.spec.policy_id in {"", "local-count/1"}:
            return CandidatePolicy(self.spec).evaluate(candidates, policy_version, budget_remaining)
        if self.spec.policy_id != "local-business/1":
            return PolicyDecision("NEEDS_INPUT", ["UNKNOWN_POLICY"], policy_version=policy_version)
        supported = {"provider_did", "version", "currency", "budget_minor", "min_quality", "allow_unknown"}
        required = self.spec.required
        if set(required) - supported:
            return PolicyDecision("NEEDS_INPUT", ["UNSUPPORTED_LOCAL_CONDITION"], policy_version=policy_version)
        policy = self.policies.get()
        selected, unknown, ranked = [], False, []
        wanted = self.spec.preferences.get("min_candidates", 3)
        if isinstance(wanted, bool) or not isinstance(wanted, int) or not 1 <= wanted <= 256:
            raise ValueError("INVALID_MIN_CANDIDATES")
        threshold = required.get("min_quality", 0)
        budget = required.get("budget_minor")
        if (isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or not 0 <= threshold <= 1 or budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 0)
                or "allow_unknown" in required and not isinstance(required["allow_unknown"], bool)):
            raise ValueError("INVALID_BUSINESS_CONDITION")
        for candidate in candidates:
            if candidate["verification"] != "CARD_VERIFIED":
                continue
            key = candidate["key"]
            if required.get("provider_did") not in (None, key["provider_did"]):
                continue
            card = candidate["cards"][-1]["card"] if candidate["cards"] else {}
            if required.get("version") not in (None, card.get("version")):
                continue
            if budget is not None and not is_free(card, self.spec.skill):
                currency = required.get("currency")
                if not currency:
                    unknown = True
                    continue
                entries = (price_book(card).get(self.spec.skill) or {}).get(currency, [])
                if not entries or any(e["key"] != "call_count" for e in entries):
                    unknown = True
                    continue
                amount = quote_card(card, self.spec.skill, currency, {"call_count": 1})["amount_minor"]
                if amount > budget:
                    continue
            subject = {"kind": "service", **key}
            reputation = self.reputation.get(subject)
            opportunity = self.policies.opportunity(subject) or {}
            if opportunity.get("effective_state") in {"MANUAL_ONLY", "BLOCKED_LOCAL"}:
                continue
            quality = ((reputation or {}).get("dimensions") or {}).get("quality") or {}
            insufficient = quality.get("support", "UNKNOWN") != "SUPPORTED"
            if insufficient:
                if not required.get("allow_unknown", policy["values"]["allow_unknown"]):
                    unknown = True
                    continue
            elif quality["theta"] < threshold:
                continue
            ranked.append((insufficient, quality.get("theta", .5), digest(key), CandidateKey.from_dict(key)))
        ranked.sort(key=lambda x: (-x[1], x[2]))
        # Bounded exploration is a display choice; this decision never invokes a service.
        quota = min(wanted, math.ceil(wanted * policy["values"]["exploration_fraction"]))
        newcomers = [r for r in ranked if r[0]][:quota]
        ordered = newcomers + [r for r in ranked if r not in newcomers]
        selected = [r[3] for r in ordered]
        status = "SATISFIED" if len(selected) >= wanted else "NEEDS_INPUT" if unknown else "CONTINUE"
        return PolicyDecision(status, ["DISPLAY_ONLY", "NEWCOMER_EXPLORATION", "CONDITIONS_UNKNOWN"] if unknown else ["DISPLAY_ONLY", "HARD_CONDITIONS_CHECKED"],
                              selected, policy_version=f"local-business/1:{policy['revision']}")
