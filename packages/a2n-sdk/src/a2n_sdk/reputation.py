"""Deterministic local reputation. No networking, execution or global score."""
from __future__ import annotations

import math
import time

from .experience import validate_subject
from .feedback import DIMENSIONS
from .feedback_schema import DIMENSIONS_V2, NEW_DIMENSIONS
from .trade_facts import digest

ALGORITHM = "a2n-reputation/1"
DEFAULTS = {"half_life_days": 90.0, "counterparty_cap": 1.0,
            "neutral_mass": 2.0, "other_version_weight": .5}


def configuration(overrides=None):
    value = {**DEFAULTS, **(overrides or {})}
    if set(value) != set(DEFAULTS):
        raise ValueError("UNKNOWN_REPUTATION_PARAMETER")
    for key, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError("INVALID_REPUTATION_PARAMETER")
        if number <= 0 or key == "other_version_weight" and number > 1:
            raise ValueError("INVALID_REPUTATION_PARAMETER")
    return value


def calculate(subject, views, *, now, current_version="", config=None, coverage=None):
    """Input must be ExperienceBook validated latest views, never remote summaries."""
    validate_subject(subject)
    cfg = configuration(config)
    if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
        raise ValueError("INVALID_CLOCK")
    direction = "seller_to_buyer" if subject["kind"] == "buyer" else "buyer_to_seller"
    target = subject.get("buyer_did") or subject["provider_did"]
    exclusions, candidates, opinions = [], [], {}
    for view in views:
        record = view["record"]
        reason = ""
        if not view.get("eligible"):
            reason = "CONFLICT" if view.get("conflict") else "INCOMPLETE_CHAIN" if not view.get("chain_complete") else "EXPIRED" if not view.get("fresh") else "WITHDRAWN"
        elif record["direction"] != direction:
            reason = "OTHER_DIRECTION"
        elif record["author_did"] == target:
            reason = "SELF_SOURCE"
        elif record.get("trade_at", now) > now + 30:
            reason = "FUTURE_TRADE"
        if reason:
            exclusions.append({"feedback_id": record["feedback_id"], "reason": reason})
            continue
        key = (record["trade_uid"], record["author_did"], direction)
        old = opinions.get(key)
        order = (record["published_at"], record["publication_revision"], record["feedback_id"])
        if old is None or order > (old["published_at"], old["publication_revision"], old["feedback_id"]):
            if old:
                exclusions.append({"feedback_id": old["feedback_id"], "reason": "DUPLICATE_TRADE_OPINION"})
            opinions[key] = record
        else:
            exclusions.append({"feedback_id": record["feedback_id"], "reason": "DUPLICATE_TRADE_OPINION"})
    for record in sorted(opinions.values(), key=lambda r: (r["author_did"], r["trade_uid"], r["feedback_id"])):
        age = max(0, now - record["trade_at"])
        decay = 2 ** (-age / (cfg["half_life_days"] * 86400))
        version = 1.0 if not current_version or record["service_version"] == current_version else cfg["other_version_weight"]
        candidates.append({"record": record, "decay": decay, "version_weight": version,
                           "q": decay * version})
    dimensions = {}
    extended = any(set(r["record"]["dimensions"]) & NEW_DIMENSIONS for r in candidates)
    for dimension in (DIMENSIONS_V2 if extended else DIMENSIONS)[direction]:
        groups = {}
        for item in candidates:
            record = item["record"]
            if dimension not in record["dimensions"] or item["q"] <= 0:
                continue
            # ExperienceBook already qualifies quality against an actual delivery anchor.
            groups.setdefault(record["author_did"], []).append(item)
        contributions, mass, weighted, squares = [], 0.0, 0.0, 0.0
        for author, items in sorted(groups.items()):
            total = math.fsum(item["q"] for item in items)
            if total <= 0:
                continue
            weight = min(total, cfg["counterparty_cap"])
            mean = math.fsum(item["q"] * ((item["record"]["dimensions"][dimension] - 1) / 4)
                             for item in items) / total
            mass += weight
            weighted += weight * mean
            squares += weight ** 2
            contributions.append({"counterparty_did": author, "weight": weight, "mean": mean,
                "opinion_count": len(items), "records": [{"feedback_id": item["record"]["feedback_id"],
                    "trade_uid": item["record"]["trade_uid"], "qualification": 1.0,
                    "decay": item["decay"], "version_weight": item["version_weight"],
                    "weight": item["q"] * min(1, cfg["counterparty_cap"] / total)} for item in items]})
        theta = (cfg["neutral_mass"] * .5 + weighted) / (cfg["neutral_mass"] + mass)
        dimensions[dimension] = {"theta": theta, "effective_mass": mass,
            "counterparty_groups": len(contributions), "n_eff": mass ** 2 / squares if squares else 0.0,
            "confidence": mass / (cfg["neutral_mass"] + mass),
            "support": "UNKNOWN" if mass == 0 else "LIMITED" if mass < 2 or len(contributions) < 3 else "SUPPORTED",
            "contributions": contributions}
    normalized = sorted(views, key=lambda v: (v["record"]["author_did"], v["record"]["feedback_id"]))
    fact_hashes = sorted(digest(item["record"]) for item in candidates if item["q"] > 0)
    return {"algorithm": "a2n-reputation/2" if extended else ALGORITHM, "mode": "SHADOW", "subject": subject,
        "current_version": current_version, "config": cfg, "calculated_at": now,
        "input_digest": digest(normalized), "qualified_fact_hashes": fact_hashes,
        "included_count": len(candidates), "excluded": sorted(exclusions, key=lambda r: (r["feedback_id"], r["reason"])), "dimensions": dimensions,
        "coverage": coverage or {"known_sources_complete": False, "global_completeness": "UNKNOWN"},
        "notice": "本机按已取得的签名意见复算；节点身份不等于独立自然人。影子模式不改变成交。"}


class ReputationBook:
    def __init__(self, store, experience, *, now=None):
        self.store, self.experience = store, experience
        self.now = now or time.time

    def rebuild(self, subject, *, current_version="", config=None, command_id):
        validate_subject(subject)
        cfg = configuration(config)
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("必须提供 Idempotency-Key")
        fingerprint = digest([subject, current_version, cfg])
        with self.store.tx():
            prior = self.store.get("reputation_commands", command_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.store.get("reputation_runs", prior["run_id"])
            queries = [q for q in self.store.items("experience_queries").values() if q["subject"] == subject]
            query = max(queries, key=lambda q: q["created_at"], default=None)
            coverage = {k: query[k] for k in ("query_id", "known_sources_complete", "global_completeness", "completed_sources", "missing_sources")} if query else None
            row = calculate(subject, self.experience.latest(subject), now=self.now(),
                            current_version=current_version, config=cfg, coverage=coverage)
            row["run_id"] = "rr_" + digest([command_id, fingerprint])
            previous = self.get(subject)
            row["previous_run_id"] = (previous or {}).get("run_id")
            row["new_qualified_fact_hashes"] = sorted(set(row["qualified_fact_hashes"]) - set((previous or {}).get("qualified_fact_hashes", [])))
            self.store.put("reputation_runs", row["run_id"], row)
            self.store.put("reputation_views", digest(subject), row)
            self.store.put("reputation_commands", command_id, {"fingerprint": fingerprint, "run_id": row["run_id"]})
            return row

    def get(self, subject):
        validate_subject(subject)
        return self.store.get("reputation_views", digest(subject))

    def list(self):
        return list(self.store.items("reputation_views").values())
