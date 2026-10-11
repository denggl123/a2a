"""Durable execution service, independent of A2A/HTTP and transport details."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import threading
import time

from .executor import DaemonExecutor
from .ports import CallOutcome, CallRequest
from .storage import LocalStore
from .trade_facts import query_unresolved


class CallService:
    _REMOTE_OPEN = {"TIMEOUT", "DELIVERY_UNKNOWN", "INTERRUPTED", "CANCEL_REQUESTED",
                    "WORKING", "SUBMITTED", "INPUT-REQUIRED", "AUTH-REQUIRED"}

    def __init__(self, invoke, store: LocalStore, *, refresh_remote=None,
                 cancel_remote=None, workers: int = 8, max_pending: int = 64,
                 stop_timeout: float = 2.0, finalize_outcome=None,
                 on_delivery=None, on_admit=None, durable_delivery=False, prepare_request=None):
        self._invoke = invoke
        self._refresh_remote = refresh_remote
        self._cancel_remote = cancel_remote
        self._finalize_outcome = finalize_outcome
        # 交付完成后的旁路钩子（如试用计数 + 样品）。**只观察，不改变结果**：
        # 它抛错也不许影响这次调用 —— 记账是附带的，不是调用的一部分。
        self._on_delivery = on_delivery
        self._on_admit = on_admit
        self._durable_delivery = durable_delivery
        self._prepare_request = prepare_request
        self.store = store
        self.store.interrupt_unfinished()
        self._pool = DaemonExecutor(workers, thread_name_prefix="a2n-call")
        self._lock = threading.RLock()
        self._futures = {}
        self._cancel_requested: set[tuple[str, str]] = set()
        self._contexts: dict[tuple[str, str], str] = {}
        self.max_pending = max_pending
        self.stop_timeout = max(0.0, stop_timeout)
        self._closed = False

    def invoke(self, scope: str, request: CallRequest, *, blocking: bool = True) -> CallOutcome:
        with self._lock:
            if self._closed:
                raise RuntimeError("节点正在停止")
            if self._prepare_request:
                self._prepare_request(scope, request)
            stored_request = asdict(request)
            fingerprint = self._fingerprint(stored_request)
            key = (scope, request.task_id)
            if len(self._futures) >= self.max_pending and not self.store.task(*key):
                raise ValueError("节点任务队列已满，请稍后再试")
            old = self.store.task(*key)
            # Keep the legacy hash while allowing retries whose only difference is
            # an explicitly defined transport envelope. Business options stay bound.
            if old and old.get("request") and self._fingerprint(old["request"]) == fingerprint:
                fingerprint = old["fingerprint"]
            with self.store.tx():
                claimed = self.store.claim(scope, request.task_id, fingerprint,
                                           stored_request, state="READY")
                if claimed and self._on_admit:
                    self._on_admit(scope, request)
            if claimed:
                self._contexts[key] = request.context_id
                import contextvars
                self._futures[key] = self._pool.submit(contextvars.copy_context().run,self._execute, scope, request)
            future = self._futures.get(key)
        if blocking and future:
            future.result()
        return self.get(scope, request.task_id)

    @staticmethod
    def _fingerprint(stored_request):
        body = dict(stored_request)
        body.pop("task_id", None)
        meta = dict(body.get("metadata") or {})
        for name in ("a2nPeerRequest", "a2nTradeAuthorization", "_a2n_transport", "_a2n_local_admission"):
            meta.pop(name, None)
        if isinstance(meta.get("_a2n_verified_peer"), dict):
            meta["_a2n_verified_peer"] = {"caller_did": meta["_a2n_verified_peer"].get("caller_did")}
        body["metadata"] = meta
        if isinstance(body.get("message"), dict):
            body["message"] = {k: v for k, v in body["message"].items() if k != "messageId"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                       allow_nan=False).encode()).hexdigest()

    def recover_ready(self):
        """Resume jobs proven never dispatched, after runtime bindings are restored."""
        with self._lock:
            if self._closed:
                return
            for row in self.store.ready_tasks(self.max_pending):
                key = (row["scope"], row["task_id"])
                request = self._request(row)
                if key in self._futures or not request or len(self._futures) >= self.max_pending:
                    continue
                self._contexts[key] = request.context_id
                self._futures[key] = self._pool.submit(self._execute, row["scope"], request)

    def _persist(self, scope, request, outcome, timing=None):
        if not self._durable_delivery or not self._on_delivery or request is None:
            with self.store.tx():
                self.store.finish(scope, outcome.task_id, outcome.to_dict())
                self._save_timing(scope, request, outcome, timing)
            return
        event_key = hashlib.sha256(json.dumps([scope, outcome.task_id]).encode()).hexdigest()
        event = {"scope": scope, "task_id": outcome.task_id, "state": "PENDING",
                 "updated": time.time()}
        original_metadata = dict(outcome.metadata)
        outcome.metadata.pop("projection_pending", None)
        try:
            with self.store.tx():
                self._on_delivery(scope, request, outcome)
                self.store.finish(scope, outcome.task_id, outcome.to_dict())
                self._save_timing(scope, request, outcome, timing)
                self.store.put("delivery_events", event_key, {**event, "state": "DONE"})
        except Exception as exc:
            # The complete result survives a projection failure; the whole failed
            # transaction rolled back, so replaying the durable event is safe.
            with self.store.tx():
                outcome.metadata = {**original_metadata, "projection_pending": True}
                self.store.finish(scope, outcome.task_id, outcome.to_dict())
                self._save_timing(scope, request, outcome, timing)
                self.store.put("delivery_events", event_key, {
                    **event, "error": f"{type(exc).__name__}: {exc}"[:500]})

    def _save_timing(self, scope, request, outcome, timing):
        # Generic execution telemetry. Never added to receipts/public samples.
        if timing is None or request is None:
            return
        key = hashlib.sha256(json.dumps([scope, request.task_id]).encode()).hexdigest()
        previous = self.store.get("execution_observations", key)
        if previous:
            return  # local retries and projection recovery cannot create attempts
        try:
            size = len(json.dumps(request.payload, ensure_ascii=False).encode())
            bucket = "small" if size < 4096 else "medium" if size < 65536 else "large"
        except (TypeError, ValueError):
            bucket = "unknown"
        complete = outcome.metadata.get("technical_delivery") is True or outcome.state in {"COMPLETED", "ACCEPTED", "SETTLED"}
        self.store.put("execution_observations", key, {"v": "a2n-execution-observation/1",
            "scope": scope, "task_id": request.task_id, "skill": request.skill,
            "elapsed_ms": timing, "at": time.time(), "state": outcome.state,
            "workload_bucket": bucket, "stage": outcome.metadata.get("stage", "unknown"),
            "transport": outcome.metadata.get("transport", "unknown"),
            "complete_timing": complete, "timing_kind": "stream_first_payload" if outcome.metadata.get("local_stream_observation") else "non_stream_total_wait",
            "first_useful_ms": outcome.metadata["local_stream_observation"]["first_useful_ms"] if outcome.metadata.get("local_stream_observation") else timing if complete and outcome.result is not None else None})

    def recover_deliveries(self, limit=100):
        recovered = 0
        for key, event in self.store.items("delivery_events").items():
            if event.get("state") != "PENDING" or recovered >= limit:
                continue
            row = self.store.task(event["scope"], event["task_id"])
            request = self._request(row or {})
            if request and (row or {}).get("outcome"):
                self._persist(event["scope"], request, CallOutcome(**row["outcome"]))
                recovered += 1
        return recovered

    def observe_external_delivery(self, scope, task_id, outcome):
        """Persist an authenticated recovered result without dispatching a task."""
        with self._lock:
            row = self.store.task(scope, task_id)
            request = self._request(row or {})
            if not request or (scope, task_id) in self._futures:
                raise ValueError("RECOVERY_REQUIRES_RECORDED_IDLE_CALL")
            outcome.task_id = task_id
            self._persist(scope, request, outcome)
            return outcome

    def _execute(self, scope: str, request: CallRequest) -> None:
        key = (scope, request.task_id)
        started = time.monotonic()
        observation=None
        try:
            with self._lock:
                if self._closed:
                    return
                self.store.mark_running(scope, request.task_id)
            try:
                from .progress import capture
                with capture(started=started) as observation:
                    outcome = self._invoke(scope, request)
                if not isinstance(outcome, CallOutcome):
                    raise TypeError("调用执行体没有返回 CallOutcome")
                if self._finalize_outcome:
                    outcome = self._finalize_outcome(scope, request, outcome)
                    if not isinstance(outcome, CallOutcome):
                        raise TypeError("调用凭证处理器没有返回 CallOutcome")
            except Exception as exc:
                outcome = CallOutcome(ok=False, task_id=request.task_id, state="FAILED",
                                      error=f"{type(exc).__name__}: {exc}")
            outcome.metadata = dict(outcome.metadata)
            outcome.metadata.pop("local_stream_observation",None)
            if observation and observation.first_useful_ms is not None:
                outcome.metadata["local_stream_observation"]={"first_useful_ms":observation.first_useful_ms,"events":observation.events,"source":"LOCAL_RECEIVED_PAYLOAD"}
            if request.context_id:
                outcome.metadata.setdefault("a2aContextId", request.context_id)
            if outcome.state == "TIMEOUT":
                # A timeout is terminal for this local idempotency record, not
                # proof that the remote task stopped.  Reusing the same task id
                # returns this record and never sends message/send a second time.
                outcome.metadata.setdefault("remote_effect_unknown", True)
                outcome.metadata.setdefault("remote_terminal", False)
                outcome.metadata.setdefault("replay_safe", False)
                outcome.metadata.setdefault("same_task_replay", "returns_recorded_outcome")
            with self._lock:
                if self._closed:
                    return
                if key in self._cancel_requested:
                    # A request is not an acknowledgement. If execution (and
                    # possibly settlement) completes anyway, persist that final
                    # truth instead of hiding a result or charge behind a forever
                    # "cancel requested" record.
                    outcome.metadata.update({
                        "cancel_requested": True,
                        "cancel_acknowledged": False,
                        "cancel_note": "取消请求未获执行体确认；记录的是最终真实结果",
                    })
                try:
                    self._persist(scope, request, outcome, timing=max(0, (time.monotonic()-started)*1000))
                except (TypeError, ValueError) as exc:
                    # A plug-in may return bytes or an arbitrary Python object.
                    # Never leave the durable idempotency row in RUNNING after
                    # the future has disappeared merely because JSON encoding
                    # failed; record a safe, serializable terminal truth.
                    fallback = CallOutcome(
                        ok=False, task_id=request.task_id, state="FAILED",
                        error=f"结果无法安全保存：{type(exc).__name__}: {exc}",
                        target_ref=scope,
                        metadata={"stage": "persistence", "result_discarded": True},
                    )
                    self._persist(scope, request, fallback)
                    outcome = fallback
            if not self._durable_delivery:
                self._notify(scope, request, outcome)
        finally:
            with self._lock:
                self._futures.pop(key, None)
                self._cancel_requested.discard(key)
                self._contexts.pop(key, None)

    def _notify(self, scope: str, request: CallRequest, outcome: CallOutcome) -> None:
        """交付落库之后的旁路记账。钩子抛错**不许**影响这次调用。"""
        if not self._on_delivery:
            return
        try:
            self._on_delivery(scope, request, outcome)
        except Exception:  # noqa: BLE001 - 记账失败不改变交付事实
            pass

    @staticmethod
    def _request(record: dict) -> CallRequest | None:
        raw = record.get("request")
        if not isinstance(raw, dict):
            return None
        try:
            return CallRequest(**raw)
        except TypeError:
            return None

    def get(self, scope: str, task_id: str, *, refresh_remote: bool = False, known_remote_task_id=None) \
            -> CallOutcome | None:
        record = self.store.task(scope, task_id)
        if not record:
            return None
        if record["outcome"]:
            outcome = CallOutcome(**record["outcome"])
            if query_unresolved(record["outcome"]) and outcome.state != "DELIVERY_UNKNOWN":
                # Migrate older query errors without changing the execution or payment.
                outcome.state = "DELIVERY_UNKNOWN"
                outcome.metadata = {**outcome.metadata, "remote_query_failed": True,
                                    "remote_effect_unknown": True, "remote_terminal": False}
                request = self._request(record)
                self._persist(scope, request, outcome)
            if (known_remote_task_id and outcome.state.upper() in self._REMOTE_OPEN
                    and not outcome.metadata.get("a2a_task_id")):
                # A trusted adapter may know the original wire id even if the
                # response was lost. This authorizes a query, never dispatch.
                outcome.metadata = {**outcome.metadata, "a2a_task_id": known_remote_task_id,
                                    "original_task_query": True}
                self.store.finish(scope, task_id, outcome.to_dict())
            wire_id = (((record.get("request") or {}).get("metadata") or {}).get("_a2n_wire_task_id"))
            if wire_id:
                outcome.metadata["wire_task_id"] = wire_id
            if (refresh_remote and self._refresh_remote
                    and outcome.state.upper() in self._REMOTE_OPEN
                    and outcome.metadata.get("a2a_task_id")):
                request = self._request(record)
                if request:
                    try:
                        refreshed = self._refresh_remote(scope, request, outcome)
                        if not isinstance(refreshed, CallOutcome):
                            raise TypeError("远端任务刷新器没有返回 CallOutcome")
                        if refreshed.task_id != task_id:
                            raise ValueError("远端任务刷新器改变了本机 task id")
                        if self._finalize_outcome:
                            refreshed = self._finalize_outcome(scope, request, refreshed)
                        refreshed.metadata = {
                            **outcome.metadata, **refreshed.metadata,
                            "remote_refreshed": True,
                            "remote_refreshed_at": time.time(),
                        }
                        self._persist(scope, request, refreshed)
                        if not self._durable_delivery:
                            self._notify(scope, request, refreshed)
                        return refreshed
                    except Exception as exc:
                        outcome.metadata = {
                            **outcome.metadata,
                            "remote_refresh_error": f"{type(exc).__name__}: {exc}",
                            "remote_refreshed_at": time.time(),
                        }
                        self.store.finish(scope, task_id, outcome.to_dict())
            return outcome
        interrupted = record["state"] == "INTERRUPTED"
        with self._lock:
            context_id = self._contexts.get((scope, task_id), "")
        metadata = {"remote_effect_unknown": True, "remote_terminal": False,
                    "replay_safe": False,
                    "same_task_replay": "returns_recorded_outcome"} if interrupted else {}
        if context_id:
            metadata["a2aContextId"] = context_id
        wire_id = (((record.get("request") or {}).get("metadata") or {}).get("_a2n_wire_task_id"))
        if wire_id:
            metadata["wire_task_id"] = wire_id
        return CallOutcome(
            ok=not interrupted, task_id=task_id,
            state="INTERRUPTED" if interrupted else "WORKING",
            error="节点在上次调用中退出，远端结果未知；未自动重放" if interrupted else None,
            target_ref=scope, metadata=metadata)

    def cancel(self, scope: str, task_id: str) -> CallOutcome | None:
        """Cancel a queued/running task without ever making it replayable.

        Queued futures are removed before execution.  A Python function that is
        already running cannot be interrupted safely, so ``CANCEL_REQUESTED`` is
        only an interim state.  Its eventual result/settlement replaces that
        state, preserving the financial truth even when cancellation was not
        acknowledged by the execution adapter.
        """
        key = (scope, task_id)
        with self._lock:
            record = self.store.task(scope, task_id)
            if not record:
                return None
            if record["outcome"]:
                current = CallOutcome(**record["outcome"])
                request = self._request(record)
                can_forward = bool(
                    self._cancel_remote and request
                    and current.state.upper() in self._REMOTE_OPEN
                    and current.metadata.get("a2a_task_id"))
                if not can_forward:
                    return current
            else:
                current = None
                request = None
            if current is None:
                if record["state"] == "INTERRUPTED":
                    # After a crash the remote side may already have acted.
                    # Calling that uncertainty "canceled" would be false.
                    return self.get(scope, task_id)
                context_id = self._contexts.get(key, "")
                future = self._futures.get(key)
                stopped_before_start = bool(future and future.cancel())
                remote_effect_unknown = not stopped_before_start
                metadata = {"remote_effect_unknown": remote_effect_unknown}
                if context_id:
                    metadata["a2aContextId"] = context_id
                if remote_effect_unknown:
                    metadata["cancel_requested"] = True
                    metadata["cancel_note"] = "本机已停止等待；远端或后台任务可能仍在执行"
                    outcome = CallOutcome(
                        ok=True, task_id=task_id, state="CANCEL_REQUESTED",
                        target_ref=scope, metadata=metadata)
                else:
                    outcome = CallOutcome(
                        ok=False, task_id=task_id, state="CANCELED",
                        error="任务已在开始执行前取消",
                        target_ref=scope, metadata=metadata)
                if remote_effect_unknown and future:
                    self._cancel_requested.add(key)
                else:
                    self._futures.pop(key, None)
                    self._contexts.pop(key, None)
                self._persist(scope, self._request(record), outcome)
                return outcome

        # Network I/O must not hold the executor state lock.
        assert current is not None and request is not None
        try:
            outcome = self._cancel_remote(scope, request, current)
            if not isinstance(outcome, CallOutcome):
                raise TypeError("远端取消器没有返回 CallOutcome")
            if outcome.task_id != task_id:
                raise ValueError("远端取消器改变了本机 task id")
        except Exception as exc:
            current.metadata = {
                **current.metadata,
                "cancel_requested": True,
                "cancel_acknowledged": False,
                "remote_effect_unknown": True,
                "remote_cancel_error": f"{type(exc).__name__}: {exc}",
            }
            self.store.finish(scope, task_id, current.to_dict())
            return current
        canceled = outcome.state.upper() in {"CANCELED", "CANCELLED"}
        remote_terminal = bool(outcome.metadata.get("remote_terminal"))
        remote_progress = outcome.state.upper() in {
            "WORKING", "SUBMITTED", "INPUT-REQUIRED", "AUTH-REQUIRED"}
        if not canceled and not remote_terminal and not remote_progress:
            # A transport/protocol failure while asking for cancellation says
            # nothing about the task's business state.  Keep the last known
            # task truth and attach the failed cancellation attempt to it.
            current.metadata = {
                **current.metadata,
                "cancel_requested": True,
                "cancel_acknowledged": False,
                "remote_effect_unknown": True,
                "remote_cancel_error": outcome.error or outcome.state,
                "remote_cancel_network": outcome.metadata,
            }
            self.store.finish(scope, task_id, current.to_dict())
            return current
        outcome.metadata = {
            **current.metadata, **outcome.metadata,
            "cancel_requested": True,
            "cancel_acknowledged": canceled,
            "remote_effect_unknown": not canceled,
            "remote_terminal": canceled or remote_terminal,
        }
        self._persist(scope, request, outcome)
        return outcome

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for (scope, task_id), future in list(self._futures.items()):
                record = self.store.task(scope, task_id)
                if record and record.get("outcome") and str(record["state"]).upper() not in {
                        "RUNNING", "WORKING", "CANCEL_REQUESTED"}:
                    continue
                queued = future.cancel()
                outcome = CallOutcome(
                    ok=False, task_id=task_id,
                    state="CANCELED" if queued else "INTERRUPTED",
                    error=("节点停止前任务尚未开始" if queued else
                           "节点停止等待；执行体可能仍在后台运行，结果不会覆盖此记录"),
                    target_ref=scope,
                    metadata={
                        "shutdown": True,
                        "remote_effect_unknown": not queued,
                        "remote_terminal": queued,
                        "replay_safe": False,
                        "same_task_replay": "returns_recorded_outcome",
                    },
                )
                self._persist(scope, self._request(record or {}), outcome)
            self._futures.clear()
            self._cancel_requested.clear()
            self._contexts.clear()
        self._pool.shutdown(wait_timeout=self.stop_timeout, cancel_futures=True)
