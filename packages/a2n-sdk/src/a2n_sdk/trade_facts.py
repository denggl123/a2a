"""Versioned local trade facts. Delivery, quality and money are independent."""
from __future__ import annotations

import hashlib
import json
import time

FACT_NS = "trade_facts"
INDEX_NS = "trade_fact_index"
CONTEXT_NS = "trade_contexts"
TERMINAL_DELIVERY = {"COMPLETED", "ACCEPTED", "SETTLED", "SETTLEMENT_PENDING",
                     "SETTLEMENT_FAILED"}
UNKNOWN_STATES = {"TIMEOUT", "DELIVERY_UNKNOWN", "INTERRUPTED", "CANCEL_REQUESTED",
                  "ROUTE_UNKNOWN"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def trade_uid(buyer_did: str, provider_did: str, service_id: str, task_id: str) -> str:
    if not all(isinstance(x, str) and x for x in
               (buyer_did, provider_did, service_id, task_id)):
        raise ValueError("交易身份必须绑定买方、卖方、商品及任务")
    return "tr_" + digest(["a2n-trade-identity/1", buyer_did, provider_did, service_id, task_id])


def query_unresolved(outcome: dict) -> bool:
    """A failed observation of an existing task is no execution verdict."""
    meta = outcome.get("metadata") or {}
    return (meta.get("rpc_method") == "tasks/get" and not outcome.get("ok")
            and meta.get("remote_terminal") is not True
            and meta.get("technical_delivery") is not True)


def delivered(outcome: dict) -> bool:
    state = str(outcome.get("state") or "").upper()
    meta = outcome.get("metadata") or {}
    if meta.get("technical_delivery") is True:
        return True
    if state in TERMINAL_DELIVERY:
        return bool(outcome.get("ok", True) or outcome.get("verdict") or outcome.get("receipt"))
    # Conservative migration of old quality rejections; transport errors do not qualify.
    return (state == "REJECTED" and bool(outcome.get("verdict"))
            and outcome.get("result") is not None)


def axes(outcome: dict) -> dict:
    state = str(outcome.get("state") or "").upper()
    execution = ("DELIVERED" if delivered(outcome) else
                 "UNKNOWN" if state in UNKNOWN_STATES or query_unresolved(outcome) else
                 "CANCELED" if state in {"CANCELED", "CANCELLED"} else
                 "RUNNING" if state in {"RUNNING", "WORKING", "SUBMITTED", "INPUT-REQUIRED", "AUTH-REQUIRED", "READY"}
                 else "FAILED")
    verdict = outcome.get("verdict") or {}
    quality = "UNMEASURED"
    if execution == "DELIVERED" and verdict.get("quality_measured") is True:
        quality = "RULE_PASS" if verdict.get("passed") else "RULE_FAIL"
    settlement = outcome.get("settlement") or {}
    paid = str(settlement.get("state") or "").upper()
    payment = ({"NOT_CONFIGURED": "UNAVAILABLE", "SETTLED": "CONFIRMED",
        "CAPTURED": "CONFIRMED", "CONFIRMED": "CONFIRMED", "PROCESSING": "PENDING",
        "PENDING": "PENDING", "FAILED": "FAILED", "REJECTED": "FAILED",
        "UNKNOWN": "UNKNOWN", "NOT_REQUIRED": "NOT_REQUIRED", "DUE": "DUE"}.get(paid)
        or "UNAVAILABLE")
    return {"execution": execution, "quality": quality, "payment": payment,
            "refund": "NONE", "dispute": "NONE"}


class TradeFactsBook:
    def __init__(self, store, *, now=None):
        self.store = store
        self.now = now or time.time

    @staticmethod
    def local_key(scope, task_id):
        return digest([scope, task_id])

    def admit(self, scope, request, context: dict) -> dict:
        key = self.local_key(scope, request.task_id)
        current = self.store.get(CONTEXT_NS, key)
        if current:
            return current
        buyer = str(context.get("buyer_did") or "")
        provider = str(context.get("provider_did") or "")
        sid = str(context.get("service_id") or scope)
        wire_task_id = (request.metadata.get("_a2n_wire_task_id") or request.task_id)
        uid = (trade_uid(buyer, provider, sid, wire_task_id) if buyer and provider
               else "local_" + digest(["unverified-trade/1", scope, request.task_id]))
        row = {**context, "buyer_did": buyer, "provider_did": provider, "service_id": sid,
               "trade_uid": uid, "scope": scope, "task_id": request.task_id,
               "wire_task_id": wire_task_id,
               "relation_verified": bool(context.get("relation_verified", buyer and provider)), "admitted_at": self.now(),
               "known_self": bool(buyer and buyer == provider)}
        self.store.put(CONTEXT_NS, key, row)
        self.store.put(INDEX_NS, key, uid)
        self.store.put(FACT_NS, uid, {"v": "a2n-trade-facts/1", **row,
            "fact_revision": 1, "execution": "ADMITTED", "quality": "UNMEASURED",
            "free_reason": context.get("free_reason", "UNSPECIFIED"),
            "payment": "NOT_REQUIRED" if context.get("free") else "UNAVAILABLE",
            "refund": "NONE", "dispute": "NONE", "observed_at": self.now()})
        return row

    def observe(self, scope, request, outcome) -> dict:
        data = outcome.to_dict() if hasattr(outcome, "to_dict") else outcome
        key = self.local_key(scope, request.task_id)
        context = self.store.get(CONTEXT_NS, key)
        if not context:
            context = self.admit(scope, request, {"service_id": scope, "historical": True})
        uid = context["trade_uid"]
        previous = self.store.get(FACT_NS, uid) or {}
        facts = axes(data)
        facts["refund"] = previous.get("refund", "NONE")
        facts["dispute"] = previous.get("dispute", "NONE")
        newly_delivered = facts["execution"] == "DELIVERED"
        if previous.get("execution") == "DELIVERED":
            facts["execution"] = "DELIVERED"
        if context.get("free"):
            facts["payment"] = "NOT_REQUIRED"
        elif previous.get("financial_observation"):
            facts["payment"] = previous["payment"]
        row = {**previous, **facts, "fact_revision": int(previous.get("fact_revision", 0)) + 1,
               "observed_at": self.now(), "legacy_state": data.get("state"),
               "delivery_digest": digest(data.get("result")) if newly_delivered else previous.get("delivery_digest", "")}
        if facts["execution"] == "DELIVERED":
            row["delivered_at"] = previous.get("delivered_at") or self.now()
        self.store.put(FACT_NS, uid, row)
        if hasattr(outcome, "metadata"):
            public_axes = {k: row.get(k) for k in ("v", "fact_revision", "execution", "quality",
                           "free_reason", "payment", "refund", "dispute", "version")}
            outcome.metadata = {**outcome.metadata, "trade_uid": uid, "trade_facts": public_axes}
        return row

    def financial(self, uid, state, *, refund=False):
        if state not in {"CONFIRMED", "FAILED", "UNKNOWN", "READY", "PENDING", "NOT_REQUIRED"}:
            raise ValueError("INVALID_FINANCIAL_STATE")
        with self.store.tx():
            old = self.get(uid)
            if not old:
                self.store.put("payment_fact_pending", uid, {"state": state, "refund": refund})
                return None
            axis = "refund" if refund else "payment"
            if old.get(axis) == "CONFIRMED" and state != "CONFIRMED":
                return old
            row = {**old, axis: state, "financial_observation": True,
                "fact_revision": old["fact_revision"] + 1, "observed_at": self.now()}
            self.store.put(FACT_NS, uid, row)
            return row

    def for_call(self, scope, task_id):
        uid = self.store.get(INDEX_NS, self.local_key(scope, task_id))
        return self.store.get(FACT_NS, uid) if uid else None

    def get(self, uid):
        return self.store.get(FACT_NS, uid)

    def list(self, limit=50):
        return sorted(self.store.items(FACT_NS).values(),
                      key=lambda x: x.get("observed_at", 0), reverse=True)[:max(1, min(int(limit), 100))]
