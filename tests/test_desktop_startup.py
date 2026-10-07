"""Desktop startup races must reuse one identity without weakening storage locks."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from a2n_node import desktop, deployment
from a2n_node.daemon import Daemon
from a2n_node.home import HomeInUseError, acquire_desktop_lock, acquire_home_lock
from a2n_node.product_cli import health
from a2n_node.protection import EnvironmentProtector


def test_launcher_election_preserves_the_separate_storage_lock(tmp_path):
    with acquire_desktop_lock(tmp_path):
        with pytest.raises(HomeInUseError):
            acquire_desktop_lock(tmp_path)
        with acquire_home_lock(tmp_path):
            with pytest.raises(HomeInUseError):
                acquire_home_lock(tmp_path)
    with acquire_desktop_lock(tmp_path), acquire_home_lock(tmp_path):
        pass


def test_runtime_owned_before_http_is_ready_is_reused(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    node = Daemon(tmp_path, port=0, protector=EnvironmentProtector(key))
    # Fix a free port before starting HTTP, while the legacy runtime owns home.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    node.port = port
    monkeypatch.setenv('A2N_HOME', str(tmp_path))
    monkeypatch.setenv('A2N_PORT', str(port))
    monkeypatch.setattr(desktop, 'network_environment', lambda *_: {})
    attempted = threading.Event()
    original = deployment.main

    def serve():
        attempted.set()
        return original()

    monkeypatch.setattr(deployment, 'main', serve)
    results = []
    errors = []

    def launch():
        try:
            results.append(desktop.main(['--home', str(tmp_path), '--port', str(port)]))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=launch, daemon=True)
    try:
        worker.start()
        assert attempted.wait(5)
        node.start()
        worker.join(5)
        assert not worker.is_alive() and not errors and results == [0]
        assert health(port, tmp_path)
        with pytest.raises(HomeInUseError):
            acquire_home_lock(tmp_path)
    finally:
        node.stop()
        worker.join(5)


@pytest.mark.parametrize('lock', [acquire_desktop_lock, acquire_home_lock])
def test_busy_home_without_a_ready_console_exits_cleanly(tmp_path, monkeypatch, lock):
    monkeypatch.setattr(desktop, 'STARTUP_TIMEOUT', 0)
    monkeypatch.setattr(desktop, 'health_snapshot', lambda *_: None)
    monkeypatch.setattr(desktop, 'network_environment', lambda *_: {})
    monkeypatch.setenv('A2N_HOME', str(tmp_path))
    with lock(tmp_path):
        assert desktop.main(['--home', str(tmp_path)]) == 2


def test_real_startup_errors_and_another_home_are_not_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'health_snapshot', lambda *_: {'instance_key': 'another home'})
    with pytest.raises(RuntimeError, match='different node home'):
        desktop.main(['--home', str(tmp_path)])
    monkeypatch.setattr(desktop, 'health_snapshot', lambda *_: None)
    monkeypatch.setattr(desktop, 'network_environment', lambda *_: {})

    def fail():
        raise RuntimeError('actual startup failure')

    monkeypatch.setattr(deployment, 'main', fail)
    with pytest.raises(RuntimeError, match='actual startup failure'):
        desktop.main(['--home', str(tmp_path)])
    with acquire_desktop_lock(tmp_path):
        pass


def test_upgrade_handover_releases_launcher_election(tmp_path, monkeypatch):
    ready = [True]
    expected = hashlib.sha256(os.path.normcase(str(tmp_path.resolve())).encode()).hexdigest()
    monkeypatch.setattr(desktop, 'health_snapshot', lambda *_: {'instance_key': expected} if ready[0] else None)
    monkeypatch.setattr(desktop.time, 'sleep', lambda *_: ready.__setitem__(0, False))
    handed_over = []

    def handover(home, port):
        with acquire_desktop_lock(home):
            handed_over.append((home, port))
        return 0

    monkeypatch.setattr(desktop, 'handover_installed_runtime', handover)
    assert desktop.main(['--home', str(tmp_path), '--watch-existing']) == 0
    assert handed_over == [(str(tmp_path), 8771)]


def test_multiple_processes_during_cold_start_share_one_node_and_identity(tmp_path):
    helper = tmp_path / 'launch.py'
    helper.write_text('''import os,sys,time,socket
from pathlib import Path
from a2n_node import desktop,deployment
home,port,claimed,delay=sys.argv[1:5]
original=deployment.Daemon
class DelayedDaemon(original):
    def start(self):
        Path(claimed).write_text('claimed')
        time.sleep(float(delay))
        return super().start()
deployment.Daemon=DelayedDaemon
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
    udp.bind(('127.0.0.1',0))
    p2p_port=udp.getsockname()[1]
desktop.network_environment=lambda home,port: {
    'A2N_HOME': str(home), 'A2N_PORT': str(port), 'A2N_P2P_PORT': str(p2p_port),
    'A2N_PUBLIC_BASE': '', 'A2N_PUBLIC_NODES': '', 'A2N_BOOTSTRAP': '',
    'A2N_COORD_MAILBOX_NODES': '', 'A2N_RELAY_NODE': '', 'A2N_COORD_ALLOW_NETWORKS': ''}
raise SystemExit(desktop.main(['--home',home,'--port',port,*sys.argv[5:]]))
''', encoding='utf-8')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    home = tmp_path / 'node'
    claimed = tmp_path / 'claimed'
    env = {**os.environ, 'PYTHONUTF8': '1',
           'A2N_STORAGE_KEY': base64.b64encode(os.urandom(32)).decode()}
    children = []
    streams = []

    def launch(delay=0, watch=False):
        stream = (tmp_path / f'launch-{len(children)}.log').open('w', encoding='utf-8')
        streams.append(stream)
        child = subprocess.Popen([sys.executable, '-u', str(helper), str(home), str(port),
            str(claimed), str(delay), *(['--watch-existing'] if watch else [])],
            stdout=stream, stderr=stream, env=env)
        children.append(child)
        return child

    def wait_until(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, 'Desktop startup did not finish'
            time.sleep(.05)

    def stop():
        if health(port, home):
            run = subprocess.run([sys.executable, '-m', 'a2n_node.product_cli', 'stop',
                '--home', str(home), '--port', str(port)], capture_output=True, env=env, timeout=15)
            assert run.returncode == 0, run.stderr

    def identity():
        run = subprocess.run([sys.executable, '-m', 'a2n_node.product_cli', 'request',
            '--home', str(home), '--port', str(port), '--path', '/v1/runtime'],
            capture_output=True, text=True, encoding='utf-8', env=env, timeout=15)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)['node_did']

    try:
        primary = launch(delay=2)
        wait_until(claimed.exists)
        duplicates = [launch(watch=watch) for watch in (False, True, True)]
        wait_until(lambda: health(port, home))
        first = identity()
        assert all(child.wait(timeout=10) == 0 for child in duplicates)
        assert primary.poll() is None
        stop()
        assert primary.wait(timeout=10) == 0
        restarted = launch()
        wait_until(lambda: health(port, home))
        assert identity() == first
        stop()
        assert restarted.wait(timeout=10) == 0
        logs = '\n'.join(path.read_text(encoding='utf-8') for path in tmp_path.glob('launch-*.log'))
        assert logs.count('READY did=') == 2
        assert 'HomeInUseError' not in logs and '这个节点目录已经有运行中的实例' not in logs
    finally:
        stop()
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
        for stream in streams:
            stream.close()
