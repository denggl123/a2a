from __future__ import annotations

import threading
import time

from a2n_sdk.network import NetworkMonitor


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_background_monitor_keeps_bounded_latency_history():
    calls = []

    def connect(address, timeout):
        calls.append((address, timeout))
        return _Connection()

    monitor = NetworkMonitor(interval=0.05, timeout=0.1, connector=connect).start()
    try:
        monitor.watch("agent-a", "https://agent.example/a2a")
        deadline = time.time() + 1
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        row = monitor.snapshot()[0]
        assert row["target"] == "agent-a"
        assert row["reachable"] is True
        assert len(row["history"]) >= 2
        assert calls[0] == (("agent.example", 443), 0.1)
        for _ in range(35):
            monitor.probe("agent-a", "https://agent.example/a2a")
        assert len(monitor.snapshot()[0]["history"]) == 30
    finally:
        monitor.stop()


def test_unwatch_removes_route_and_samples():
    monitor = NetworkMonitor(connector=lambda *_args, **_kwargs: _Connection())
    monitor.watch("gone", "http://127.0.0.1:8765/a2a")
    monitor.probe("gone", "http://127.0.0.1:8765/a2a")
    assert monitor.snapshot()
    monitor.unwatch("gone")
    assert monitor.snapshot() == []


def test_malformed_routes_are_rejected_before_watch():
    monitor = NetworkMonitor()
    try:
        monitor.watch("bad", "file:///tmp/agent")
    except ValueError as exc:
        assert "HTTP" in str(exc)
    else:
        raise AssertionError("non-HTTP route should be rejected")
    assert monitor.snapshot() == []


def test_unwatch_during_inflight_probe_cannot_resurrect_sample():
    entered = threading.Event()
    release = threading.Event()

    def connect(_address, timeout):
        entered.set()
        assert release.wait(1)
        return _Connection()

    monitor = NetworkMonitor(interval=10, timeout=0.2, connector=connect)
    monitor.watch("gone", "http://127.0.0.1:8765/a2a")
    worker = threading.Thread(
        target=monitor.probe,
        args=("gone", "http://127.0.0.1:8765/a2a"))
    worker.start()
    assert entered.wait(1)
    monitor.unwatch("gone")
    release.set()
    worker.join(1)
    assert monitor.snapshot() == []


def test_background_probes_routes_concurrently():
    lock = threading.Lock()
    all_entered = threading.Event()
    release = threading.Event()
    active = 0
    maximum = 0

    def connect(_address, timeout):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 3:
                all_entered.set()
        assert release.wait(1)
        with lock:
            active -= 1
        return _Connection()

    monitor = NetworkMonitor(interval=10, timeout=0.2, connector=connect, workers=3)
    for index in range(3):
        monitor.watch(f"agent-{index}", f"http://127.0.0.1:{8800 + index}/a2a")
    monitor.start()
    try:
        assert all_entered.wait(1)
        assert maximum == 3
    finally:
        release.set()
        monitor.stop()


def test_slow_target_is_never_queued_again_while_probe_is_inflight():
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def connect(_address, timeout):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return _Connection()

    monitor = NetworkMonitor(interval=0.05, timeout=0.05,
                             connector=connect, workers=1)
    monitor.watch("slow", "http://127.0.0.1:8899/a2a")
    monitor.start()
    try:
        assert entered.wait(1)
        time.sleep(0.2)
        assert calls == 1
    finally:
        release.set()
        monitor.stop()
