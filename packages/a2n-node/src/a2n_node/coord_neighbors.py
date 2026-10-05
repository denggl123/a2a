"""Maintain explicitly configured coordination neighbours; never expand a search."""
from __future__ import annotations

import threading
import time


class CoordinationNeighbors:
    REFRESH_SECONDS = 45
    MAX_ROOTS = 32
    BATCH_SIZE = 3

    def __init__(self, network, roots):
        self.network, self.roots = network, roots
        self._stop = threading.Event()
        self._thread = None
        self._attempted = {}
        self.last_error = ""

    def step(self):
        roots = list(dict.fromkeys(self.roots()))[:self.MAX_ROOTS]
        self._attempted = {base: at for base, at in self._attempted.items() if base in roots}
        attempted = 0
        for base in roots:
            if self._stop.is_set():
                break
            if time.monotonic() - self._attempted.get(base, float('-inf')) < self.REFRESH_SECONDS:
                continue
            self._attempted[base] = time.monotonic()
            try:
                self.network.handshake({"endpoint": base.rstrip('/') + '/public/v1/coord'}, 16384, 1.5)
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
            attempted += 1
            if attempted >= self.BATCH_SIZE:
                break

    def _run(self):
        while not self._stop.is_set():
            self.step()
            self._stop.wait(2)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='a2n-coord-neighbours')
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)
