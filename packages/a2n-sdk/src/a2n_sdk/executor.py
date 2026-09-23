"""Small daemon-thread executor used by the local node.

``ThreadPoolExecutor`` deliberately joins every worker during interpreter
shutdown.  That is normally helpful, but an untrusted local Agent callable can
block forever and prevent the whole node process from stopping.  Python cannot
safely kill a running thread, so the honest boundary is:

* queued work is cancelable;
* running work is recorded as interrupted after a bounded grace period;
* daemon workers may finish in the background, but cannot hold process exit.

This module owns only execution mechanics.  Durable task truth remains in
``CallService``/``LocalStore``.
"""
from __future__ import annotations

from concurrent.futures import Future
import queue
import threading
import time
from typing import Any, Callable


class DaemonExecutor:
    """A minimal ``submit``/``shutdown`` executor with daemon workers."""

    def __init__(self, max_workers: int, *, thread_name_prefix: str) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers 必须大于 0")
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._threads = [threading.Thread(
            target=self._worker, daemon=True,
            name=f"{thread_name_prefix}-{index + 1}")
            for index in range(max_workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        with self._lock:
            if self._closed:
                raise RuntimeError("执行器已经停止")
            future: Future = Future()
            self._queue.put((future, fn, args, kwargs))
            return future

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                future, fn, args, kwargs = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def shutdown(self, *, wait_timeout: float = 0.0,
                 cancel_futures: bool = True) -> bool:
        """Stop accepting work and wait at most ``wait_timeout`` seconds.

        Returns ``True`` when every worker exited within the grace period.
        Running Python code cannot be killed; daemon workers intentionally do
        not keep interpreter shutdown alive.
        """
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_futures:
                    with self._queue.mutex:
                        pending = list(self._queue.queue)
                    for item in pending:
                        if item is not None:
                            item[0].cancel()
                for _thread in self._threads:
                    self._queue.put(None)
        deadline = time.monotonic() + max(0.0, wait_timeout)
        for thread in self._threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        return not any(thread.is_alive() for thread in self._threads)
