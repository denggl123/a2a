"""Pure, versioned personalized ranking. No storage, network or payment imports."""
from __future__ import annotations

import math
from .contracts import DIMENSIONS, VERSION, clone, digest, metric, number
from .metrics import normalize

DEFAULT_PROFILE = {
    "weights": dict(zip(DIMENSIONS, (.20, .25, .20, .10, .10, .05, .07, .03))),
    "required": {}, "preferred_tags": [], "preferred_styles": [],
    "budgets": {}, "exploration_fraction": .2, "max_per_provider": 2,
    "top_k": 5, "satisfactory_count": 3,
    "anchors": {"rtt": [100, 2000], "jitter": [20, 500],
                "first": [1000, 10000], "completion": [5000, 60000]},
}
REQUIRED_LISTS = {"skills", "input_modes", "output_modes", "methods", "point_modes",
                  "providers", "versions"}
REQUIRED_FIELDS = REQUIRED_LISTS | {"min_quality", "min_credit", "require_supported"}


def _strings(value):
    if (not isinstance(value, list) or len(value) > 64
            or any(not isinstance(x, str) or not x or len(x) > 256 for x in value)):
        raise ValueError("INVALID_SELECTION_LIST")
    return sorted(set(value))


def normalize_profile(overrides=None):
    if overrides is None:
        return clone(DEFAULT_PROFILE)
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_PROFILE):
        raise ValueError("INVALID_SELECTION_PROFILE_FIELDS")
    value = {**clone(DEFAULT_PROFILE), **clone(overrides)}
    weights = value["weights"]
    if not isinstance(weights, dict) or set(weights) != set(DIMENSIONS):
        raise ValueError("INVALID_SELECTION_WEIGHTS")
    for w in weights.values():
        number(w, maximum=1)
    if not math.isclose(math.fsum(weights.values()), 1, abs_tol=1e-9, rel_tol=0):
        raise ValueError("SELECTION_WEIGHTS_MUST_SUM_TO_ONE")
    required = value["required"]
    if not isinstance(required, dict) or set(required) - REQUIRED_FIELDS:
        raise ValueError("INVALID_SELECTION_REQUIRED")
    for name in REQUIRED_LISTS & set(required):
        required[name] = _strings(required[name])
    for name in {"min_quality", "min_credit"} & set(required):
        number(required[name], maximum=1)
    if "require_supported" in required and not isinstance(required["require_supported"], bool):
        raise ValueError("INVALID_SELECTION_SUPPORT_REQUIREMENT")
    for name in ("preferred_tags", "preferred_styles"):
        value[name] = _strings(value[name])
    budgets = value["budgets"]
    if not isinstance(budgets, dict) or len(budgets) > 32:
        raise ValueError("INVALID_SELECTION_BUDGETS")
    for currency, budget in budgets.items():
        if not isinstance(currency, str) or not 1 <= len(currency) <= 256 or not isinstance(budget, dict) or set(budget) != {"comfortable", "maximum"}:
            raise ValueError("INVALID_SELECTION_BUDGET")
        for field, amount in list(budget.items()):
            if isinstance(amount, str) and 1 <= len(amount) <= 78 and amount.isascii() and amount.isdigit():
                amount = budget[field] = int(amount)
            if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 2**256-1:
                raise ValueError("INVALID_SELECTION_BUDGET_AMOUNT")
        if budget["comfortable"] > budget["maximum"]:
            raise ValueError("INVALID_SELECTION_BUDGET_RANGE")
    for name, cap in (("top_k", 100), ("max_per_provider", 100), ("satisfactory_count", 100)):
        if isinstance(value[name], bool) or not isinstance(value[name], int) or not 1 <= value[name] <= cap:
            raise ValueError("INVALID_SELECTION_COUNT")
    number(value["exploration_fraction"], maximum=.5)
    anchors = value["anchors"]
    if not isinstance(anchors, dict) or set(anchors) != set(DEFAULT_PROFILE["anchors"]):
        raise ValueError("INVALID_SELECTION_ANCHORS")
    for pair in anchors.values():
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("INVALID_SELECTION_ANCHORS")
        normalize(pair[0], *pair)
    return value


