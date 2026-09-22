"""Network observations, deliberately separate from quality and acceptance.

The monitor only establishes a TCP connection.  It never invokes an Agent and
therefore cannot create a task, acceptance verdict, or charge.  A small rolling
history lets human-facing consoles render the same useful signal as a VPN
client without coupling the UI to a transport implementation.
"""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urlsplit


class NetworkMonitor:
    def __init__(self, *, interval: float = 10.0, timeout: float = 3.0,
                 connector=socket.create_connection, workers: int = 16):
        self._samples = {}
        self._targets = {}
        self._versions = {}
        self._lock = threading.RLock()
        self._interval = max(0.05, float(interval))
        self._timeout = max(0.05, float(timeout))
        self._workers = max(1, min(int(workers), 32))
        self._connector = connector
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._executor = None
        self._inflight = {}

    def watch(self, key: str, endpoint: str) -> None:
        """Add or update a route observed by the background sampler."""
        # Validate before mutating state so malformed imported cards do not
        # poison the monitor loop.
        self._address(endpoint)
        with self._lock:
            self._versions[key] = self._versions.get(key, 0) + 1
            self._targets[key] = endpoint
        self._wake.set()

    def unwatch(self, key: str) -> None:
        with self._lock:
            self._versions[key] = self._versions.get(key, 0) + 1
            self._targets.pop(key, None)
            self._samples.pop(key, None)
        self._wake.set()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self
            self._stop.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="a2n-net-probe")
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="a2n-network-monitor")
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self._timeout + 2)
        executor = self._executor
        self._executor = None
        with self._lock:
            inflight = list(self._inflight.values())
            self._inflight.clear()
        for future in inflight:
            future.cancel()
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _address(endpoint: str) -> tuple[str, int]:
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("没有可探测的 HTTP 地址")
        return url.hostname, url.port or (443 if url.scheme == "https" else 80)

    def _run(self) -> None:
        # Probe immediately after startup/watch, then at a calm fixed cadence.
        while not self._stop.is_set():
            with self._lock:
                targets = [(key, endpoint, self._versions.get(key, 0))
                           for key, endpoint in self._targets.items()]
                executor = self._executor
            if executor:
                futures = []
                with self._lock:
                    for key, endpoint, generation in targets:
                        previous = self._inflight.get(key)
                        if previous is not None and not previous.done():
                            continue
                        future = executor.submit(
                            self.probe, key, endpoint, _generation=generation)
                        self._inflight[key] = future
                        future.add_done_callback(
                            lambda done, item=key: self._probe_finished(item, done))
                        futures.append(future)
                # One slow/offline Agent no longer blocks every other row.  The
                # connector timeout remains the upper bound for this batch. A
                # still-running key is never enqueued again next cycle.
                wait(futures, timeout=self._timeout + 0.5)
            self._wake.wait(self._interval)
            self._wake.clear()

    def _probe_finished(self, key: str, future) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                self._inflight.pop(key, None)

    def probe(self, key: str, endpoint: str, *, _generation: int | None = None) -> dict:
        with self._lock:
            generation = (self._versions.get(key, 0) if _generation is None
                          else _generation)
        host, port = self._address(endpoint)
        started = time.monotonic()
        sample = {"target": key, "kind": "tcp-connect", "checked_at": time.time(),
                  "reachable": False, "rtt_ms": None}
        try:
            with self._connector((host, port), timeout=self._timeout):
                sample.update(reachable=True, rtt_ms=round((time.monotonic()-started)*1000, 1))
        except OSError as exc:
            sample["error"] = type(exc).__name__
        with self._lock:
            # ``unwatch`` or replacing an endpoint while a TCP connect is in
            # flight invalidates the result.  Without this generation check an
            # already-deleted Agent could reappear in the console.
            if (self._versions.get(key, 0) != generation
                    or self._targets.get(key) != endpoint):
                return dict(sample)
            previous = self._samples.get(key) or {}
            history = list(previous.get("history") or [])
            history.append({"checked_at": sample["checked_at"],
                            "reachable": sample["reachable"],
                            "rtt_ms": sample["rtt_ms"]})
            sample["history"] = history[-30:]
            self._samples[key] = sample
            while len(self._samples) > 256:
                self._samples.pop(next(iter(self._samples)))
        return dict(sample)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [{**v, "history": [dict(point) for point in v.get("history") or []]}
                    for v in self._samples.values()]
