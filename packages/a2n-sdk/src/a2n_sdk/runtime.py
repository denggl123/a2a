"""一人一个节点运行时：多 Agent 挂载、投影与本地 A2A 接入。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import secrets
import threading
import re
from typing import Any, Callable

from .accounts import MemoryAccountVault
from .adapters import DirectA2ATransport
from .pipeline import CallPipeline
from .ports import (AcceptancePort, AgentTarget, CallOutcome, CallRequest,
                    CallResponse, SettlementPort, TransportPort)
from .projection import (Signer, local_projection, stable_service_id,
                         supply_projection)
from .upstream import (A2AUpstream, AgentBinding, BindingTable,
                       CallableUpstream, HttpJsonUpstream)


@dataclass(slots=True)
class ImportedAgent:
    projection_id: str
    target: AgentTarget
    network_card: dict[str, Any]
    local_card: dict[str, Any]


class NodeRuntime:
    """一个人的常驻 SDK。

    ``NodeRuntime`` 不拥有平台、P2P、验收或支付实现。它只装配端口：

    * 多个真实 Agent 通过 ``UpstreamPort`` 挂载；
    * 网络 Agent 通过 ``TransportPort`` 调用；
    * 验收与结算分别由自己的端口完成；
    * localhost A2A 网关只做协议适配。
    """

    def __init__(self, node_did: str, *, transport: TransportPort | None = None,
                 acceptance: AcceptancePort | None = None,
                 settlement: SettlementPort | None = None,
                 signer: Signer | None = None,
                 card_verifier: Callable[[dict], None] | None = None,
                 accounts: MemoryAccountVault | None = None) -> None:
        if not node_did:
            raise ValueError("node_did 不能为空")
        self.node_did = node_did
        self.signer = signer
        self.card_verifier = card_verifier
        self.bindings = BindingTable()
        self.accounts = accounts or MemoryAccountVault()
        self.pipeline = CallPipeline(transport or DirectA2ATransport(),
                                     acceptance=acceptance,
                                     settlement=settlement)
        self.imported: dict[str, ImportedAgent] = {}
        self._imported_lock = threading.RLock()
        self.gateway = None
        self.management_token = secrets.token_urlsafe(24)

    @staticmethod
    def _valid_id(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Agent 标识只支持 1–128 位字母、数字、下划线和连字符")
        return value

    # ---------------- 账户 ----------------

    def add_account(self, account_id: str, *, kind: str = "agent", label: str = "",
                    headers: dict[str, str] | None = None,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.accounts.put(account_id, kind=kind, label=label,
                                 headers=headers, metadata=metadata)

    def account_references(self, account_id: str) -> list[dict[str, str]]:
        """Return live resources that still need an account.

        The vault deliberately knows nothing about Agents.  Reference checks
        therefore belong at the runtime boundary where bindings and imported
        projections meet, rather than in the credential adapter.
        """
        references = [{"kind": "binding", "id": binding.service_id}
                      for binding in self.bindings.list()
                      if binding.account_ref == account_id]
        with self._imported_lock:
            references.extend(
                {"kind": "projection", "id": item.projection_id}
                for item in self.imported.values()
                if item.target.metadata.get("account_ref") == account_id)
        return references

    def remove_account(self, account_id: str) -> dict[str, Any]:
        references = self.account_references(account_id)
        if references:
            names = "、".join(ref["id"] for ref in references)
            raise ValueError(f"账户仍被 Agent 使用：{names}；请先移除这些资源")
        item = self.accounts.public(account_id)  # also gives a clear not-found error
        self.accounts.remove(account_id)
        return item

    # ---------------- 供给挂载 ----------------

    def mount_callable(self, source_card: dict[str, Any], handler: Callable[[Any], Any],
                       *, service_id: str | None = None,
                       pass_request: bool = False,
                       metadata: dict[str, Any] | None = None) -> AgentBinding:
        sid = self._valid_id(service_id or stable_service_id(self.node_did, source_card, "local"))
        return self.bindings.add(AgentBinding(
            service_id=sid, source_card=copy.deepcopy(source_card),
            upstream=CallableUpstream(handler, pass_request=pass_request),
            source_kind="local", metadata=copy.deepcopy(metadata or {})))

    def mount_http(self, source_card: dict[str, Any], endpoint: str, *,
                   protocol: str = "a2a", service_id: str | None = None,
                   account_ref: str | None = None,
                   headers: dict[str, str] | None = None,
                   timeout: float = 60.0,
                   source_kind: str = "auto",
                   metadata: dict[str, Any] | None = None) -> AgentBinding:
        """挂载本机或远程 HTTP Agent；区别只在地址，不在业务层。"""
        if self.card_verifier:
            self.card_verifier(source_card)
        if protocol not in {"a2a", "json"}:
            raise ValueError("protocol 只能是 a2a 或 json")
        if source_kind not in {"auto", "local", "remote"}:
            raise ValueError("source_kind 只能是 auto / local / remote")
        sid = self._valid_id(service_id or stable_service_id(self.node_did, source_card,
                                                             f"{protocol}:{endpoint}"))

        def account_headers() -> dict[str, str]:
            return self.accounts.headers(account_ref)

        kw = {"headers": headers, "header_provider": account_headers if account_ref else None,
              "timeout": timeout}
        upstream = A2AUpstream(endpoint, **kw) if protocol == "a2a" \
            else HttpJsonUpstream(endpoint, **kw)
        detected = "local" if endpoint.startswith(("http://127.0.0.1", "http://localhost")) \
            else "remote"
        kind = detected if source_kind == "auto" else source_kind
        return self.bindings.add(AgentBinding(
            service_id=sid, source_card=copy.deepcopy(source_card), upstream=upstream,
            source_kind=kind, account_ref=account_ref,
            metadata={**copy.deepcopy(metadata or {}), "protocol": protocol,
                      "endpoint_scope": detected}))

    def project_binding(self, service_id: str, *, public_base: str | None = None) -> dict[str, Any]:
        binding = self.bindings.get(service_id)
        if not binding:
            raise KeyError(f"没有这份挂载：{service_id}")
        base = (public_base or self.local_base_url).rstrip("/")
        return supply_projection(
            binding.source_card, node_did=self.node_did,
            public_url=f"{base}/a2a/{service_id}", service_id=service_id,
            source_kind=binding.source_kind, signer=self.signer)

    def invoke_binding(self, service_id: str, request: CallRequest) -> CallResponse:
        """供给侧只执行并交付；不替调用方宣布验收或扣款。"""
        binding = self.bindings.get(service_id)
        if not binding:
            return CallResponse.failure(f"节点没有挂载 {service_id}", state="NOT_FOUND")
        if not binding.enabled:
            return CallResponse.failure(f"{service_id} 已暂停", state="UNAVAILABLE")
        return binding.upstream.invoke(request)

    def unmount_binding(self, service_id: str) -> AgentBinding:
        """Remove one in-memory supply binding.

        Platform publication is intentionally not understood here.  The
        management service must coordinate that external lifecycle first.
        """
        item = self.bindings.remove(service_id)
        if not item:
            raise KeyError(f"挂载不存在：{service_id}")
        return item

    # ---------------- 使用投影 ----------------

    def import_agent(self, network_card: dict[str, Any], *, target_ref: str | None = None,
                     projection_id: str | None = None,
                     headers: dict[str, str] | None = None,
                     account_ref: str | None = None) -> ImportedAgent:
        """把网络 Agent 投影成 localhost A2A Agent，供不方便集成 SDK 的工作台使用。"""
        if self.card_verifier:
            self.card_verifier(network_card)
        ref = target_ref or str(((network_card.get("x-a2n") or {}).get("agent_id")
                                 or (network_card.get("x-a2n") or {}).get("projection", {}).get("service_id")
                                 or network_card.get("url") or ""))
        if not ref:
            raise ValueError("无法从卡片确定网络 Agent 引用，请显式给 target_ref")
        route = network_card.get("url")
        # 先算稳定 projection id，再把它放进真正的 localhost URL。
        provisional, _ = local_projection(
            network_card, node_did=self.node_did,
            local_url=f"{self.local_base_url}/a2a/pending", target_ref=ref,
            projection_id=projection_id, signer=None)
        pid = self._valid_id(projection_id or provisional)
        if self.bindings.get(pid):
            raise ValueError("这个标识已经用于本机供给")
        if account_ref:
            self.accounts.headers(account_ref)
        pid, card = local_projection(
            network_card, node_did=self.node_did,
            local_url=f"{self.local_base_url}/a2a/{pid}", target_ref=ref,
            projection_id=pid, signer=self.signer)
        item = ImportedAgent(
            projection_id=pid,
            target=AgentTarget(ref=ref, card=copy.deepcopy(network_card), route=route,
                               metadata={"headers": dict(headers or {}),
                                         "account_ref": account_ref}),
            network_card=copy.deepcopy(network_card), local_card=card)
        with self._imported_lock:
            self.imported[pid] = item
        return item

    def invoke_projection(self, projection_id: str, request: CallRequest) -> CallOutcome:
        with self._imported_lock:
            item = self.imported.get(projection_id)
        if not item:
            return CallOutcome(ok=False, task_id=request.task_id, state="NOT_FOUND",
                               error=f"本机没有投影 {projection_id}")
        target = copy.deepcopy(item.target)
        account_ref = target.metadata.get("account_ref")
        if account_ref:
            target.metadata["headers"].update(self.accounts.headers(account_ref))
        return self.pipeline.invoke(target, request)

    def remove_projection(self, projection_id: str) -> ImportedAgent:
        with self._imported_lock:
            item = self.imported.pop(projection_id, None)
        if not item:
            raise KeyError(f"投影不存在：{projection_id}")
        return item

    # ---------------- 本地 A2A 网关 ----------------

    def start_gateway(self, *, host: str = "127.0.0.1", port: int = 0,
                      allow_remote_calls: bool = False, management=None, pairing=None,
                      calls=None, public_card_bases=None):
        if self.gateway:
            return self.gateway
        from .gateway import LocalA2AGateway
        self.gateway = LocalA2AGateway(self, host=host, port=port,
                                       allow_remote_calls=allow_remote_calls,
                                       management=management, pairing=pairing, calls=calls,
                                       public_card_bases=public_card_bases)
        self.gateway.start()
        return self.gateway

    def stop(self) -> None:
        if self.gateway:
            self.gateway.stop()
            self.gateway = None

    @property
    def local_base_url(self) -> str:
        if not self.gateway:
            raise RuntimeError("本地 A2A 网关尚未启动")
        return self.gateway.base_url

    def card_for(self, item_id: str, *, public_base: str | None = None) -> dict[str, Any] | None:
        with self._imported_lock:
            imported = self.imported.get(item_id)
        if imported:
            return copy.deepcopy(imported.local_card)
        binding = self.bindings.get(item_id)
        if not binding:
            return None
        # A projection is a pure view of (source, node, route).  Caching one
        # mutable "published card" let platform/local/P2P routes overwrite each
        # other and made signed hashes nondeterministic for consumers.
        return self.project_binding(item_id, public_base=public_base)

    def invoke_local_id(self, item_id: str, request: CallRequest) -> CallOutcome:
        with self._imported_lock:
            imported = item_id in self.imported
        if imported:
            return self.invoke_projection(item_id, request)
        response = self.invoke_binding(item_id, request)
        return CallOutcome(
            ok=response.ok, task_id=request.task_id,
            # ``ok`` means the upstream exchange was valid; it does not mean a
            # long-running A2A task delivered its final artifact.  Preserve
            # input/auth-required and other non-terminal states verbatim.
            state=response.state,
            result=response.result, error=response.error, usage=response.usage,
            receipt=response.receipt, target_ref=f"local:{item_id}",
            metadata=dict(response.metadata))

    def snapshot(self) -> dict[str, Any]:
        with self._imported_lock:
            projections = list(self.imported.values())
        return {
            "node_did": self.node_did,
            "gateway": self.gateway.base_url if self.gateway else None,
            "bindings": [{
                "service_id": b.service_id,
                "name": b.source_card.get("name"),
                "skills": [s.get("id") for s in b.source_card.get("skills") or []],
                "source_kind": b.source_kind,
                "account_ref": b.account_ref,
                "enabled": b.enabled,
            } for b in self.bindings.list()],
            "projections": [{
                "projection_id": p.projection_id,
                "target_ref": p.target.ref,
                "name": p.local_card.get("name"),
                "url": p.local_card.get("url"),
            } for p in projections],
            "accounts": self.accounts.list(),
        }
