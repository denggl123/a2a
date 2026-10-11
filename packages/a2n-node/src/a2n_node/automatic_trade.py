"""One authorized call: match once, freeze the plan, recover only that plan."""
from __future__ import annotations

import time
import re
from a2n_sdk.trade_facts import digest
from a2n_sdk.ports import CallRequest, CallOutcome


class AutomaticTrade:
    def __init__(self, trades):
        self.trades, self.store = trades, trades.store
        self.payments = trades.daemon.payment_coordination

    def execute(self, body):
        if not isinstance(body, dict) or set(body) != {"offer_id", "command_id"}:
            raise ValueError("INVALID_AUTOMATIC_TRADE")
        command = body["command_id"]
        if not isinstance(command, str) or not 1 <= len(command) <= 128:
            raise ValueError("PAYMENT_COMMAND_ID_REQUIRED")
        # Serialize initial plan selection; existing payment/points guards own IO recovery.
        with self.payments._lock:
            try:
                return self._execute(body, command)
            except Exception as exc:
                row = self.store.get("automatic_trades", command)
                if row:
                    attempts = row.get("recovery_attempts", 0) + 1
                    code = str(exc).split(":", 1)[0]
                    code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", code) else None
                    self.store.put("automatic_trades", command, {**row, "body": body,
                        "recovery_attempts": attempts, "last_error_type": type(exc).__name__,
                        "last_error_code": code,
                        "action_required": str(exc).startswith("AUTOMATIC_SETTLEMENT_AUTHORIZATION_REVOKED"),
                        "updated_at": time.time(), "next_attempt_at": time.time() + min(300, 2 ** min(attempts, 8))})
                raise

    def recover(self, *, limit=1, stopping=lambda: False):
        """Fair, bounded recovery of previously authorized jobs; never create purchases."""
        rows = sorted(self.store.items("automatic_trades").items(),
                      key=lambda pair: pair[1].get("next_attempt_at", 0))
        recovered = []
        for command, row in rows:
            if len(recovered) >= limit or stopping():
                break
            if row.get("finished") or row.get("next_attempt_at", 0) > time.time():
                continue
            body = row.get("body")
            # Older paid rows can be recovered from their frozen preparation. An older
            # free row without an offer identity must never cause a guessed invocation.
            if not body and row.get("prepare", {}).get("offer_id"):
                body = {"offer_id": row["prepare"]["offer_id"], "command_id": command}
            if not body:
                continue
            recovered.append(command)
            try:
                self.execute(body)
            except Exception:
                # execute persisted the diagnostic and backoff before returning here.
                pass
        return recovered

    def call(self, scope, request):
        """Persist quote identity before settlement so SDK retries resume the same job."""
        from dataclasses import asdict
        command = "call_" + digest({"scope": scope, "task_id": request.task_id})
        fingerprint = digest({"scope": scope, "request": asdict(request)})
        with self.payments._lock:
            row = self.store.get("automatic_calls", command)
            if row and row["fingerprint"] != fingerprint:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            if not row:
                previous = self.store.task(scope, request.task_id)
                if previous:
                    # A previous direct invocation may already have external effects.
                    self.trades.daemon.calls.invoke(scope, request)
                    return self.trades.daemon.calls.get(scope, request.task_id, refresh_remote=True)
                if sum(not r.get("finished") for r in self.store.items("automatic_trades").values()) >= 128:
                    raise ValueError("AUTOMATIC_TRADE_CAPACITY_LIMIT")
                row = {"fingerprint": fingerprint, "scope": scope, "request": asdict(request)}
                self.store.put("automatic_calls", command, row)
            if not row.get("offer_id"):
                offer = self.trades.quote({"projection_id": scope, "request": row["request"]})
                row = {**row, "offer_id": offer["offer_id"]}
                self.store.put("automatic_calls", command, row)
            try:
                self.execute({"offer_id": row["offer_id"], "command_id": command})
            except Exception:
                if not self.store.get("automatic_trades", command):
                    raise
            return self.outcome(command, row)

    def outcome(self, command, call):
        job = self.store.get("automatic_trades", command) or {}
        # x402 recovery returns a wire receipt; the verified CallOutcome is journaled
        # by PaymentService, so read the journal instead of interpreting raw receipts.
        recorded = self.store.task(call["scope"], call["request"]["task_id"])
        delivery = (recorded or {}).get("outcome")
        if not delivery:
            candidate = (job.get("result") or {}).get("delivery")
            if isinstance(candidate, dict) and "ok" in candidate:
                delivery = candidate
        outcome = (CallOutcome(**delivery) if delivery else
            CallOutcome(False, call["request"]["task_id"], "UNKNOWN", target_ref=call["scope"]))
        if job.get("state") in {"CONFIRMED", "FAILED", "NOT_REQUIRED"}:
            outcome.settlement = {**outcome.settlement, "state": job["state"]}
        outcome.metadata = {**outcome.metadata, "automatic_trade": {
            "command_id": command, "offer_id": call["offer_id"], "plan_id": job.get("plan_id"),
            "state": job.get("state", "PREPARATION_PENDING"), "finished": job.get("finished", False),
            "action_required": job.get("action_required", False)}}
        return outcome

    def _execute(self, body, command):
        row = self.store.get("automatic_trades", command)
        fingerprint = digest(body)
        if row and row["fingerprint"] != fingerprint:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if row and row.get("finished"):
            return row["result"]
        quote = self.store.get("payment_buyer_quotes", body["offer_id"])
        if not quote:
            raise ValueError("PAYMENT_OFFER_NOT_FOUND")
        offer = quote["offer"]
        if not row:
            if sum(not r.get("finished") for r in self.store.items("automatic_trades").values()) >= 128:
                raise ValueError("AUTOMATIC_TRADE_CAPACITY_LIMIT")
            choice = self.payments.policy.match(offer)
            if offer["payment_state"] == "NOT_REQUIRED":
                # No payment method, intention, reservation or points entry is created.
                row = {"fingerprint": fingerprint, "free": True}
            else:
                if not choice.get("automatic"):
                    raise ValueError("AUTOMATIC_SETTLEMENT_NOT_AUTHORIZED")
                if not choice.get("candidates"):
                    raise ValueError("NO_COMMON_AUTHORIZED_SETTLEMENT_METHOD")
                selected = choice["candidates"][0]
                terms = offer["options"][selected["option_index"]]
                prepare = {"offer_id": body["offer_id"], "command_id": "auto_" + digest(command),
                    "option_index": selected["option_index"], "fee_cap_minor": selected["fee_cap_minor"],
                    **{k: selected[k] for k in ("funding_issuer", "max_cost") if k in selected}}
                if terms.get("mode") == "PAY" and "max_cost" not in prepare:
                    prepare["max_cost"] = terms["amount_minor"]
                row = {"fingerprint": fingerprint, "prepare": prepare,
                       "policy_revision": choice["policy_revision"], "option_index": selected["option_index"]}
            row = {**row, "body": body, "created_at": time.time()}
            self.store.put("automatic_trades", command, row)
        if row.get("free"):
            result = {"state": "NOT_REQUIRED", "delivery": self.trades.free_execute(body["offer_id"])}
        else:
            if not row.get("plan_id"):
                self._require_authorization(offer, row)
                try:
                    prepared = self.payments.prepare(row["prepare"])
                except Exception:
                    # Prepare can lose the acceptance response. Retain its original command;
                    # the next request resumes it, never chooses a second payment method.
                    self.store.put("automatic_trades", command, {**row, "state": "PREPARATION_PENDING"})
                    raise
                row = {**row, "plan_id": prepared["record"]["plan_id"]}
                self.store.put("automatic_trades", command, row)
            plan_id = row["plan_id"]
            prepared = self.payments._row(plan_id)
            if plan_id.startswith("pt_"):
                if not prepared.get("execution_started") and not prepared.get("decision"):
                    self._require_authorization(offer, row)
                if prepared["state"] in {"PROPOSED", "UNKNOWN"} and not prepared.get("execution_started"):
                    prepared = self.trades.daemon.points.prepare(plan_id)
                result = self.trades.execute_points(plan_id)
                state = result["payment"]["state"]
            else:
                intent = self.payments.payments.get(prepared["intent_id"])
                flow = prepared["record"]["terms"]["flow"]
                if flow == "upfront":
                    if intent["state"] == "READY":
                        self._require_authorization(offer, row)
                        intent = self.payments.pay(plan_id)
                    elif intent["state"] not in {"CONFIRMED", "FAILED"}:
                        intent = self.payments.pay(plan_id, reconcile=True)
                    result = {"payment": intent}
                    if intent["state"] == "CONFIRMED":
                        result["delivery"] = self.trades.execute(plan_id)
                elif intent["state"] in {"UNKNOWN", "PROCESSING", "SUBMITTING"}:
                    result = self.payments.reconcile_x402(plan_id)
                elif intent["state"] == "CONFIRMED" and self.store.get("x402_plans", prepared["intent_id"]):
                    result = self.payments.reconcile_x402(plan_id)
                elif intent["state"] == "FAILED":
                    result = {"payment": intent}
                else:
                    if intent["state"] == "READY":
                        self._require_authorization(offer, row)
                    result = {"delivery": self.trades.execute(plan_id)}
                state = self.payments.payments.get(prepared["intent_id"])["state"]
            result = {**result, "state": state, "plan_id": plan_id,
                      "option_index": row["option_index"], "policy_revision": row["policy_revision"]}
        # A confirmed payment with unknown execution still needs original delivery recovery.
        delivery = result.get("delivery") or {}
        if "ok" not in delivery and row.get("plan_id"):
            plan = self.payments._row(row["plan_id"])
            delivery = (self.store.task(plan["scope"], plan["request"]["task_id"]) or {}).get("outcome") or {}
            if delivery:
                result = {**result, "delivery": delivery}
        finished = (result["state"] == "FAILED" or result["state"] in {"CONFIRMED", "NOT_REQUIRED"}
                    and (delivery.get("metadata", {}).get("technical_delivery") is True
                         or delivery.get("state") in {"COMPLETED", "ACCEPTED", "SETTLED", "REJECTED", "FAILED", "CANCELED"}))
        self.store.put("automatic_trades", command, {**row, "state": result["state"],
                       "finished": finished, "result": result, "body": body,
                       "updated_at": time.time(), "next_attempt_at": time.time() + 5,
                       "last_error_type": None, "last_error_code": None, "action_required": False})
        return result

    def _require_authorization(self, offer, row):
        current = self.payments.policy.match(offer)
        selected = next((c for c in current.get("candidates", [])
                         if c["option_index"] == row["option_index"]), None)
        frozen = row["prepare"]
        if (not current.get("automatic") or selected is None
                or frozen["fee_cap_minor"] > selected["fee_cap_minor"]
                or "funding_issuer" in selected and selected["funding_issuer"] != frozen.get("funding_issuer")
                or "max_cost" in selected and frozen.get("max_cost", 0) > selected["max_cost"]):
            raise ValueError("AUTOMATIC_SETTLEMENT_AUTHORIZATION_REVOKED")
