"""Deterministic measurement math. Only normalized facts, no ledger/network I/O."""
from __future__ import annotations

import math
from .contracts import metric, number


def clip(value):
    return min(1.0, max(0.0, value))


def normalize(value, good, bad, *, lower=True):
    for item in (value, good, bad):
        number(item)
    if lower and bad <= good or not lower and good <= bad:
        raise ValueError("INVALID_SELECTION_ANCHORS")
    return clip((bad - value) / (bad - good) if lower else (value - bad) / (good - bad))


def decay(at, now, half_life_days=90):
    number(at); number(now); number(half_life_days, minimum=0.000001)
    return 2 ** (-max(0, now - at) / (half_life_days * 86400))


def aggregate(records, *, now, prior=2, cap=1, half_life_days=90,
              public=True, negative=False):
    """Records already qualified by an adapter; latest revision per trade/author.

    Each record: trade_uid, author_did, at, value, revision, ref. Unknowns excluded.
    public=False is a personal stream; cap=None retains real repeated experience.
    """
    latest = {}
    for row in records:
        if row.get("value") is None or not row.get("trade_uid") or not row.get("author_did"):
            continue
        number(row["value"], maximum=1)
        at = row.get("at")
        number(at)
        if at > now + 30:
            continue
        key = (row["trade_uid"], row["author_did"])
        order = (row.get("revision", 1), row.get("ref", ""))
        if key not in latest or order > (latest[key].get("revision", 1), latest[key].get("ref", "")):
            latest[key] = row
    groups = {}
    for key, row in sorted(latest.items()):
        weight = decay(row["at"], now, half_life_days)
        if weight:
            groups.setdefault(row["author_did"], []).append((row, weight))
    mass = weighted = squares = 0.0
    refs = []
    for _, items in sorted(groups.items()):
        total = math.fsum(w for _, w in items)
        w = min(total, cap) if cap is not None else total
        mean = math.fsum(row["value"] * v for row, v in items) / total
        mass += w; weighted += w * mean; squares += w * w
        refs.extend(row.get("ref", "") for row, _ in items if row.get("ref"))
    support = {"effective_mass": mass, "counterparty_groups": len(groups),
               "samples": len(latest), "n_eff": mass * mass / squares if squares else 0,
               "strength": mass / (prior + mass)}
    status = ("UNKNOWN" if not mass else "SUPPORTED" if
              (mass >= 2 and len(groups) >= 3 if public else mass >= 5) else "LIMITED")
    value = weighted / (prior + mass) if negative else (prior * .5 + weighted) / (prior + mass)
    return metric(value if mass else None, status=status, support=support,
                  raw={"mean": weighted / mass if mass else None}, refs=sorted(set(refs)))


def personalized(local, external, *, now):
    own = aggregate(sorted(local, key=lambda r: (r["at"], r["trade_uid"], r.get("ref", "")))[-100:], now=now, prior=5, cap=None, public=False)
    others = aggregate(external, now=now)
    if own["value"] is None:
        return others
    fraction = .7 * own["support"]["strength"]
    value = fraction * own["raw"]["mean"] + (1-fraction) * (
        others["value"] if others["value"] is not None else .5)
    return metric(value, status="SUPPORTED" if own["status"] == "SUPPORTED"
                  or others["status"] == "SUPPORTED" else "LIMITED",
                  support={"local": own["support"], "external": others["support"], "personal_weight": fraction},
                  raw={"local_mean": own["raw"]["mean"], "external_value": others["value"]},
                  refs=sorted(set(own["refs"] + others["refs"])))


def credit(base, breaches, handling, *, now, public_breaches=(), public_handling=(), complete=False):
    """Complaints/closed agreements are deliberately absent from numeric inputs."""
    # A proved breach and a handling opinion about that same trade cannot count twice.
    local_breach_ids = {r["trade_uid"] for r in breaches if r.get("value", 0) > 0}
    public_breach_ids = {r["trade_uid"] for r in public_breaches if r.get("value", 0) > 0}
    handling = [r for r in handling if r["trade_uid"] not in local_breach_ids]
    public_handling = [r for r in public_handling if r["trade_uid"] not in public_breach_ids]
    pressures, components = {}, {}
    for label, local, external in (("breach", breaches, public_breaches),
                                   ("handling", handling, public_handling)):
        own = aggregate(local, now=now, negative=True, public=False)
        other = aggregate(external, now=now, negative=True)
        qualified = complete and other["status"] == "SUPPORTED"
        pressures[label] = max(own["value"] or 0, (other["value"] or 0) if qualified else 0)
        components[label] = {"local": own, "external": other, "external_applied": qualified}
    known = base["value"] is not None or any(pressures.values())
    value = clip((base["value"] if base["value"] is not None else .5)
                 - .60 * pressures["breach"] - .25 * pressures["handling"])
    status = base["status"] if base["value"] is not None else "LIMITED" if known else "UNKNOWN"
    refs = set(base["refs"])
    for item in components.values():
        refs.update(item["local"]["refs"])
        if item["external_applied"]:
            refs.update(item["external"]["refs"])
    summaries = {label: {"external_applied": item["external_applied"],
        **{kind: {k: item[kind][k] for k in ("status", "support", "raw")} for kind in ("local", "external")}}
        for label, item in components.items()}
    return metric(value if known else None, status=status,
                  support={"base": base["support"], "pressures": summaries},
                  raw={"base": base["value"], **pressures}, refs=sorted(refs))


