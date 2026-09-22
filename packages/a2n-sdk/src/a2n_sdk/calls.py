"""Durable execution service, independent of A2A/HTTP and transport details."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import threading

from .ports import CallOutcome, CallRequest
from .storage import LocalStore


class CallService:
    def __init__(self, invoke, store: LocalStore, *, workers: int = 8, max_pending: int = 64):
        self._invoke = invoke
        self.store = store
        self.store.interrupt_unfinished()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="a2n-call")
        self._lock = threading.RLock()
        self._futures = {}
        self._cancel_requested: set[tuple[str, str]] = set()
        self._contexts: dict[tuple[str, str], str] = {}
        self.max_pending = max_pending
        self._closed = False

    def invoke(self, scope: str, request: CallRequest, *, blocking: bool = True) -> CallOutcome:
        body = asdict(request)
        body.pop("task_id")
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                               allow_nan=False).encode()).hexdigest()
        key = (scope, request.task_id)
        with self._lock:
            if self._closed:
                raise RuntimeError("节点正在停止")
            if len(self._futures) >= self.max_pending and not self.store.task(*key):
                raise ValueError("节点任务队列已满，请稍后再试")
            claimed = self.store.claim(scope, request.task_id, fingerprint)
            if claimed:
                self._contexts[key] = request.context_id
                self._futures[key] = self._pool.submit(self._execute, scope, request)
            future = self._futures.get(key)
        if blocking and future:
            future.result()
        return self.get(scope, request.task_id)

    def _execute(self, scope: str, request: CallRequest) -> None:
        key = (scope, request.task_id)
        try:
            try:
                outcome = self._invoke(scope, request)
                if not isinstance(outcome, CallOutcome):
                    raise TypeError("调用执行体没有返回 CallOutcome")
            except Exception as exc:
                outcome = CallOutcome(ok=False, task_id=request.task_id, state="FAILED",
                                      error=f"{type(exc).__name__}: {exc}")
            outcome.metadata = dict(outcome.metadata)
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
                    self.store.finish(scope, request.task_id, outcome.to_dict())
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
                    self.store.finish(scope, request.task_id, fallback.to_dict())
        finally:
            with self._lock:
                self._futures.pop(key, None)
                self._cancel_requested.discard(key)
                self._contexts.pop(key, None)

    def get(self, scope: str, task_id: str) -> CallOutcome | None:
        record = self.store.task(scope, task_id)
        if not record:
            return None
        if record["outcome"]:
            return CallOutcome(**record["outcome"])
        interrupted = record["state"] == "INTERRUPTED"
        with self._lock:
            context_id = self._contexts.get((scope, task_id), "")
        metadata = {"remote_effect_unknown": True, "remote_terminal": False,
                    "replay_safe": False,
                    "same_task_replay": "returns_recorded_outcome"} if interrupted else {}
        if context_id:
            metadata["a2aContextId"] = context_id
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
                return CallOutcome(**record["outcome"])
            if record["state"] == "INTERRUPTED":
                # After a crash the remote side may already have acted.  Calling
                # that uncertainty "canceled" would be a false guarantee.
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
            self.store.finish(scope, task_id, outcome.to_dict())
            return outcome

    def stop(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)
