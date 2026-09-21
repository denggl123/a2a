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
                self._futures[key] = self._pool.submit(self._execute, scope, request)
            future = self._futures.get(key)
        if blocking and future:
            future.result()
        return self.get(scope, request.task_id)

    def _execute(self, scope: str, request: CallRequest) -> None:
        try:
            try:
                outcome = self._invoke(scope, request)
            except Exception as exc:
                outcome = CallOutcome(ok=False, task_id=request.task_id, state="FAILED",
                                      error=f"{type(exc).__name__}: {exc}")
            self.store.finish(scope, request.task_id, outcome.to_dict())
        finally:
            with self._lock:
                self._futures.pop((scope, request.task_id), None)

    def get(self, scope: str, task_id: str) -> CallOutcome | None:
        record = self.store.task(scope, task_id)
        if not record:
            return None
        if record["outcome"]:
            return CallOutcome(**record["outcome"])
        interrupted = record["state"] == "INTERRUPTED"
        return CallOutcome(
            ok=not interrupted, task_id=task_id,
            state="INTERRUPTED" if interrupted else "WORKING",
            error="节点在上次调用中退出，远端结果未知；未自动重放" if interrupted else None,
            target_ref=scope)

    def stop(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)