def quantile(weighted, q):
    total = math.fsum(w for _, w in weighted)
    if not total:
        return None
    so_far = 0
    for value, w in sorted(weighted):
        so_far += w
        if so_far >= q * total:
            return value
    return max(value for value, _ in weighted)


def network(observations, *, now, anchors):
    buckets = {}
    for row in observations:
        at = row.get("checked_at")
        if (type(row.get("reachable")) is not bool or isinstance(at, bool)
                or not isinstance(at, (int, float)) or not math.isfinite(at)
                or not now - 1800 <= at <= now):
            continue
        key = int(row["checked_at"] // 30)
        buckets.setdefault(key, []).append(row)
    if not buckets:
        return metric()
    mass = successes = 0
    rtts = []
    latest = max(row["checked_at"] for items in buckets.values() for row in items)
    for bucket, items in sorted(buckets.items()):
        w = decay(bucket * 30, now, 5 / 1440)
        mass += w; successes += w * sum(r["reachable"] is True for r in items) / len(items)
        values = sorted(r["rtt_ms"] for r in items if r["reachable"] is True
                        and isinstance(r.get("rtt_ms"), (int, float)) and not isinstance(r["rtt_ms"], bool)
                        and math.isfinite(r["rtt_ms"]) and r["rtt_ms"] >= 0)
        if values:
            rtts.append((values[len(values)//2], w))
    availability = (1 + successes) / (2 + mass)
    p50, p90 = quantile(rtts, .5), quantile(rtts, .9)
    total = math.fsum(w for _, w in rtts)
    squares = math.fsum(w*w for _, w in rtts)
    neff = total * total / squares if squares else 0
    strength = neff / (5 + neff)
    latency = .5 if p90 is None else .5 + strength * (normalize(p90, *anchors["rtt"]) - .5)
    jitter = .5 if p90 is None else .5 + strength * (normalize(max(0, p90-p50), *anchors["jitter"]) - .5)
    return metric(.7*availability+.2*latency+.1*jitter,
                  status="STALE" if latest < now-120 else "SUPPORTED" if len(buckets) >= 20 else "LIMITED",
                  support={"buckets": len(buckets), "rtt_samples": len(rtts), "n_eff": neff},
                  raw={"availability": availability, "rtt_p50_ms": p50, "rtt_p90_ms": p90,
                       "latest_at": latest, "probe_kind": max(
                           (row for items in buckets.values() for row in items),
                           key=lambda r: r["checked_at"]).get("kind", "unknown")})


def execution(observations, *, now, anchors):
    rows = {r["trade_uid"]: r for r in sorted(observations, key=lambda r: r.get("at", 0))
            if r.get("trade_uid") and now-30*86400 <= r.get("at", 0) <= now}
    delivered = failed = mass_t = weighted_t = 0.0
    counts, times, first_times = {}, [], []
    for row in rows.values():
        state = row.get("execution", "UNKNOWN")
        counts[state] = counts.get(state, 0) + 1
        w = decay(row["at"], now, 7)
        if state == "DELIVERED":
            delivered += w
        elif state == "FAILED" and row.get("failure_origin") == "agent":
            failed += w
        elapsed = row.get("elapsed_ms")
        if elapsed is None or row.get("failure_origin") in {"network", "payment", "input", "local"}:
            continue
        number(elapsed)
        if state == "DELIVERED" and row.get("complete_timing") is True:
            complete = normalize(elapsed, *anchors["completion"])
            first = row.get("first_useful_ms")
            first_utility = normalize(first, *anchors["first"]) if first is not None else .5
            times.append((elapsed, w))
            if first is not None:
                first_times.append((first, w))
        elif state == "UNKNOWN" and elapsed >= anchors["completion"][1]:
            complete = 0
            first_utility = 0 if row.get("first_useful_ms") is None and row.get("no_useful_result") else .5
        else:
            continue
        mass_t += w
        weighted_t += w * (.35 * first_utility + .65 * complete)
    r_mass = delivered + failed
    reliability = metric((1+delivered)/(2+r_mass) if r_mass else None,
                         status="SUPPORTED" if r_mass >= 5 and not counts.get("UNKNOWN") else "LIMITED" if r_mass else "UNKNOWN",
                         support={"determined_mass": r_mass, "samples": len(rows)}, raw={"outcomes": counts})
    timing = metric((2.5+weighted_t)/(5+mass_t) if mass_t else None,
                    status="SUPPORTED" if mass_t >= 5 else "LIMITED" if mass_t else "UNKNOWN",
                    support={"mass": mass_t, "completed_samples": len(times)},
                    raw={"completion_p50_ms": quantile(times, .5), "completion_p90_ms": quantile(times, .9),
                         "first_useful_p50_ms": quantile(first_times, .5), "tail_supported": len(times) >= 20,
                         "outcomes": counts})
    return {"reliability": reliability, "time": timing}
