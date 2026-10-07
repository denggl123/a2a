"""Persisted desktop network settings; use the same full node composition."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

from .product_cli import default_home, health_snapshot, runtime_command
from .home import HomeInUseError, acquire_desktop_lock

STARTUP_TIMEOUT = 30
RETRY_INTERVAL = .25
_RECHECK_INSTALLATION = object()


def handover_installed_runtime(home, port):
    """A waiting supervisor must reread installation after an upgrade stop."""
    path = Path(home) / "installation.json"
    if not path.exists():
        return None
    installation = json.loads(path.read_text(encoding="utf-8-sig"))
    if installation.get("runtime_bundled") is not True:
        return None
    binary = Path(installation["executable"]).resolve()
    from .upgrades import file_digest
    if not binary.is_file() or binary.name.lower() != "a2n.exe" or file_digest(binary) != installation.get("sha256"):
        raise ValueError("INSTALLED_RUNTIME_CHANGED")
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve() == binary:
        return None
    return subprocess.call(runtime_command("a2n_node.desktop", ["--home", str(home), "--port", str(port), "--watch-existing"],
        executable=str(binary)), env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def network_environment(home, port=8771):
    home = Path(home).expanduser().resolve()
    settings_path = home / 'network.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    env = {'A2N_HOME': str(home), 'A2N_PORT': str(port), 'A2N_P2P_PORT': '9701',
           'A2N_PUBLIC_BASE': '', 'A2N_PUBLIC_PORT': '18885', 'A2N_PUBLIC_NODES': '',
           'A2N_COORD_MAILBOX_NODES': '', 'A2N_RELAY_NODE': '', 'A2N_COORD_ALLOW_NETWORKS': ''}
    if not settings:
        return env
    host = ipaddress.IPv4Address(settings['host_address'])
    if host.is_unspecified or host.is_multicast or host.is_loopback:
        raise ValueError('Desktop network host must be a LAN IPv4 address')
    server = settings.get('server_base') or ''
    allow = ['172.30.47.0/24', f'{host}/32']
    if server:
        parsed = urlsplit(server)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
            raise ValueError('ServerBase must be an HTTP(S) origin')
        _ = parsed.port
        server = f'{parsed.scheme}://{parsed.netloc}'
        if parsed.scheme == 'http':
            allow.append(f'{ipaddress.IPv4Address(parsed.hostname)}/32')
    env.update(A2N_PUBLIC_BASE=f'http://{host}:18885',
               A2N_PUBLIC_NODES=','.join(v for v in (f'http://{host}:18881', server) if v),
               A2N_COORD_MAILBOX_NODES=server, A2N_RELAY_NODE=server,
               A2N_COORD_ALLOW_NETWORKS=','.join(allow))
    return env


def _existing_node(home, port):
    state = health_snapshot(port)
    if state is None:
        return False
    expected = hashlib.sha256(os.path.normcase(str(Path(home).expanduser().resolve())).encode()).hexdigest()
    if state.get('instance_key') != expected:
        raise RuntimeError('Desktop port belongs to a different node home')
    return True


def _serve_or_watch(home, port, watch_existing):
    from .deployment import main as serve
    watching = False
    waiting = False
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while True:
        if _existing_node(home, port):
            if not watch_existing:
                print('Desktop SDK is already running', flush=True)
                return 0
            if not watching:
                print('Watching the existing desktop SDK; its identity and data are reused', flush=True)
            watching = True
            time.sleep(1)
            continue
        if watching:
            # Release the launcher election before invoking another installed
            # executable. The replacement must be able to win that election.
            return _RECHECK_INSTALLATION
        os.environ.update(network_environment(home, port))
        try:
            # Daemon atomically acquires runtime.lock and retains it throughout
            # its lifetime. Never acquire/release it as a readiness check.
            serve()
            return 0
        except HomeInUseError:
            if not waiting:
                print('Waiting for the existing node to finish starting or stopping', flush=True)
                waiting = True
            if time.monotonic() >= deadline:
                print('Node home is still in use and its console is unavailable; see node.log', flush=True)
                return 2
            time.sleep(RETRY_INTERVAL)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Start the canonical desktop SDK node')
    parser.add_argument('--home', default=str(default_home()))
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--watch-existing', action='store_true')
    args = parser.parse_args(argv)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while True:
        if _existing_node(args.home, args.port) and not args.watch_existing:
            print('Desktop SDK is already running', flush=True)
            return 0
        try:
            launcher = acquire_desktop_lock(args.home)
        except HomeInUseError:
            if _existing_node(args.home, args.port):
                print('Desktop SDK is already running; its launcher is reused', flush=True)
                return 0
            if time.monotonic() >= deadline:
                print('Desktop launcher is active but its console is unavailable; see node.log', flush=True)
                return 2
            time.sleep(RETRY_INTERVAL)
            continue
        with launcher:
            result = _serve_or_watch(args.home, args.port, args.watch_existing)
        if result is not _RECHECK_INSTALLATION:
            return result
        handed_over = handover_installed_runtime(args.home, args.port)
        if handed_over is not None:
            return handed_over
        deadline = time.monotonic() + STARTUP_TIMEOUT


if __name__ == '__main__':
    raise SystemExit(main())
