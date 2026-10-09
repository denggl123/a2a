"""Ports and bounded data helpers. Pure stdlib; no trade/feedback/network imports."""
from __future__ import annotations
import hashlib
import json
import math
import re
from typing import Protocol

REVIEW_VERSION = "a2n-task-review/1"
CALIBRATION_VERSION = "a2n-local-calibration/1"

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value):
        raise ValueError("INVALID_TASK_QUALITY_ID")
    return value

def number(value, *, maximum=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("INVALID_TASK_QUALITY_NUMBER")
    return value

def context(value):
    if not isinstance(value, dict) or set(value) != {"rubric", "task_class", "skill", "workload_bucket"}:
        raise ValueError("INVALID_TASK_QUALITY_CONTEXT")
    ref = value["rubric"]
    if not isinstance(ref, str) or ref.count("@") != 1:
        raise ValueError("INVALID_TASK_RUBRIC_REFERENCE")
    for part in ref.split("@"): identifier(part)
    for k in ("task_class", "skill", "workload_bucket"): identifier(value[k])
    return clone(value)

class StorePort(Protocol):
    def get(self, namespace, key, default=None): ...
    def put(self, namespace, key, value): ...
    def items(self, namespace): ...
    def tx(self): ...

class ArtifactPort(Protocol):
    """Local buyer-owned artifacts only; never executes or fetches an Agent."""
    def describe(self, scope, task_id) -> dict: ...
    def delivered(self, scope, task_id) -> dict | None: ...

class CalibrationDataPort(Protocol):
    def observations(self, task_context) -> dict: ...

class CalibrationModelPort(Protocol):
    def active(self, task_context, now) -> dict: ...
