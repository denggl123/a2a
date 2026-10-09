"""Small data/port contracts. No business implementation or transport imports."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Protocol

VERSION = "a2n-selection/1"
DIMENSIONS = ("fit", "quality", "credit", "reliability", "time", "network", "cost", "personal")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def number(value, *, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value < minimum
            or maximum is not None and value > maximum):
        raise ValueError("INVALID_SELECTION_NUMBER")
    return value


def metric(value=None, *, status=None, support=None, reasons=(), raw=None, refs=()):
    if value is not None:
        number(value, maximum=1)
    refs = sorted(set(refs))
    return {"value": value, "status": status or ("UNKNOWN" if value is None else "LIMITED"),
            "support": support or {}, "raw": raw or {}, "reasons": list(reasons),
            "refs": refs[:64], "ref_count": len(refs), "refs_truncated": len(refs) > 64}


class StorePort(Protocol):
    def get(self, namespace: str, key: str, default=None): ...
    def put(self, namespace: str, key: str, value) -> None: ...
    def items(self, namespace: str) -> dict: ...
    def delete(self, namespace: str, key: str) -> None: ...
    def tx(self): ...


class FactSource(Protocol):
    """Returns verified, normalized LOCAL snapshots; reading never does remote I/O.

    candidates contain key/provider_did/service_id/version, declared capabilities,
    formats, payment modes, availability, costs and local opportunity state.
    metrics are scoped by candidate key and task. Proof verification belongs here.
    Private originals, credentials and endpoints are not part of scoring inputs.
    """

    def snapshot(self, search_id: str, result_revision: int | None, task: dict,
                 profile: dict, now: float) -> dict: ...

    def subject_view(self, subject: dict, now: float) -> dict: ...

    def reset_network_context(self, now: float) -> dict: ...


class MetadataRefreshPort(Protocol):
    """Explicit, budgeted metadata jobs. Never used by rank or page reads."""
    def start(self, request: dict) -> dict: ...
    def get(self, job_id: str) -> dict: ...
    def cancel(self, job_id: str) -> dict: ...


class TaskQualityPort(Protocol):
    """Task-scoped LOCAL metric; no evaluator, execution or transport on reads."""
    def metric(self, provider_did: str, service_id: str, version: str, task: dict, now: float) -> dict: ...
