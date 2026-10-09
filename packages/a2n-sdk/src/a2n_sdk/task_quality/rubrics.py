"""Versioned task rubrics and deterministic checks. Never executes supplied code."""
from __future__ import annotations
import json
import math
from .contracts import clone, digest, identifier, number

BUILTINS = (
    {"id": "structured-output", "version": "1", "name": "结构化结果", "task_class": "structured-output",
     "dimensions": {"accuracy": {"weight": .35, "sources": {"objective": .8, "human": .2}},
                    "completeness": {"weight": .25, "sources": {"objective": .8, "human": .2}},
                    "format": {"weight": .2, "sources": {"objective": 1.0}},
                    "usefulness": {"weight": .2, "sources": {"human": 1.0}}}, "calibration_target": "usefulness"},
    {"id": "text-output", "version": "1", "name": "文本提取与表达", "task_class": "text-output",
     "dimensions": {"accuracy": {"weight": .3, "sources": {"objective": .5, "human": .5}},
                    "completeness": {"weight": .25, "sources": {"objective": .5, "human": .5}},
                    "expression": {"weight": .2, "sources": {"human": 1.0}},
                    "usefulness": {"weight": .25, "sources": {"human": 1.0}}}, "calibration_target": "usefulness"},
    {"id": "code-output", "version": "1", "name": "代码交付", "task_class": "code-output",
     "dimensions": {"requirements": {"weight": .35, "sources": {"objective": .5, "human": .5}},
                    "format": {"weight": .15, "sources": {"objective": 1.0}},
                    "maintainability": {"weight": .25, "sources": {"human": 1.0}},
                    "usefulness": {"weight": .25, "sources": {"human": 1.0}}}, "calibration_target": "usefulness"},
)

BUILTINS += tuple({'id':kind+'-output','version':'1','name':name,'task_class':kind+'-output',
    'dimensions':{'requirements':{'weight':.4,'sources':{'objective':.5,'human':.5}},
                  'format':{'weight':.25,'sources':{'objective':1.0}},
                  'usefulness':{'weight':.35,'sources':{'human':1.0}}},'calibration_target':'usefulness'}
    for kind,name in (('document','文档成果'),('image','图像成果'),('audio','音频成果'),('video','视频成果')))

def validate_rubric(value):
    if not isinstance(value, dict) or set(value) != {"id", "version", "name", "task_class", "dimensions", "calibration_target"}:
        raise ValueError("INVALID_TASK_RUBRIC")
    for k in ("id", "version", "task_class"): identifier(value[k])
    if not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 120:
        raise ValueError("INVALID_TASK_RUBRIC_NAME")
    dims = value["dimensions"]
    if not isinstance(dims, dict) or not 1 <= len(dims) <= 16:
        raise ValueError("INVALID_TASK_RUBRIC_DIMENSIONS")
    for key, item in dims.items():
        identifier(key)
        if not isinstance(item, dict) or set(item) != {"weight", "sources"}:
            raise ValueError("INVALID_TASK_RUBRIC_DIMENSION")
        number(item["weight"])
        sources = item["sources"]
        if not isinstance(sources, dict) or not sources or set(sources) - {"objective", "human"}:
            raise ValueError("INVALID_TASK_RUBRIC_SOURCES")
        for weight in sources.values(): number(weight)
        if not math.isclose(math.fsum(sources.values()), 1, abs_tol=1e-9):
            raise ValueError("TASK_RUBRIC_SOURCE_WEIGHTS_MUST_SUM_TO_ONE")
    if not math.isclose(math.fsum(v["weight"] for v in dims.values()), 1, abs_tol=1e-9):
        raise ValueError("TASK_RUBRIC_WEIGHTS_MUST_SUM_TO_ONE")
    target = value["calibration_target"]
    if target not in dims or dims[target]["sources"] != {"human": 1.0}:
        raise ValueError("CALIBRATION_TARGET_MUST_BE_INDEPENDENT_HUMAN_DIMENSION")
    return clone(value)

def reference(rubric): return rubric["id"] + "@" + rubric["version"]

