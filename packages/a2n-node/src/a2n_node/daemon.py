"""Composition root for the user's persistent local node.

SDK services have no dependency on this module. This is the only place that
wires identity/signing, encrypted storage, management and the platform adapter.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets
import threading

from a2n_p2p import Identity
from a2n_sdk.calls import CallService
from a2n_sdk.management import RuntimeManagement
from a2n_sdk.pairing import PairingService
from a2n_sdk.platform_runtime import RuntimePlatformBridge
from a2n_sdk.runtime import NodeRuntime
from a2n_sdk.storage import LocalStore

from .card import sign_card, verify_card
from .protection import system_protector


class Daemon:
    def __init__(self, home: str | Path, *, port=8771, protector=None,
                 platform=None, principal=None, origins=None):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock_file = (self.home / "runtime.lock").open("a+b")
        self._lock_file.seek(0)
        self._lock_file.write(b"0")
        self._lock_file.flush()
        self._lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("这个节点目录已经有运行中的实例") from None
        self.store = None
        self.runtime = None
        self.calls = None
        self.bridge = None
        self._stop = threading.Event()
        self._publisher_thread = None
        self.port = port
        try:
            self.store = LocalStore(self.home / "runtime.db", protector or system_protector())
            seed = self.store.get("identity", "seed")
            if not seed:
                seed = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
                self.store.put("identity", "seed", seed)
            self.identity = Identity.from_private_bytes(base64.b64decode(seed))
            self.runtime = NodeRuntime(self.identity.did, signer=lambda card: sign_card(self.identity, card),
                                       card_verifier=self._verify_source)
            self.calls = CallService(self.runtime.invoke_local_id, self.store)
            self.pairing = PairingService(origins=origins)
            if platform:
                self.bridge = RuntimePlatformBridge(self.runtime, base_url=platform,
                                                    principal=principal, calls=self.calls)
            self.management = RuntimeManagement(self.runtime, self.store, calls=self.calls,
                                                publisher=self.bridge)
        except Exception:
            self.stop()
            raise

    @staticmethod
    def _verify_source(card):
        if ((card.get("x-a2n") or {}).get("sovereign") or {}).get("sig"):
            ok, reason = verify_card(card)
            if not ok:
                raise ValueError(f"原始卡片验签失败：{reason}")

    def start(self):
        try:
            self.runtime.start_gateway(port=self.port, management=self.management,
                                       pairing=self.pairing, calls=self.calls)
            self.management.restore()
            if self.bridge:
                self._publisher_thread = threading.Thread(target=self._restore_publications,
                                                           daemon=True, name="a2n-republish")
                self._publisher_thread.start()
            return self
        except Exception:
            self.stop()
            raise

    def _restore_publications(self):
        while not self._stop.is_set():
            for sid in self.store.items("publications"):
                if self._stop.is_set():
                    return
                if sid in self.bridge.handles:
                    continue
                try:
                    self.management.command("/v1/publish", {"service_id": sid})
                except Exception:
                    pass  # Offline platform must not prevent local agent use.
            self._stop.wait(15)

    def stop(self):
        self._stop.set()
        if self._publisher_thread:
            self._publisher_thread.join(timeout=35)
        if self.bridge:
            self.bridge.stop()
        if self.runtime:
            self.runtime.stop()
        if self.calls:
            self.calls.stop()
            self.calls = None
        if self.store:
            self.store.close()
            self.store = None
        if not self._lock_file.closed:
            self._lock_file.close()
