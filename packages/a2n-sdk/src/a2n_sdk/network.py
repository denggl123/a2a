"""Network observations, deliberately separate from quality and acceptance."""
from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlsplit


class NetworkMonitor:
    def __init__(self):
        self._samples = {}
        self._lock = threading.RLock()

    def probe(self, key: str, endpoint: str) -> dict:
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("没有可探测的 HTTP 地址")
        started = time.monotonic()
        sample = {"target": key, "kind": "tcp-connect", "checked_at": time.time(),
                  "reachable": False, "rtt_ms": None}
        try:
            with socket.create_connection((url.hostname, url.port or
                                            (443 if url.scheme == "https" else 80)), timeout=3):
                sample.update(reachable=True, rtt_ms=round((time.monotonic()-started)*1000, 1))
        except OSError as exc:
            sample["error"] = type(exc).__name__
        with self._lock:
            self._samples[key] = sample
            while len(self._samples) > 256:
                self._samples.pop(next(iter(self._samples)))
        return dict(sample)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._samples.values()]