OPS = {"exists", "equals", "type", "contains", "range", "length", "reviewer"}
TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}

def validate_checks(checks, rubric):
    if not isinstance(checks, dict) or set(checks) - set(rubric["dimensions"]):
        raise ValueError("INVALID_TASK_CHECK_DIMENSIONS")
    total = 0
    for dimension, rules in checks.items():
        if rubric["dimensions"][dimension]["sources"].get("objective", 0) <= 0 or not isinstance(rules, list) or not rules:
            raise ValueError("INVALID_TASK_CHECK_SOURCE")
        total += len(rules)
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"path", "op", "expected", "minimum", "maximum"} or rule.get("op") not in OPS:
                raise ValueError("INVALID_TASK_CHECK")
            path = rule.get("path")
            if not isinstance(path, list) or len(path) > 16 or any(type(p) not in (str, int) or isinstance(p, int) and p < 0
                  or isinstance(p, str) and len(p) > 128 for p in path):
                raise ValueError("INVALID_TASK_CHECK_PATH")
            op = rule["op"]
            required = {"path", "op"} | ({"minimum", "maximum"} if op in {"range", "length"} else
                                         {"expected"} if op in {"equals", "contains", "type", "reviewer"} else set())
            if set(rule) != required: raise ValueError("INVALID_TASK_CHECK_FIELDS")
            if op=="reviewer":
                spec=rule["expected"]
                if not isinstance(spec,dict) or spec.get("kind") not in {"python-function","semantic","media-constraints"} or not isinstance(spec.get("reviewer_digest"),str) or len(spec["reviewer_digest"])!=64:
                    raise ValueError("INVALID_REVIEWER_CHECK")
                if spec["kind"]=="python-function":
                    if set(spec)!={"kind","reviewer_digest","function","cases"} or not isinstance(spec["function"],str):raise ValueError("INVALID_REVIEWER_CHECK")
                    identifier(spec["function"])
                    if not isinstance(spec["cases"],list) or not 1<=len(spec["cases"])<=32 or any(not isinstance(c,dict) or set(c)!={"args","expected"} or not isinstance(c["args"],list) or len(c["args"])>16 for c in spec["cases"]):raise ValueError("INVALID_REVIEWER_CASES")
                elif spec['kind']=='media-constraints':
                    if set(spec)!={'kind','reviewer_digest','constraints'} or not isinstance(spec['constraints'],list) or any(not isinstance(r,dict) or r.get('op')=='reviewer' for r in spec['constraints']):raise ValueError('INVALID_MEDIA_CONSTRAINTS')
                    validate_checks({'format':spec['constraints']},BUILTINS[0])
                elif set(spec)!={"kind","reviewer_digest","requirements"} or not isinstance(spec["requirements"],str) or not 1<=len(spec["requirements"])<=4096:raise ValueError("INVALID_SEMANTIC_REQUIREMENTS")
            if op == "type" and (not isinstance(rule["expected"],str) or rule["expected"] not in TYPES): raise ValueError("INVALID_TASK_CHECK_TYPE")
            if op == "contains" and (not isinstance(rule["expected"], str) or not 1 <= len(rule["expected"]) <= 4096):
                raise ValueError("INVALID_TASK_CHECK_CONTAINS")
            if op in {"range", "length"}:
                number(rule["minimum"], maximum=10**18); number(rule["maximum"], maximum=10**18)
                if rule["minimum"] > rule["maximum"]: raise ValueError("INVALID_TASK_CHECK_RANGE")
    if total > 64 or len(json.dumps(checks, ensure_ascii=False, allow_nan=False).encode()) > 65536:
        raise ValueError("TASK_CHECK_BUDGET_EXCEEDED")
    return clone(checks)

MISSING = object()

def _path(value, path):
    for part in path:
        if isinstance(value, dict) and isinstance(part, str): value = value.get(part, MISSING)
        elif isinstance(value, list) and type(part) is int and part < len(value): value = value[part]
        else: return MISSING
    return value

