"""Composition root for the user's persistent local node.

SDK services have no dependency on this module. This is the only place that
wires identity, encrypted storage, the node marketplace and network adapters.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time

from a2n_p2p import Identity
from a2n_sdk.calls import CallService
from a2n_sdk.feedback import FeedbackBook
from a2n_sdk.adapters import DirectA2ATransport, FallbackTransport
from a2n_sdk.ports import CallRequest
from a2n_sdk.management import RuntimeManagement, parse_seed
from a2n_sdk.pairing import PairingService
from a2n_sdk.runtime import NodeRuntime
from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook
from a2n_sdk.reconnect import ReconnectBook
from a2n_sdk.contracts import ContractBook, input_digest
from a2n_sdk.reputation import ReputationBook
from a2n_sdk.policies import PolicyBook, BusinessCandidatePolicy
from a2n_sdk.payments import RiskBook, PaymentBook
from a2n_sdk.resolutions import ResolutionBook
from a2n_sdk.trade_facts import TradeFactsBook, CONTEXT_NS
from a2n_sdk.experience import ExperienceBook, AUTH_VERSION, ANCHOR_VERSION, signed, unsigned
from a2n_sdk.coordination_service import CoordinationService
from a2n_sdk.experience_service import ExperienceService
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
from .coord_service import PublicCoordination
from .coord_network import CoordinationNetwork
from .coord_mailbox import CoordinationMailboxClient
from .coord_neighbors import CoordinationNeighbors
from .experience_gateway import PublicExperience, ExperienceNetwork
from .metadata_mailbox import MetadataMailboxClient, PublicMetadataMailbox, verify_lease, PREFIX as METADATA_PREFIX
from .backups import BackupService
from .home import acquire_home_lock
from .resolution_gateway import PublicResolution, ResolutionDelivery
from .package_runtime import PackageService
from .asset_service import AssetService
from .asset_mailbox import AssetMailboxClient, PublicAssetMailbox
from .rework_service import ReworkService
from .upgrades import UpgradeService


class Daemon:
    def __init__(self, home: str | Path, *, port=8771, protector=None,
                 origins=None,
                 p2p_port: int | None = None, bootstrap=None, beacon=True,
                 advertise_host="127.0.0.1", discovery_public_base=None,
                 public_nodes=None, relay_node=None, coord_allow_networks=(),
                 coord_mailbox_nodes=None, payment_driver=None, x402_config=None, x402_signer=None):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock_file = acquire_home_lock(self.home)
        self.store = None
        self.runtime = None
        self.calls = None
        self.trials = None
        self.discovery = None
        self._stop = threading.Event()
        self.stop_requested = threading.Event()
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
            self.experience = ExperienceBook(self.store, self.feedback,
                signer=signer_for(self.identity), verifier=verifier_for())
            self.reputation = ReputationBook(self.store, self.experience)
            self.policies = PolicyBook(self.store)
            self.risk = RiskBook(self.store)
            from .x402.service import X402NodeService, environment_config
            saved_payment = self.store.get("payment_settings", "config")
            if saved_payment is not None:
                x402_config = saved_payment.get("x402")
                wallet_key = self.store.get("payment_wallet", "key")
                if wallet_key:
                    from .x402.evm import EvmSigner
                    x402_signer = EvmSigner(wallet_key)
            if x402_config is None and saved_payment is None:
                x402_config, environment_signer = environment_config()
                x402_signer = x402_signer or environment_signer
            self.x402 = X402NodeService(self.store, x402_config, x402_signer)
            if payment_driver is not None and self.x402.driver and x402_signer:
                raise ValueError("PAYMENT_DRIVER_CONFIG_CONFLICT")
            selected_driver = payment_driver or (self.x402.driver if x402_signer else None)
            self.payments = PaymentBook(self.store, self.risk, selected_driver)
            self.x402.bind(self.payments)
            self.runtime = NodeRuntime(self.identity.did,
                                       transport=FallbackTransport([
                                           ("sealed-relay", RelayA2ATransport(
                                               self.identity, feedback=self.feedback,
                                               anchor_validator=self.experience.validate_anchor)),
                                           ("signed-a2a", SignedA2ATransport(
                                               self.identity, feedback=self.feedback,
                                               anchor_validator=self.experience.validate_anchor)),
                                           ("direct", DirectA2ATransport())]),
                                       acceptance=DeclaredAcceptance(),
                                       signer=self._sign_projection_card,
                                       card_verifier=self._verify_source)
            self.runtime.instance_key = hashlib.sha256(os.path.normcase(str(self.home)).encode()).hexdigest()
            self.store.put("runtime_control", "access", {"token": self.runtime.management_token, "port": port})
            self.peer_exchange = PeerExchange(self.identity, self.runtime, self.store,
                                              feedback=self.feedback)
            self.witness = PublicWitness(self.identity, self.store)
            # 控制台「连接节点」写进 node_settings 的种子/目录源必须**在启动时生效**：
            # CLI 的 --bootstrap / --public-node 只是"这一次额外加的"，不是唯一来源。
            # 否则"控制台连了一个节点、重启后它悄悄消失"——那是最难查的一类不一致。
            self.public_directories = PublicDirectoryClient(
                [*(public_nodes or []), *(coord_mailbox_nodes or []), *([relay_node] if relay_node else []),
                 *self._saved_public_nodes()], allowed_networks=coord_allow_networks)
            self.relay_service = PublicRelay(
                lambda: self.management.discovery_public_base
                if getattr(self, "management", None) else "")
            self.relay_provider = (RelayProvider(
                self.identity, self.runtime, relay_private, relay_node)
                if relay_node else None)
            # 试用期 + 样品账本（§1.2 #12）：与交付完成钩子、管理面共用同一个实例。
            self.trials = TrialBook(self.store)
            self.reconnect = ReconnectBook(self.store, self.trials, node_did=self.identity.did)
            self.trade_facts = TradeFactsBook(self.store)
            self.contracts = ContractBook(self.store, node_did=self.identity.did,
                signer=signer_for(self.identity), verifier=verifier_for())
            # 对外卡片上要能**调用前**看到"前 N 次免费、交付默认成公开样品"。
            self.runtime.trial_provider = self._trial_card_block
            self.runtime.experience_provider = self._experience_capabilities
            self.calls = CallService(
                self._invoke_with_contract, self.store,
                refresh_remote=self.runtime.refresh_remote_task,
                cancel_remote=self.runtime.cancel_remote_task,
                finalize_outcome=self.peer_exchange.finalize,
                on_admit=self._admit_trade, on_delivery=self._record_delivery,
                durable_delivery=True, prepare_request=self._prepare_trade_request)
            self.pairing = PairingService(origins=origins)
            if p2p_port is not None:
                self.discovery = P2PDiscoveryService(
                    self.identity, port=p2p_port,
                    bootstrap=self._merge_seeds(bootstrap),
                    beacon=beacon, advertise_host=advertise_host)
            self.management = RuntimeManagement(self.runtime, self.store, calls=self.calls,
                                                discovery=self.discovery,
                                                discovery_public_base=discovery_public_base,
                                                receipt_auditor=self._audit_receipt,
                                                witness_service=self.witness,
                                                public_directories=self.public_directories,
                                                relay_service=self.relay_service,
                                                relay_provider=self.relay_provider,
                                                trials=self.trials,
                                                feedback=self.feedback,
                                                feedback_deliver=self._deliver_feedback,
                                                shutdown=self.stop_requested.set)
            self.management.trade_facts = self.trade_facts
            from .product_cli import runtime_info
            self.management.product_runtime = runtime_info()
            self.management.experience = self.experience
            self.management.reputation = self.reputation
            self.management.policies = self.policies
            self.management.risk = self.risk
            self.management.payments = self.payments
            self.management.x402 = self.x402
            from .payment_service import PaymentService
            self.payment_coordination = PaymentService(self)
            self.management.payment_coordination = self.payment_coordination
            self.runtime.payment_provider = lambda: {"coordination_version":"a2n-payment-plan/1",
                "methods":self.payment_coordination.methods(receiving=True),"platform_commission_minor":0,
                "automatic_payment":False}
            self.management.reconnect = self.reconnect
            self.packages = PackageService(self.store, self.runtime)
            self.management.packages = self.packages
            self.resolutions = ResolutionBook(self.store, self.management.disputes, self.trade_facts,
                node_did=self.identity.did, signer=signer_for(self.identity), verifier=verifier_for())
            self.management.resolutions = self.resolutions
            self.rework = ReworkService(self)
            self.management.rework = self.rework
            self.backups = BackupService(self.home, self.store, self.identity)
            self.management.backups = self.backups
            self.upgrades = UpgradeService(self.home, self.store, self.backups)
            self.management.upgrades = self.upgrades
            self.public_coordination = PublicCoordination(
                self.identity, self.store,
                endpoint=lambda: self.management.discovery_public_base or
                                 (self.runtime.local_base_url if self.runtime.gateway else ""),
                cards=self._coord_cards, services=lambda: dict(self.management.public_services))
            self.coord_network = CoordinationNetwork(
                self.identity, self.public_coordination,
                roots=lambda: self.public_directories.bases,
                legacy=self._legacy_discovery if self.discovery else None,
                legacy_cost=2,
                legacy_available=lambda: bool(self.discovery and self.discovery.p2p.table.alive()),
                allowed_networks=coord_allow_networks,
                on_connection=lambda did: self.reconnect.note_verified_connection(did,
                    [b.service_id for b in self.runtime.bindings.list() if b.enabled]))
            self.public_coordination.network = self.coord_network
            self.assets = AssetService(self.store, self.identity, self.trade_facts, self.coord_network)
            self.management.assets = self.assets
            self.public_resolution = PublicResolution(self.identity, self.resolutions)
            self.management.public_resolution = self.public_resolution
            self.resolution_delivery = ResolutionDelivery(self.identity, self.coord_network, self.resolutions)
            self.public_experience = PublicExperience(self.identity, self.experience, self.management)
            self.management.public_experience = self.public_experience
            self.public_metadata_mailbox = PublicMetadataMailbox(self.identity,
                endpoint=lambda: self.management.discovery_public_base or self.runtime.local_base_url)
            self.management.public_metadata_mailbox = self.public_metadata_mailbox
            self.experience_service = ExperienceService(self.store, self.experience,
                ExperienceNetwork(self.identity, self.coord_network), sources=self._experience_sources)
            self.management.experience_service = self.experience_service
            self.coordination = CoordinationService(self.store, self.coord_network,
                policy_factory=lambda spec: BusinessCandidatePolicy(spec, self.reputation, self.policies))
            self.management.coordination = self.coordination
            self.management.public_coordination = self.public_coordination
            self.coord_mailbox = CoordinationMailboxClient(
                self.coord_network, self.public_coordination,
                roots=lambda: tuple(coord_mailbox_nodes) if coord_mailbox_nodes else
                (self.public_directories.bases if not self.management.discovery_public_base else ()))
            self.metadata_mailbox = MetadataMailboxClient(self.identity, self.coord_network, self.public_experience,
                resolution=self.public_resolution,
                roots=lambda: tuple(coord_mailbox_nodes) if coord_mailbox_nodes else
                (self.public_directories.bases if not self.management.discovery_public_base else ()))
            self.management.metadata_mailbox = self.metadata_mailbox
            self.public_asset_mailbox = PublicAssetMailbox(self.identity,
                endpoint=lambda: self.management.discovery_public_base or self.runtime.local_base_url,
                enabled=lambda: self.management.public_services.get("blob_cache", False))
            self.management.public_asset_mailbox = self.public_asset_mailbox
            self.asset_mailbox = AssetMailboxClient(self.identity, self.coord_network, self.assets,
                roots=lambda: tuple(coord_mailbox_nodes) if coord_mailbox_nodes else
                (self.public_directories.bases if not self.management.discovery_public_base else ()))
            self.management.asset_mailbox = self.asset_mailbox
            self.coord_neighbors = CoordinationNeighbors(
                self.coord_network, roots=lambda: self.public_directories.bases)
        except Exception:
            self.stop()
            raise

    # ---------------- 控制台「连接节点」落库的设置在启动时生效 ----------------

    def _coord_cards(self):
        base = self.management.discovery_public_base or self.runtime.local_base_url
        cards = [self.runtime.project_binding(b.service_id, public_base=base)
                 for b in self.runtime.bindings.list() if b.enabled and b.metadata.get("listed", True)]
        if self.relay_provider:
            cards.extend(self.relay_provider._cards())
        if self.relay_service and self.management.public_services.get("task_relay"):
            cards.extend(self.relay_service.coordination_cards())
        return cards

    def _experience_sources(self, subject):
        sources = []
        for item in self.runtime.imported.values():
            card = item.network_card
            if card_did(card) != subject.get("provider_did") or not verify_card(card)[0]:
                continue
            declaration = (card.get("x-a2n") or {}).get("experience") or {}
            route = declaration.get("endpoint")
            if route:
                source = {"node_did": card_did(card), "endpoint": route}
                lease = declaration.get("mailbox_lease")
                if verify_lease(lease) and lease["author_did"] == card_did(card):
                    source.update(endpoint=lease["endpoint"], mailbox={"endpoint": lease["endpoint"], "relay_did": lease["relay_did"]})
                sources.append(source)
        sources.extend({"endpoint": base} for base in self.public_directories.bases)
        for record in self.public_coordination.known_records():
            for route in record["coord_routes"][:1]:
                endpoint = route["endpoint"]
                if endpoint.endswith("/public/v1/coord"):
                    sources.append({"node_did": record["node_did"],
                                    "endpoint": endpoint.removesuffix("/public/v1/coord")})
                elif route.get("channel_type") == "coord_mailbox" and endpoint.endswith("/public/v1/coord/mailbox"):
                    relay_endpoint = endpoint.removesuffix("/public/v1/coord/mailbox") + METADATA_PREFIX.rstrip("/")
                    sources.append({"node_did": record["node_did"], "endpoint": relay_endpoint,
                        "mailbox": {"endpoint": relay_endpoint, "relay_did": route["relay_did"]}})
        return sources

    def _experience_capabilities(self, base):
        declaration = {"protocol": "a2n-experience/1", "endpoint": base}
        lease = getattr(getattr(self, "metadata_mailbox", None), "lease", None)
        if verify_lease(lease):
            declaration["mailbox_lease"] = lease
        return declaration

    def _legacy_discovery(self, skill, timeout, byte_cap, cursor=""):
        cards = []
        index = int(cursor or 0)
        peers = self.discovery.p2p.table.alive()[:32] if self.discovery else []
        use_p2p = index < len(peers)
        if use_p2p:
            cards.extend({"card": c, "source": {"kind": "p2p", "node_did": card_did(c), "endpoint": "udp"}}
                         for c in self.discovery.discover(skill, timeout=timeout,
                                                          coordination_budget=byte_cap,
                                                          coordination_peer=peers[index].did))
        return {"entries": cards, "next_cursor": str(index + 1) if index + 1 < len(peers) else ""}

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
        from a2n_sdk.cards import validate_card
        validate_card(card)
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
        return {"cap": st["cap"], "ended": bool(st["ended"]), "reconnect": st.get("reconnect"),
                "notice": st["notice"],
                "policy": "first_n_free_calls_become_public_samples"}

    def _record_delivery(self, scope: str, request, outcome) -> None:
        """一次交付完成后的旁路记账：试用计数 + 样品（§1.2 #12）。

        只记**本节点自己供给**的交付（scope 命中本机挂载）。别人家的供给经本机转发
        进来的（导入投影）不算 —— 那是买方视角，卖方的节点会在他自己那边记。
        """
        binding = self.runtime.bindings.get(scope)
        if binding:
            outcome.metadata.pop("sample_media", None)
            # 卖方是否声明过"可安全公开"，只作为**审计事实**记进交付元数据
            # （样品本身一律公开，用户裁决 2026-10-06）；不再作为是否公开的门禁。
            declared = ((binding.source_card.get("x-a2n") or {}).get("public_sample_policy") or {})
            declared = declared if isinstance(declared, dict) else {}
            outcome.metadata["sample_policy"] = {"safe_output": declared.get("safe_output") is True}
        facts = self.trade_facts.observe(scope, request, outcome)
        rework = request.metadata.get("a2nReworkAuthorization")
        if isinstance(rework, dict):
            self.resolutions.note_rework(rework["proposal_id"], facts)
        if facts.get("role") != "provider":
            contract = outcome.metadata.get("admission_contract")
            if contract and facts.get("role") == "buyer":
                try:
                    self.contracts.verify_delivery_contract(contract, request, provider_did=facts["provider_did"],
                        service_id=facts["service_id"], trade_uid=facts["trade_uid"])
                    if contract["payment_mode"]=="FREE":
                        key=self.trade_facts.local_key(scope,request.task_id)
                        context=self.store.get("trade_contexts",key)
                        self.store.put("trade_contexts",key,{**context,"free":True,"free_reason":contract["free_reason"]})
                        self.store.put("trade_facts",facts["trade_uid"],{**facts,"free_reason":contract["free_reason"]})
                        outcome.settlement={"state":"NOT_REQUIRED","platform_commission_minor":0}
                        self.trade_facts.observe(scope,request,outcome)
                except ValueError:
                    outcome.metadata["contract_error"] = "INVALID_ADMISSION_CONTRACT"
            return
        contract = self.store.get("admission_contracts", facts["trade_uid"])
        if contract:
            outcome.metadata["admission_contract"] = contract
        authorization = facts.get("buyer_authorization")
        if authorization:
            anchor = signed({"v": ANCHOR_VERSION, "author_did": self.identity.did,
                "provider_did": self.identity.did, "buyer_did": facts["buyer_did"],
                "service_id": facts["service_id"], "service_version": facts.get("version", ""),
                "trade_uid": facts["trade_uid"], "execution": facts["execution"],
                "admitted_at": facts["admitted_at"],
                "observed_at": facts.get("delivered_at") or facts["admitted_at"],
                "buyer_authorization": authorization}, signer_for(self.identity))
            outcome.metadata["public_trade_anchor"] = anchor
        version = facts.get("version", "")
        self.assets.record_delivery(facts, request, outcome)
        self.trials.finish_admission(scope, request.task_id, outcome.to_dict())
        self.trials.record(scope, request.task_id, outcome,
                           request=request, version=version)

    def _admit_trade(self, scope, request):
        binding = self.runtime.bindings.get(scope)
        if binding:
            peer = request.metadata.get("_a2n_verified_peer") or {}
            buyer = str(peer.get("caller_did") or "")
            if not buyer and not request.metadata.get("_a2n_anonymous"):
                buyer = self.identity.did
            card = binding.source_card
            opportunity = self.policies.opportunity({"kind": "buyer", "buyer_did": buyer}) if buyer else None
            if opportunity and opportunity.get("effective_state") in {"BLOCKED_LOCAL", "MANUAL_ONLY"}:
                raise ValueError("LOCAL_ADMISSION_POLICY: 本机当前不自动接此买方的任务")
            authorization = request.metadata.get("a2nTradeAuthorization")
            if authorization:
                from a2n_sdk.trade_facts import trade_uid
                self.experience.validate_authorization(authorization)
                if (not buyer or authorization.get("v") != AUTH_VERSION
                        or not self.experience.verify(authorization)
                        or authorization.get("author_did") != buyer
                        or authorization.get("buyer_did") != buyer
                        or authorization.get("provider_did") != self.identity.did
                        or authorization.get("service_id") != scope
                        or authorization.get("trade_uid") != trade_uid(buyer, self.identity.did, scope,
                            request.metadata.get("_a2n_wire_task_id") or request.task_id)
                        or not authorization.get("issued_at", 0) - 30 <= time.time() < authorization.get("expires_at", 0)):
                    raise ValueError("INVALID_TRADE_AUTHORIZATION")
            from a2n_sdk.pricing import is_free
            rework_free = self.rework.reserve(scope, request, buyer, str(card.get("version") or ""))
            admission = self.trials.admit(scope, request.task_id, caller_did=buyer,
                provider_did=self.identity.did, voluntary_free=is_free(card),
                rework_free=rework_free,
                max_pending=1 if opportunity and opportunity.get("effective_state") == "LIMITED" else 2)
            if not admission["free"]:
                if not request.metadata.get("a2nPaymentPlan"):
                    from a2n_sdk.pricing import price_book
                    raise ValueError("PAYMENT_UNAVAILABLE: 收费服务需要双方接受的支付计划" if price_book(card) else
                                     "PRICE_UNSPECIFIED: 试用结束，需明确免费或收费条件")
                self.payment_coordination.validate_admission(scope, request, buyer)
            context = {"role": "provider", "buyer_did": buyer,
                       "provider_did": self.identity.did, "service_id": scope,
                       "version": str(card.get("version") or ""),
                       "free": admission["free"], "free_reason": admission["free_reason"]}
            if buyer and isinstance(request.metadata.get("a2nResolutionEndpoint"), str):
                context["counterparty_endpoint"] = request.metadata["a2nResolutionEndpoint"]
            if authorization:
                context["buyer_authorization"] = authorization
        else:
            item = self.runtime.imported.get(scope)
            card = item.network_card if item else {}
            ext = (card.get("x-a2n") or {}).get("projection") or {}
            context = {"role": "buyer", "buyer_did": self.identity.did,
                       "provider_did": card_did(card) or "", "service_id": ext.get("service_id") or scope,
                       "version": str(card.get("version") or ""),
                       "relation_verified": verify_card(card)[0] if item else False}
            if item and "/a2a/" in str(card.get("url") or ""):
                context["counterparty_endpoint"] = card["url"].rsplit("/a2a/", 1)[0]
        admitted = self.trade_facts.admit(scope, request, context)
        if binding:
            if context["free"]:
                self.contracts.admit_free(request, admitted, source_card=binding.source_card)
            else:
                self.contracts.admit_paid(request, admitted, source_card=binding.source_card,
                    plan=request.metadata["a2nPaymentPlan"])
                payment = self.store.get("payment_acceptances", request.metadata["a2nPaymentPlan"]["plan_id"])
                self.trade_facts.financial(admitted["trade_uid"], payment["state"] if payment["state"] == "CONFIRMED" else "PENDING")
        elif request.metadata.get("a2nPaymentPlan"):
            plan = request.metadata["a2nPaymentPlan"]
            payment = self.payments.get(self.payment_coordination._row(plan["plan_id"])["intent_id"])
            self.trade_facts.financial(admitted["trade_uid"], payment["state"])

    def _invoke_with_contract(self, scope, request):
        binding = self.runtime.bindings.get(scope)
        plan = request.metadata.get("a2nPaymentPlan")
        if not binding and plan and plan["terms"]["method"] == "x402/2":
            return self.payment_coordination.invoke_x402(scope, request)
        if binding:
            from a2n_sdk.trade_facts import digest
            facts = self.trade_facts.for_call(scope, request.task_id)
            contract = self.store.get("admission_contracts", (facts or {}).get("trade_uid", ""))
            if contract and digest(binding.source_card) != contract["source_card_digest"]:
                raise ValueError("ADMITTED_AGENT_CHANGED: 接单后商品条件变化，未执行")
        outcome = self.runtime.invoke_local_id(scope, request)
        if plan and plan["terms"]["flow"] == "upfront":
            if binding:
                payment = self.store.get("payment_acceptances",plan["plan_id"])
                reference = (payment.get("observation") or {}).get("reference")
            else:
                payment = self.payments.get(self.payment_coordination._row(plan["plan_id"])["intent_id"])
                reference = payment.get("reference")
            outcome.settlement={"state":payment["state"],"reference":reference,"platform_commission_minor":0}
        return outcome

    def _prepare_trade_request(self, scope, request):
        if self.runtime.bindings.get(scope):
            peer = request.metadata.get("_a2n_verified_peer") or {}
            if peer.get("caller_did"):
                wire_id = request.metadata.get("_a2n_wire_task_id") or request.task_id
                journal_id = self.peer_exchange.journal_task_id(scope, wire_id, peer["caller_did"])
                if journal_id != wire_id:
                    request.metadata["_a2n_wire_task_id"] = wire_id
                request.task_id = journal_id
            return
        item = self.runtime.imported.get(scope)
        if not item or not verify_card(item.network_card)[0]:
            return
        previous = self.store.task(scope, request.task_id)
        if previous:
            original = ((previous.get("request") or {}).get("metadata") or {}).get("a2nTradeAuthorization")
            if original:
                request.metadata["a2nTradeAuthorization"] = original
            callback = ((previous.get("request") or {}).get("metadata") or {}).get("a2nResolutionEndpoint")
            if callback:
                request.metadata["a2nResolutionEndpoint"] = callback
            admission_authorization = ((previous.get("request") or {}).get("metadata") or {}).get("a2nAdmissionAuthorization")
            if admission_authorization:
                request.metadata["a2nAdmissionAuthorization"] = admission_authorization
            return
        from a2n_sdk.trade_facts import trade_uid
        card = item.network_card
        provider = card_did(card)
        sid = ((card.get("x-a2n") or {}).get("projection") or {}).get("service_id")
        if not provider or not sid:
            return
        for subject in ({"kind": "service", "provider_did": provider, "service_id": sid},
                        {"kind": "provider", "provider_did": provider}):
            opportunity = self.policies.opportunity(subject) or {}
            if opportunity.get("effective_state") in {"MANUAL_ONLY", "BLOCKED_LOCAL"}:
                raise ValueError("LOCAL_SELECTION_POLICY: 本机当前不自动调用此供给")
        now = time.time()
        if not request.skill:
            request.skill = next((str(s.get("id") or "") for s in card.get("skills", []) if isinstance(s, dict)), "")
        request.metadata["a2nResolutionEndpoint"] = self.management.discovery_public_base or self.runtime.local_base_url
        request.metadata["a2nTradeAuthorization"] = signed({"v": AUTH_VERSION,
            "author_did": self.identity.did, "buyer_did": self.identity.did,
            "provider_did": provider, "service_id": sid,
            "trade_uid": trade_uid(self.identity.did, provider, sid, request.task_id),
            "issued_at": now, "expires_at": now + 300}, signer_for(self.identity))
        request.metadata["a2nAdmissionAuthorization"] = self.contracts.authorize_free(request,
            trade_uid=trade_uid(self.identity.did, provider, sid, request.task_id), provider_did=provider,
            service_id=sid, version=str(card.get("version") or ""))
        if request.metadata.get("a2nPaymentPlan"):
            plan = request.metadata["a2nPaymentPlan"]
            row = self.payment_coordination._row(plan["plan_id"])
            if (row["scope"] != scope or plan != row["record"] or input_digest(request) != plan["offer"]["input_digest"]):
                raise ValueError("LOCAL_PAYMENT_PLAN_MISMATCH")
            request.metadata["a2nAdmissionAuthorization"] = self.contracts.authorize_paid(request, plan)

    def _migrate_trade_facts(self):
        """Backfill factual views without executing, granting free work or rearranging samples."""
        from a2n_sdk.ports import CallOutcome
        for row in self.store.iter_requests():
            existing = self.trade_facts.for_call(row["scope"], row["task_id"])
            if existing:
                self._record_historical_sample_gap(existing)
                continue
            raw = row.get("request")
            if not isinstance(raw, dict):
                raw = {"task_id": row["task_id"]}
            try:
                request = CallRequest(**raw)
                result = row.get("outcome")
                outcome = (CallOutcome(**result) if result else
                           CallOutcome(False, row["task_id"], row["state"]))
                scope = row["scope"]
                binding = self.runtime.bindings.get(scope)
                imported = self.runtime.imported.get(scope)
                receipt = (result or {}).get("receipt") or {}
                verified = receipt_proof.verify(receipt)[0] if receipt else False
                peer = request.metadata.get("_a2n_verified_peer") or {}
                sample = self.trials.sample(scope, row["task_id"]) or {}
                mark = self.store.get("trial_calls", f"{scope}::{row['task_id']}") or {}
                context = {"historical": True, "role": "provider" if binding else
                           "buyer" if imported else "unknown", "service_id": scope,
                           "version": sample.get("version", ""),
                           "version_source": "sample" if sample else "LEGACY_UNKNOWN",
                           "free": mark.get("free", False),
                           "free_reason": "FREE_INITIAL" if mark.get("free") else "UNSPECIFIED"}
                if verified:
                    context.update(buyer_did=receipt["caller_did"], provider_did=receipt["provider_did"])
                elif binding:
                    context.update(buyer_did=str(peer.get("caller_did") or ""), provider_did=self.identity.did)
                if imported:
                    ext = (imported.network_card.get("x-a2n") or {}).get("projection") or {}
                    context["service_id"] = ext.get("service_id") or scope
                with self.store.tx():
                    self.trade_facts.admit(scope, request, context)
                    facts = self.trade_facts.observe(scope, request, outcome)
                    self._record_historical_sample_gap(facts)
            except (TypeError, ValueError, KeyError):
                # One damaged legacy row cannot prevent use of other retained records.
                self.store.put("migration_errors", f"trade:{row['scope']}:{row['task_id']}",
                               {"reason": "无法核对旧交易记录；原记录保留"})
        self.store.put("schema_versions", "trade_facts", 2)

    def _record_historical_sample_gap(self, facts):
        if (facts.get("historical") and facts.get("role") == "provider"
                and facts.get("execution") == "DELIVERED"
                and not self.store.get("trial_calls", f"{facts['service_id']}::{facts['task_id']}")):
            self.store.put("historical_sample_gaps", facts["trade_uid"], {
                "service_id": facts["service_id"], "task_id": facts["task_id"],
                "trade_uid": facts["trade_uid"], "execution": "DELIVERED",
                "kind": "LEGACY_MISSING_SAMPLE", "reason": "历史实际交付未纳入原试用计数；原样品不重排"})

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
            self.packages.restore()
            self._migrate_trade_facts()
            self.calls.recover_deliveries()
            self.calls.recover_ready()
            self._projection_worker = threading.Thread(target=self._drain_projections,
                name="a2n-projections", daemon=True)
            self._projection_worker.start()
            self.management.network.start()
            self.coord_mailbox.start()
            self.metadata_mailbox.start()
            self.asset_mailbox.start()
            self.coord_neighbors.start()
            if self.relay_provider:
                self.relay_provider.start()
            if self.discovery:
                self.discovery.start()
            return self
        except Exception:
            self.stop()
            raise

    def stop(self):
        self._stop.set()
        worker = getattr(self, "_projection_worker", None)
        if worker:
            worker.join(timeout=4)
        if getattr(self, "experience_service", None):
            self.experience_service.close()
        if getattr(self, "metadata_mailbox", None):
            self.metadata_mailbox.stop()
        if getattr(self, "asset_mailbox", None):
            self.asset_mailbox.stop()
        if getattr(self, "coord_neighbors", None):
            self.coord_neighbors.stop()
        if getattr(self, "coord_mailbox", None):
            self.coord_mailbox.stop()
        if getattr(self, "coord_network", None):
            self.coord_network.close()
        if getattr(self, "coordination", None):
            self.coordination.close()
        if getattr(self, "relay_provider", None):
            self.relay_provider.stop()
        if self.discovery:
            self.discovery.stop()
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

    def _drain_projections(self):
        while not self._stop.wait(2):
            try:
                self.calls.recover_deliveries(limit=10)
                self.calls.recover_ready()
                if not self._stop.is_set():
                    self.resolution_delivery.drain(limit=1)
            except (ValueError, RuntimeError, OSError):
                # Durable pending events remain available for the next bounded drain.
                pass
