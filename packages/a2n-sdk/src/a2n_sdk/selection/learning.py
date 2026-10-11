"""Private, bounded preference learning from normalized, consented local facts."""
from __future__ import annotations
import math
from .contracts import DIMENSIONS, clone, digest, number


def history(observations, now):
    current = {}
    for row in observations:
        uid = row.get("trade_uid")
        if not isinstance(uid, str) or not uid or not isinstance(row.get("reference"), str):
            continue
        try:
            number(row["label_at"], maximum=now)
        except (KeyError, TypeError, ValueError):
            continue
        previous = current.get(uid)
        if previous is None or (row["label_at"], row["reference"]) > (previous["label_at"], previous["reference"]):
            current[uid] = row
    latest = {}
    for row in current.values():
        if (row.get("origin") != "PRODUCTION" or row.get("label_source") != "HUMAN"
                or not row.get("consent") or row.get("withdrawn") or row.get("known_self")
                or not row.get("verified_delivery")):
            continue
        try:
            number(row["label"], maximum=1)
            number(row["at"], maximum=now)
            number(row["label_at"], maximum=now)
            if row["at"] < now - 90 * 86400 or row["label_at"] < row["at"]:
                continue
            if not row.get("trade_uid") or not row.get("provider_did") or not row.get("reference"):
                continue
            if set(row["features"]) != set(DIMENSIONS):
                continue
            for value in row["features"].values():
                number(value, maximum=1)
        except (KeyError, ValueError, TypeError):
            continue
        old = latest.get(row["trade_uid"])
        if old is None or (row["label_at"], row["reference"]) > (old["label_at"], old["reference"]):
            latest[row["trade_uid"]] = clone(row)
    rows, counts = [], {}
    for row in sorted(latest.values(), key=lambda r: (r["label_at"], r["trade_uid"]), reverse=True):
        provider = row["provider_did"]
        if counts.get(provider, 0) >= 100:
            continue
        counts[provider] = counts.get(provider, 0) + 1
        rows.append(row)
        if len(rows) >= 1000:
            break
    return sorted(rows, key=lambda r: (r["label_at"], r["trade_uid"]))


def _mse(rows, weights):
    counts = {}
    for row in rows:
        counts[row["provider_did"]] = counts.get(row["provider_did"], 0) + 1
    mass = sum(1 / counts[r["provider_did"]] for r in rows)
    return sum((sum(weights[k] * r["features"][k] for k in DIMENSIONS) - r["label"]) ** 2
               / counts[r["provider_did"]] for r in rows) / mass


def _project(values, baseline):
    # Per-dimension changes are capped at five percentage points. Quality and
    # credit retain a floor respecting any explicitly lower owner preference.
    lower = {k: max(0, baseline[k] - .05, min(baseline[k], .1) if k in {"quality", "credit"} else 0)
             for k in DIMENSIONS}
    upper = {k: min(1, baseline[k] + .05) for k in DIMENSIONS}
    lo, hi = -2.0, 2.0
    for _ in range(60):
        shift = (lo + hi) / 2
        if sum(min(upper[k], max(lower[k], values[k] - shift)) for k in DIMENSIONS) > 1:
            lo = shift
        else:
            hi = shift
    return {k: min(upper[k], max(lower[k], values[k] - (lo + hi) / 2)) for k in DIMENSIONS}


def fit(observations, baseline, *, now):
    number(now)
    if set(baseline) != set(DIMENSIONS) or not math.isclose(sum(baseline.values()), 1, abs_tol=1e-9):
        raise ValueError("INVALID_SELECTION_WEIGHTS")
    for value in baseline.values():
        number(value, maximum=1)
    rows = history(observations, now)
    support = {"human_samples": len(rows), "providers": len({r["provider_did"] for r in rows}),
        "days": len({int(r["at"] // 86400) for r in rows}),
        "minimum_samples": 40, "minimum_providers": 3, "minimum_days": 3,
        "independent_people_verified": False}
    result = {"algorithm": "a2n-private-preference-learning/1", "support": support,
        "weights": clone(baseline), "data_digest": digest([[r["trade_uid"], r["reference"]] for r in rows]),
        "state": "INSUFFICIENT_DATA", "heldout": None,
        "notice": "仅学习本人授权的已交付任务评价；不修改公开信用、硬性要求、预算或付款规则。"}
    if len(rows) < 40 or support["providers"] < 3 or support["days"] < 3:
        return result
    if max(r["label"] for r in rows) - min(r["label"] for r in rows) < .25:
        return {**result, "state": "INSUFFICIENT_VARIATION"}
    cut = len(rows) - max(8, math.ceil(len(rows) * .2))
    train, test = rows[:cut], rows[cut:]
    if len({r["provider_did"] for r in train}) < 2 or len({r["provider_did"] for r in test}) < 2:
        return {**result, "state": "INSUFFICIENT_DIVERSITY"}
    weights = clone(baseline)
    counts = {}
    for row in train:
        counts[row["provider_did"]] = counts.get(row["provider_did"], 0) + 1
    mass = sum(1 / counts[r["provider_did"]] for r in train)
    for _ in range(300):
        gradient = {k: .1 * (weights[k] - baseline[k]) for k in DIMENSIONS}
        for row in train:
            error = sum(weights[k] * row["features"][k] for k in DIMENSIONS) - row["label"]
            for k in DIMENSIONS:
                gradient[k] += 2 * error * row["features"][k] / counts[row["provider_did"]] / mass
        weights = _project({k: weights[k] - .15 * gradient[k] for k in DIMENSIONS}, baseline)
    before, after = _mse(test, baseline), _mse(test, weights)
    ready = before - after >= max(.0001, before * .05)
    return {**result, "state": "READY" if ready else "NO_HELDOUT_IMPROVEMENT",
        "weights": weights if ready else clone(baseline),
        "heldout": {"training_samples": len(train), "samples": len(test),
                    "baseline_mse": before, "candidate_mse": after,
                    "chronological": True, "providers_balanced": True}}