def _type(value, name):
    return {"object": type(value) is dict, "array": type(value) is list, "string": type(value) is str,
            "number": type(value) in (int, float), "integer": type(value) is int,
            "boolean": type(value) is bool, "null": value is None}[name]

def evaluate_checks(result, checks, reviewer=None, artifact=None):
    # Inputs stay in memory. Only score and rule fingerprint enter derived evidence.
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 1048576:
        return {}, ["LOCAL_REVIEW_ARTIFACT_TOO_LARGE"]
    output = {}; reasons=[]
    for dimension, rules in checks.items():
        observations = []
        for rule in rules:
            value, op = _path(result, rule["path"]), rule["op"]
            if op=="reviewer":
                if reviewer and value is not MISSING:
                    evaluation=reviewer.evaluate(value,rule['expected'],artifact=artifact) if rule['expected']['kind']=='media-constraints' else reviewer.evaluate(value,rule['expected'])
                else:evaluation={'score':None,'state':'INDEPENDENT_REVIEW_UNAVAILABLE'}
                score=evaluation.get("score")
                if score is not None:number(score)
                observations.append({"rule_digest":digest(rule),"passed":score==1 if score is not None else None,"score":score,"evaluator":evaluation})
                if score is None:reasons.append(evaluation["state"])
                continue
            passed = value is not MISSING
            if passed and op == "equals": passed = digest(value) == digest(rule["expected"])
            elif passed and op == "type": passed = _type(value, rule["expected"])
            elif passed and op == "contains": passed = isinstance(value, str) and rule["expected"] in value
            elif passed and op == "range": passed = type(value) in (int, float) and math.isfinite(value) and rule["minimum"] <= value <= rule["maximum"]
            elif passed and op == "length": passed = isinstance(value, (dict, list, str)) and rule["minimum"] <= len(value) <= rule["maximum"]
            observations.append({"rule_digest": digest(rule), "passed": bool(passed)})
        values=[r.get("score",float(r["passed"])) if r["passed"] is not None else None for r in observations]
        output[dimension] = {"value": sum(values)/len(values) if all(x is not None for x in values) else None, "checks": observations}
    return output, reasons

def combine(rubric, objective, human, *, objective_map=None):
    dimensions, total, known, objective_total, objective_known, possible_objective = {}, 0.0, 0.0, 0.0, 0.0, 0.0
    for name, spec in rubric["dimensions"].items():
        values, observed = {}, 0.0
        for source, fraction in spec["sources"].items():
            raw = objective.get(name, {}).get("value") if source == "objective" else ((human[name]-1)/4 if name in human else None)
            if raw is not None: number(raw)
            values[source] = raw
            if raw is not None: observed += fraction
        dimension = sum(f * (values[s] if values[s] is not None else .5) for s, f in spec["sources"].items())
        total += spec["weight"] * dimension
        known += spec["weight"] * observed
        fraction = spec["sources"].get("objective", 0)
        possible_objective += spec["weight"] if fraction else 0
        if fraction and values.get("objective") is not None:
            objective_total += spec["weight"] * values["objective"]
            objective_known += spec["weight"]
        dimensions[name] = {"value": dimension if observed else None, "coverage": observed, "sources": values, "weight": spec["weight"]}
    prediction = objective_total/objective_known if objective_known else None
    calibrated = objective_map(prediction) if objective_map and prediction is not None else None
    if calibrated is not None:
        # Change only the empirical objective contribution; preserve raw checks and subjective scores.
        share = sum(spec["weight"]*spec["sources"].get("objective", 0) for name,spec in rubric["dimensions"].items()
                    if objective.get(name, {}).get("value") is not None)
        baseline = sum(spec["weight"]*spec["sources"].get("objective", 0)*objective[name]["value"]
                       for name,spec in rubric["dimensions"].items() if objective.get(name, {}).get("value") is not None)
        total += calibrated*share-baseline
    return {"value": max(0, min(1, total)) if known else None, "coverage": known, "dimensions": dimensions,
            "objective_prediction": prediction, "objective_coverage": objective_known/possible_objective if possible_objective else 0,
            "calibrated_objective": calibrated}