def _hard(candidate, task, profile, signals):
    checks = []
    def add(name, state):
        checks.append({"condition": name, "state": state})
    if not candidate.get("verified"):
        add("CARD_VERIFICATION", "FAIL")
    if candidate.get("opportunity") in {"BLOCKED_LOCAL", "MANUAL_ONLY"}:
        add("LOCAL_POLICY", "FAIL")
    if candidate.get("available") is False:
        add("CURRENT_AVAILABILITY", "FAIL")
    requirements = dict(profile["required"])
    # The task and the user's independent constraints both apply.
    for name, field in (("skill", "skills"), ("input_mode", "input_modes"), ("output_mode", "output_modes"),
                        ("language", "languages")):
        if task.get(name):
            actual = candidate.get(field) or []
            add("task_"+name, "UNKNOWN" if not actual else "PASS" if task[name] in actual else "FAIL")
    fields = {"skills": "skills", "input_modes": "input_modes", "output_modes": "output_modes",
              "methods": "methods", "point_modes": "point_modes", "providers": "provider_did", "versions": "version"}
    for name, wanted in requirements.items():
        if name not in fields or not wanted:
            continue
        actual = candidate.get(fields[name])
        actual = [actual] if isinstance(actual, str) and actual else actual or []
        add(name, "UNKNOWN" if not actual else "PASS" if set(wanted) & set(actual) else "FAIL")
    for name, dimension in (("min_quality", "quality"), ("min_credit", "credit")):
        if name not in requirements:
            continue
        m = signals.get(dimension, metric())
        if m["value"] is None or m["status"] in {"UNKNOWN", "STALE", "CONFLICT"}:
            add(name, "UNKNOWN")
        elif requirements.get("require_supported") and m["status"] != "SUPPORTED":
            add(name, "UNKNOWN")
        else:
            add(name, "PASS" if m["value"] >= requirements[name] else "FAIL")
    if profile["budgets"]:
        applicable = [cost for cost in _cost_options(candidate, profile) if cost["currency"] in profile["budgets"]]
        if not applicable:
            add("budget", "UNKNOWN")
        else:
            states = ["PASS" if cost["amount_minor"] <= profile["budgets"][cost["currency"]]["maximum"]
                      and cost.get("complete") else "UNKNOWN" if not cost.get("complete") else "FAIL" for cost in applicable]
            add("budget", "PASS" if "PASS" in states else "UNKNOWN" if "UNKNOWN" in states else "FAIL")
    state = "FAIL" if any(c["state"] == "FAIL" for c in checks) else "UNKNOWN" if any(c["state"] == "UNKNOWN" for c in checks) else "PASS"
    return state, checks


def _fit(candidate, task, profile):
    skill = task.get("skill")
    capability = 1 if not skill else 1 if skill in candidate.get("skills", []) else .5 if not candidate.get("skills") else 0
    formats = []
    for name, field in (("input_mode", "input_modes"), ("output_mode", "output_modes")):
        wanted, actual = task.get(name), candidate.get(field, [])
        formats.append(1 if not wanted else 1 if wanted in actual else .5 if not actual else 0)
    tags = set(profile["preferred_tags"])
    match = 1 if not tags else len(tags & set(candidate.get("tags", []))) / len(tags)
    language = task.get("language")
    declared = candidate.get("languages", [])
    constraints = 1 if not language else 1 if language in declared else .5 if not declared else 0
    declared_score = .5*capability+.2*sum(formats)/2+.15*match+.15*constraints
    keywords = task.get("keywords", [])
    if keywords:
        description = candidate.get("description", "").lower()
        overlap = sum(word.lower() in description for word in keywords)/len(keywords)
        declared_score = .7*declared_score+.3*overlap
    return metric(declared_score,
                  status="SUPPORTED", reasons=["DECLARED_CAPABILITY_MATCH"], raw={"declared_only": True})


def _cost_options(candidate, profile):
    modes = profile["required"].get("point_modes", [])
    methods = profile["required"].get("methods", [])
    return [item for item in candidate.get("costs", []) if (not modes or item.get("mode") in modes)
            and (not methods or ("points" in methods if item.get("mode") else any(m != "points" for m in methods)))]


def _cost(candidate, profile):
    values, details = [], []
    for item in _cost_options(candidate, profile):
        budget = profile["budgets"].get(item["currency"])
        if not budget or not item.get("complete"):
            continue
        amount = item["amount_minor"]
        if amount > budget["maximum"]:
            utility = 0
        elif budget["comfortable"] == budget["maximum"]:
            utility = 1
        else:
            utility = normalize(amount, budget["comfortable"], budget["maximum"])
        values.append(utility)
        details.append({**item, "amount_minor_decimal": str(amount), "budget_utility": utility})
    return metric(max(values) if values else None, raw={"alternatives": details},
                  reasons=["PERSONAL_BUDGET_UTILITY"] if values else ["COST_OR_ACCEPTANCE_UNKNOWN"])


