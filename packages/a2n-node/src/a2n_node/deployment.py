"""Generic public node lifecycle, shared by Docker and ordinary host deployment."""
from __future__ import annotations
import os
import signal
from pathlib import Path
from .daemon import Daemon
from .public_entry import env_listen_port, serve_public_entry
from .__main__ import _bootstrap_addresses

def main():
    base = os.environ.get("A2N_PUBLIC_BASE") or None
    port = int(os.environ.get("A2N_PORT", "8771"))
    daemon = Daemon(
        Path(os.environ.get("A2N_HOME", "/app/state/node")), port=port,
        p2p_port=int(os.environ.get("A2N_P2P_PORT", "9701")), beacon=False,
        bootstrap=_bootstrap_addresses(os.environ.get("A2N_BOOTSTRAP", "").split(",") if os.environ.get("A2N_BOOTSTRAP") else []),
        discovery_public_base=base,
        public_nodes=[v.strip() for v in os.environ.get("A2N_PUBLIC_NODES", "").split(",") if v.strip()],
        relay_node=os.environ.get("A2N_RELAY_NODE") or None,
        coord_mailbox_nodes=[v.strip() for v in os.environ.get("A2N_COORD_MAILBOX_NODES", "").split(",") if v.strip()],
        coord_allow_networks=[v.strip() for v in os.environ.get("A2N_COORD_ALLOW_NETWORKS", "").split(",") if v.strip()]).start()
    try:
        if base:
            serve_public_entry(port, base, listen_port=env_listen_port(base), tag="node")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: daemon.stop_requested.set())
        print(f"READY did={daemon.identity.did} local={daemon.runtime.local_base_url} public={base or ''}", flush=True)
        daemon.stop_requested.wait()
    finally:
        daemon.stop()
