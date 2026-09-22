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
from .acceptance_adapter import DeclaredAcceptance
from .p2p_service import P2PDiscoveryService
from .protection import system_protector


class Daemon:
    def __init__(self, home: str | Path, *, port=8771, protector=None,
                 platform=None, principal=None, origins=None,
                 p2p_port: int | None = None, bootstrap=None, beacon=True,
                 advertise_host="127.0.0.1", discovery_public_base=None):
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
        self.discovery = None
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
            self.runtime = NodeRuntime(self.identity.did,
                                       acceptance=DeclaredAcceptance(),
                                       signer=lambda card: sign_card(self.identity, card),
                                       card_verifier=self._verify_source)
            self.calls = CallService(self.runtime.invoke_local_id, self.store)
            self.pairing = PairingService(origins=origins)
            if platform:
                self.bridge = RuntimePlatformBridge(self.runtime, base_url=platform,
                                                    principal=principal, calls=self.calls)
            if p2p_port is not None:
                self.discovery = P2PDiscoveryService(
                    self.identity, port=p2p_port, bootstrap=bootstrap,
                    beacon=beacon, advertise_host=advertise_host)
            self.management = RuntimeManagement(self.runtime, self.store, calls=self.calls,
                                                publisher=self.bridge,
                                                discovery=self.discovery,
                                                discovery_public_base=discovery_public_base)
        except Exception:
            self.stop()
            raise

    @staticmethod
    def _verify_source(card):
        sovereign = ((card.get("x-a2n") or {}).get("sovereign") or {})
        # An entirely unsigned compatibility card may be imported, but a card
        # that claims any sovereign identity field must provide a complete,
        # valid proof.  Otherwise stripping only ``sig`` would downgrade a
        # forged card into the harmless-looking "unattested" class.
        if any(sovereign.get(key) for key in ("did", "pub", "sig")):
            ok, reason = verify_card(card)
            if not ok:
                raise ValueError(f"原始卡片验签失败：{reason}")

    def start(self):
        try:
            self.runtime.start_gateway(port=self.port, management=self.management,
                                       pairing=self.pairing, calls=self.calls,
                                       public_card_bases=(
                                           [self.management.discovery_public_base]
                                           if self.management.discovery_public_base else []))
            self.management.restore()
            self.management.network.start()
            if self.discovery:
                self.discovery.start()
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
            for sid, publication in self.store.items("publications").items():
                if self._stop.is_set():
                    return
                if publication.get("state") == "unpublishing":
                    try:
                        self.management.command("/v1/unpublish", {"service_id": sid})
                        if publication.get("remove_binding"):
                            self.management.command(
                                "/v1/bindings/remove", {"service_id": sid})
                    except Exception:
                        pass
                    continue
                if publication.get("state") == "pausing":
                    try:
                        self.bridge.unpublish(
                            sid, agent_id=publication.get("agent_id"))
                        self.store.put("publications", sid, {
                            "service_id": sid,
                            "agent_id": publication.get("agent_id"),
                            "state": "paused", "resume_on_enable": True})
                    except Exception:
                        pass
                    continue
                if publication.get("state") == "paused":
                    continue
                if sid in self.bridge.handles:
                    continue
                try:
                    self.management.command("/v1/publish", {"service_id": sid})
                except Exception:
                    pass  # Offline platform must not prevent local agent use.
            for sid in self.store.items("binding_removals"):
                if self._stop.is_set():
                    return
                if self.store.get("publications", sid):
                    continue
                try:
                    self.management.command("/v1/bindings/remove", {
                        "service_id": sid})
                except Exception:
                    pass
            self._stop.wait(15)

    def stop(self):
        self._stop.set()
        if self._publisher_thread:
            self._publisher_thread.join(timeout=35)
        if self.discovery:
            self.discovery.stop()
        if self.bridge:
            self.bridge.stop()
        if getattr(self, "management", None):
            self.management.network.stop()
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