def rank(candidates, *, profile=None, task=None, metrics=None, now=0, seed=""):
    """No effects. The same canonical facts/profile/clock/seed produce the same result."""
    cfg, task, metrics = normalize_profile(profile), clone(task or {}), metrics or {}
    number(now)
    allowed_task = {"skill", "input_mode", "output_mode", "workload_bucket", "rubric", "language", "task_class", "keywords"}
    keywords = task.get("keywords", [])
    if (set(task) - allowed_task or any(not isinstance(x, str) or len(x) > 256 for k,x in task.items() if k!="keywords")
            or not isinstance(keywords,list) or len(keywords)>24
            or any(not isinstance(x,str) or not 1<=len(x)<=64 for x in keywords)):
        raise ValueError("INVALID_SELECTION_TASK")
    rows, keys = [], set()
    for c in candidates:
        key = c["key"]
        if key in keys:
            raise ValueError("DUPLICATE_SELECTION_CANDIDATE")
        keys.add(key)
        signals = {name: clone(metrics.get(key, {}).get(name, metric())) for name in DIMENSIONS}
        signals["fit"] = _fit(c, task, cfg)
        signals["cost"] = _cost(c, cfg)
        if cfg["preferred_styles"]:
            wanted = set(cfg["preferred_styles"])
            actual = set(c.get("styles", []))
            signals["personal"] = metric(len(wanted & actual)/len(wanted) if actual else None,
                                         raw={"explicit_preference": True})
        state, checks = _hard(c, task, cfg, signals)
        contributions = {}
        for name, m in signals.items():
            if m["value"] is not None:
                number(m["value"], maximum=1)
            value = m["value"] if m["value"] is not None and m["status"] not in {"STALE", "CONFLICT"} else .5
            contributions[name] = cfg["weights"][name] * value * 100
        score = math.fsum(contributions.values())
        rows.append({"key": key, "provider_did": c["provider_did"], "service_id": c["service_id"],
                     "version": c.get("version", ""), "state": state, "score": score,
                     "dimensions": signals, "contributions": contributions, "checks": checks,
                     "unknown_dimensions": [k for k,v in signals.items() if v["value"] is None or v["status"] in {"STALE", "CONFLICT"}],
                     "newcomer": signals["quality"]["status"] in {"UNKNOWN", "LIMITED"},
                     "slot_reason": "PERSONAL_FIT", "algorithm": VERSION})
    rows.sort(key=lambda r: (-r["score"], len(r["unknown_dimensions"]), r["key"]))
    passed = [r for r in rows if r["state"] == "PASS"]
    pending = [r for r in rows if r["state"] == "UNKNOWN"]
    excluded = [r for r in rows if r["state"] == "FAIL"]
    selected, deferred, per_provider = [], [], {}
    for r in passed:
        p = r["provider_did"]
        if len(selected) < cfg["top_k"] and per_provider.get(p, 0) < cfg["max_per_provider"]:
            selected.append(r); per_provider[p] = per_provider.get(p, 0)+1
        else:
            deferred.append(r)
    if len(selected) < min(cfg["top_k"], len(passed)):
        needed = min(cfg["top_k"], len(passed))-len(selected)
        selected += deferred[:needed]; deferred = deferred[needed:]
    quota = int(cfg["top_k"] * cfg["exploration_fraction"])
    existing_new = sum(r["newcomer"] for r in selected)
    newcomers = sorted((r for r in deferred if r["newcomer"]), key=lambda r: digest([seed, r["provider_did"], r["key"]]))
    for r in newcomers:
        if existing_new >= quota or not selected:
            break
        # Exchange a tail slot only if the provider quota remains satisfied.
        old = next((x for x in reversed(selected) if not x["newcomer"]), None)
        if old is None:
            break
        new_count = sum(x["provider_did"] == r["provider_did"] for x in selected) - (old["provider_did"] == r["provider_did"])
        if new_count >= cfg["max_per_provider"]:
            continue
        pos = selected.index(old); selected[pos] = r
        deferred.remove(r); deferred.append(old)
        r["slot_reason"] = "NEW_SUPPLY_EXPLORATION"; existing_new += 1
    deferred.sort(key=lambda r: (-r["score"], r["key"]))
    strong = [r for r in passed if r["dimensions"]["fit"]["value"] >= .75
              and all(r["dimensions"][d]["value"] is not None
                      and r["dimensions"][d]["status"] == "SUPPORTED"
                      and r["dimensions"][d]["value"] >= .6 for d in ("quality", "credit"))]
    satisfactory = len(passed) >= cfg["satisfactory_count"] and bool(strong)
    return {"algorithm": VERSION, "task": task, "items": selected+deferred+pending+excluded,
            "counts": {"acquired": len(rows), "pass": len(passed), "pending": len(pending), "excluded": len(excluded)},
            "decision": "SATISFIED" if satisfactory else "CONTINUE",
            "reasons": [] if satisfactory else ["MORE_CANDIDATES_OR_SUPPORTED_HISTORY_NEEDED"],
            "weights": cfg["weights"]}
