"""Composition root for the user's persistent local node.

SDK services have no dependency on this module. This is the only place that
wires identity/signing, encrypted storage, management and the platform adapter.
"""
from __future__ import annotations

import base64
import copy
import os
from pathlib import Path
import secrets
import threading

from a2n_p2p import Identity
from a2n_sdk.calls import CallService
from a2n_sdk.feedback import FeedbackBook
from a2n_sdk.adapters import DirectA2ATransport, FallbackTransport
from a2n_sdk.ports import CallRequest
from a2n_sdk.management import RuntimeManagement, parse_seed
from a2n_sdk.pairing import PairingService
from a2n_sdk.platform_runtime import RuntimePlatformBridge
from a2n_sdk.runtime import NodeRuntime
from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook
from a2n_sdk.upstream import a2a_message

from .card import card_did, sign_card, verify_card
from .acceptance_adapter import DeclaredAcceptance
from .feedback_identity import absorb_feedback, offer_feedback, signer_for, verifier_for
from .p2p_service import P2PDiscoveryService
from . import peer as peer_protocol
from .peer_exchange import PeerExchange, signed_a2a_input
from .peer_transport import PROTOCOL, SignedA2ATransport
from .relay_crypto import private_from_seed
from .relay_provider import RelayProvider
from .relay_service import PublicRelay
from .relay_transport import RelayA2ATransport
from .public_directory import PublicDirectoryClient
from .protection import system_protector
from . import receipt as receipt_proof
from .witness import PublicWitness


class Daemon:
    def __init__(self, home: str | Path, *, port=8771, protector=None,
                 platform=None, principal=None, origins=None,
                 p2p_port: int | None = None, bootstrap=None, beacon=True,
                 advertise_host="127.0.0.1", discovery_public_base=None,
                 public_nodes=None, relay_node=None):
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
        self.trials = None
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
            relay_seed = self.store.get("identity", "relay_seed")
            if not relay_seed:
                relay_seed = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
                self.store.put("identity", "relay_seed", relay_seed)
            relay_private = private_from_seed(base64.b64decode(relay_seed))
            # 双方反馈账本（R2）：签名/验签用本节点 ed25519 身份注入 —— 一个
            # did = 一个节点，反馈由节点身份自动签名，对方不能伪造或改写。
            # 先建它，是因为签名/中继两条传输都要带它做「随终结消息捎带」（FEEDBACK-API §4）。
            self.feedback = FeedbackBook(
                self.store, signer=signer_for(self.identity), verifier=verifier_for())
            self.runtime = NodeRuntime(self.identity.did,
                                       transport=FallbackTransport([
                                           ("sealed-relay", RelayA2ATransport(
                                               self.identity, feedback=self.feedback)),
                                           ("signed-a2a", SignedA2ATransport(
                                               self.identity, feedback=self.feedback)),
                                           ("direct", DirectA2ATransport())]),
                                       acceptance=DeclaredAcceptance(),
                                       signer=self._sign_projection_card,
                                       card_verifier=self._verify_source)
            self.peer_exchange = PeerExchange(self.identity, self.runtime, self.store,
                                              feedback=self.feedback)
            self.witness = PublicWitness(self.identity, self.store)
            # 控制台「连接节点」写进 node_settings 的种子/目录源必须**在启动时生效**：
            # CLI 的 --bootstrap / --public-node 只是"这一次额外加的"，不是唯一来源。
            # 否则"控制台连了一个节点、重启后它悄悄消失"——那是最难查的一类不一致。
            self.public_directories = PublicDirectoryClient(
                [*(public_nodes or []), *([relay_node] if relay_node else []),
                 *self._saved_public_nodes()])
            self.relay_service = PublicRelay(
                lambda: self.management.discovery_public_base
                if getattr(self, "management", None) else "")
            self.relay_provider = (RelayProvider(
                self.identity, self.runtime, relay_private, relay_node)
                if relay_node else None)
            # 试用期 + 样品账本（§1.2 #12）：与交付完成钩子、管理面共用同一个实例。
            self.trials = TrialBook(self.store)
            # 对外卡片上要能**调用前**看到"前 N 次免费、交付默认成公开样品"。
            self.runtime.trial_provider = self._trial_card_block
            self.calls = CallService(
                self.runtime.invoke_local_id, self.store,
                refresh_remote=self.runtime.refresh_remote_task,
                cancel_remote=self.runtime.cancel_remote_task,
                finalize_outcome=self.peer_exchange.finalize,
                on_delivery=self._record_delivery)
            self.pairing = PairingService(origins=origins)
            if platform:
                self.bridge = RuntimePlatformBridge(self.runtime, base_url=platform,
                                                    principal=principal, calls=self.calls)
            if p2p_port is not None:
                self.discovery = P2PDiscoveryService(
                    self.identity, port=p2p_port,
                    bootstrap=self._merge_seeds(bootstrap),
                    beacon=beacon, advertise_host=advertise_host)
            self.management = RuntimeManagement(self.runtime, self.store, calls=self.calls,
                                                publisher=self.bridge,
                                                discovery=self.discovery,
                                                discovery_public_base=discovery_public_base,
                                                receipt_auditor=self._audit_receipt,
                                                witness_service=self.witness,
                                                public_directories=self.public_directories,
                                                relay_service=self.relay_service,
                                                relay_provider=self.relay_provider,
                                                trials=self.trials,
                                                feedback=self.feedback,
                                                feedback_deliver=self._deliver_feedback)
        except Exception:
            self.stop()
            raise

    # ---------------- 控制台「连接节点」落库的设置在启动时生效 ----------------

    def _saved_seeds(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for item in self.store.get("node_settings", "bootstrap_peers", []) or []:
            try:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    out.append((str(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
        return out

    def _saved_public_nodes(self) -> list[str]:
        raw = self.store.get("node_settings", "public_nodes", []) or []
        return [str(x) for x in raw if str(x or "").strip()]

    def _merge_seeds(self, cli_seeds) -> list[tuple[str, int]]:
        """CLI 种子 + 控制台保存的种子，去重且保序（先 CLI 后保存）。

        解析失败的一条种子不该让整个节点起不来：跳过并继续，
        但它也不会被静默当成"配好了"。
        """
        merged: list[tuple[str, int]] = []
        for item in list(cli_seeds or ()) + [
                (h, p) for h, p in self._saved_seeds()]:
            try:
                host, port = parse_seed(f"{item[0]}:{item[1]}")
            except (ValueError, TypeError, IndexError):
                continue
            if (host, port) not in merged:
                merged.append((host, port))
        return merged

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

    def _sign_projection_card(self, card):
        projected = copy.deepcopy(card)
        ext = projected.setdefault("x-a2n", {})
        if (ext.get("projection") or {}).get("role") == "supply":
            ext["peer_protocol"] = PROTOCOL
        else:
            ext.pop("peer_protocol", None)
        return sign_card(self.identity, projected)

    def _trial_card_block(self, service_id: str) -> dict:
        """给对外卡片用的**静态**试用/样品声明。

        只放"调用前就该知道、且不随每次调用抖动"的事实：名额与规则文案、以及
        是否已结束首批。**不**放 remaining/used —— 那会让签名卡片每调一次就变一次。
        动态进度在目录/控制台的试用视图里看。
        """
        st = self.trials.status(service_id)
        return {"cap": st["cap"], "ended": bool(st["ended"]),
                "notice": st["notice"],
                "policy": "first_n_free_calls_become_public_samples"}

    def _record_delivery(self, scope: str, request, outcome) -> None:
        """一次交付完成后的旁路记账：试用计数 + 样品（§1.2 #12）。

        只记**本节点自己供给**的交付（scope 命中本机挂载）。别人家的供给经本机转发
        进来的（导入投影）不算 —— 那是买方视角，卖方的节点会在他自己那边记。
        """
        binding = self.runtime.bindings.get(scope)
        if binding is None:
            return
        version = ""
        card = binding.source_card or {}
        if isinstance(card, dict):
            version = str(card.get("version") or (card.get("x-a2n") or {}).get("version") or "")
        self.trials.record(scope, request.task_id, outcome,
                           request=request, version=version)

    def _audit_receipt(self, scope: str, task_id: str) -> str:
        """Distinguish a signed delivery from a *confirmed* bilateral receipt."""
        row = self.store.task(scope, task_id)
        if not row:
            return "receipt_unverified"
        outcome = row.get("outcome") or {}
        receipt = outcome.get("receipt") or {}
        ok, _reason = receipt_proof.verify(receipt)
        if not ok or receipt.get("task_id") != task_id:
            return "receipt_unverified"
        imported = self.runtime.imported.get(scope)
        expected_provider = (card_did(imported.network_card) if imported else
                             self.identity.did if self.runtime.bindings.get(scope) else None)
        if not expected_provider or receipt.get("provider_did") != expected_provider \
                or receipt.get("by") != expected_provider:
            return "receipt_unverified"
        request = row.get("request") or {}
        request_meta = request.get("metadata") or {}
        if (request_meta.get("_a2n_verified_peer")
                or (outcome.get("metadata") or {}).get("transport_route") == "signed-a2a"):
            original = CallRequest(**request)
            input_value = signed_a2a_input(
                a2a_message(original), original.context_id, request_meta)
        else:
            input_value = request.get("payload")
        if receipt.get("input_hash") != receipt_proof.hash_payload(input_value) \
                or receipt.get("output_hash") != receipt_proof.hash_payload(outcome.get("result")):
            return "receipt_unverified"
        metadata = outcome.get("metadata") or {}
        acknowledgement = metadata.get("bilateral_ack")
        # A locally signed but unsent acknowledgement does not prove that the
        # other side holds it. Require explicit confirmed delivery/receipt.
        if (metadata.get("bilateral_ack_confirmed") is True and acknowledgement
                and receipt_proof.verify_ack(acknowledgement, receipt)[0]):
            return "receipt_bilateral_verified"
        return "receipt_provider_signed"

    def _deliver_feedback(self, scope: str, task_id: str) -> dict:
        """R2 补交（FEEDBACK-API §4「补交走同一捎带路径」）：把本机**买方**反馈
        随这笔任务的**收据回执**再捎一次给供给方，并收下供给方随响应捎回的反馈。

        为什么是重发回执：反馈通常写在调用之后，终结消息早已发过；R2 不做专门重传
        协议，就复用**同一份回执**当载体（供给方 `acknowledge` 幂等）。没有回执/没写
        反馈/对方不在线，都**如实回 `delivered=False` 并说明原因**，绝不假装送达。
        """
        row = self.store.task(scope, task_id)
        meta = ((row or {}).get("outcome") or {}).get("metadata") or {}
        ack = meta.get("bilateral_ack")
        base = meta.get("peer_base")
        sid = meta.get("peer_service_id")
        if not (ack and base and sid):
            return {"delivered": False, "reason": "本机没有这笔任务的回执，无法补交"}
        carried = offer_feedback(self.feedback, task_id=task_id, direction="buyer_to_seller")
        if not carried:
            return {"delivered": False, "reason": "本机还没有这笔的买方反馈，无可补交"}
        resp = peer_protocol.send_ack_result(
            str(base), ack, service_id=str(sid),
            witness_claim=meta.get("witness_claim"), feedback=carried, timeout=10.0)
        if not resp or not resp.get("ok"):
            return {"delivered": False, "reason": "对方没确认收到（可能不在线）"}
        got = absorb_feedback(self.feedback, resp)
        return {"delivered": True, "received": bool(got),
                "ref": carried.get("feedback_id")}

    def start(self):
        try:
            self.runtime.start_gateway(port=self.port, management=self.management,
                                       pairing=self.pairing, calls=self.calls,
                                       peer_exchange=self.peer_exchange,
                                       public_card_bases=(
                                           [self.management.discovery_public_base]
                                           if self.management.discovery_public_base else []))
            self.management.restore()
            self.management.network.start()
            if self.relay_provider:
                self.relay_provider.start()
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
        if getattr(self, "relay_provider", None):
            self.relay_provider.stop()
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
