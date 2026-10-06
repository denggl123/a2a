"""一人一个节点运行时：多 Agent 挂载、投影与本地 A2A 接入。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import secrets
import threading
import re
from typing import Any, Callable

from .accounts import MemoryAccountVault
from .adapters import DirectA2ATransport, FallbackTransport
from .pipeline import CallPipeline
from .ports import (AcceptancePort, AgentTarget, CallOutcome, CallRequest,
                    CallResponse, SettlementPort, TransportPort)
from .projection import (Signer, local_projection, stable_service_id,
                         supply_projection)
from .upstream import (A2AUpstream, AgentBinding, BindingTable,
                       CallableUpstream, HttpJsonUpstream)


def _public_samples_url(network_url: str) -> str:
    """从卖方的投影卡 URL 推出它的**公开样品面**地址（买方入口）。

    卖方投影卡的 url 形如 `{public_base}/a2a/{service_id}`（`project_binding`），
    其公开面同源提供 `/public/v1/samples?service_id=...`。推不出来就返回空串 ——
    控制台据此**如实显示"入口未知"**，而不是编一个地址。
    """
    text = str(network_url or "")
    if "/a2a/" not in text:
        return ""
    origin, sid = text.rsplit("/a2a/", 1)
    sid = sid.strip("/")
    if not origin.startswith(("http://", "https://")) or not sid:
        return ""
    return f"{origin}/public/v1/samples?service_id={sid}"


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
        self.pipeline = CallPipeline(
            transport or FallbackTransport([("direct", DirectA2ATransport())]),
            acceptance=acceptance, settlement=settlement)
        self.imported: dict[str, ImportedAgent] = {}
        self._imported_lock = threading.RLock()
        self.gateway = None
        # 由装配层注入：给"这份供给"返回调用前就该看到的试用/样品声明（可为 None）。
        self.trial_provider: Callable[[str], dict | None] | None = None
        self.experience_provider = None
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

    def _build_http_upstream(self, protocol: str, endpoint: str, *,
                             account_ref: str | None = None,
                             headers: dict[str, str] | None = None,
                             timeout: float = 60.0):
        """按协议造一个 HTTP 上游。**唯一实现**：mount_http 与 rebind_http 共用，
        免得"挂载时用 A2A、换地址时用 JSON"这种两套语义漂开。"""
        def account_headers() -> dict[str, str]:
            return self.accounts.headers(account_ref)

        kw = {"headers": headers, "header_provider": account_headers if account_ref else None,
              "timeout": timeout}
        return A2AUpstream(endpoint, **kw) if protocol == "a2a" \
            else HttpJsonUpstream(endpoint, **kw)

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
        upstream = self._build_http_upstream(protocol, endpoint,
                                             account_ref=account_ref, headers=headers,
                                             timeout=timeout)
        detected = "local" if endpoint.startswith(("http://127.0.0.1", "http://localhost")) \
            else "remote"
        kind = detected if source_kind == "auto" else source_kind
        return self.bindings.add(AgentBinding(
            service_id=sid, source_card=copy.deepcopy(source_card), upstream=upstream,
            source_kind=kind, account_ref=account_ref,
            metadata={**copy.deepcopy(metadata or {}), "protocol": protocol,
                      # 真实上游地址必须可追溯：调用实际发往这里，而不是卡上的 url
                      # （卡可能不带 url，也可能与真实转发地址不同）。控制台据此
                      # 展示"真实上游"，不冒充本节点。
                      "endpoint": endpoint, "endpoint_scope": detected}))

    def rebind_http(self, service_id: str, endpoint: str, *,
                    protocol: str = "a2a",
                    account_ref: str | None = None,
                    headers: dict[str, str] | None = None,
                    timeout: float = 60.0,
                    metadata: dict[str, Any] | None = None) -> AgentBinding:
        """把**同一份供给**的传输换到一个新地址/新协议（原地，service_id 不变）。

        这是"同一逻辑商品换了上游地址"的正当更新 —— 不是"改绑到别人家的服务"：
        service_id 里带着本节点身份，节点没换、商品没换，只是它现在监听的地址变了
        （容器绿灯上游用随机端口，重启就会换一个）。原地换 upstream 而**不新建**
        service_id，试用/样品/信誉这些按 service_id 累积的事实因此不被打断。

        允许被误用（把一份商品接到内容不同的上游）—— 但那仍是**本节点自己**的声明，
        且调用方看到的是本节点转发的供给；管理面负责把这次变更**响亮地说出来**。
        """
        binding = self.bindings.get(service_id)
        if binding is None:
            raise KeyError(f"没有这份挂载：{service_id}")
        if protocol not in {"a2a", "json"}:
            raise ValueError("protocol 只能是 a2a 或 json")
        ref = account_ref if account_ref is not None else binding.account_ref
        binding.upstream = self._build_http_upstream(
            protocol, endpoint, account_ref=ref, headers=headers, timeout=timeout)
        binding.account_ref = ref
        detected = "local" if endpoint.startswith(("http://127.0.0.1", "http://localhost")) \
            else "remote"
        binding.metadata = {**copy.deepcopy(binding.metadata or {}),
                            **copy.deepcopy(metadata or {}),
                            "protocol": protocol, "endpoint": endpoint,
                            "endpoint_scope": detected}
        return binding

    def project_binding(self, service_id: str, *, public_base: str | None = None) -> dict[str, Any]:
        binding = self.bindings.get(service_id)
        if not binding:
            raise KeyError(f"没有这份挂载：{service_id}")
        base = (public_base or self.local_base_url).rstrip("/")
        card = binding.source_card
        # 试用/样品声明必须在**调用前**就能看到（§6.4）：把它挂进对外卡片，
        # 且**在投影+签名之前**注入，避免"先签名再改卡"把签名改坏。
        info = None
        if self.trial_provider:
            try:
                info = self.trial_provider(service_id)
            except Exception:  # noqa: BLE001 - 声明缺失不该让供给投影失败
                info = None
        if info:
            card = copy.deepcopy(card)
            card.setdefault("x-a2n", {})["trial"] = info
        if self.experience_provider:
            card = copy.deepcopy(card)
            card.setdefault("x-a2n", {})["experience"] = self.experience_provider(base)
        return supply_projection(
            card, node_did=self.node_did,
            public_url=f"{base}/a2a/{service_id}", service_id=service_id,
            source_kind=binding.source_kind, signer=self.signer)

    def invoke_binding(self, service_id: str, request: CallRequest) -> CallResponse:
        """供给侧只执行并交付；不替调用方宣布验收或扣款。"""
        binding = self.bindings.get(service_id)
        if not binding:
            return CallResponse.failure(f"节点没有挂载 {service_id}", state="NOT_FOUND")
        if not binding.enabled:
            return CallResponse.failure(f"{service_id} 已暂停", state="UNAVAILABLE")
        # 一档一卡：调用方按 URL 指定的是**这一份供给**，A2A 的 message/send 不携带 skill。
        # 若调用方没点名技能，就用这份供给卡上声明的技能补齐 —— 否则转发给"一个上游挂多个
        # 技能"的普通 JSON 服务时 skill 为空，上游如实回"这台上游没有技能 ''"（R0 真踩到：
        # finance-legal 一个上游挂 finance-report + legal-contract，就断在这一跳）。
        if not request.skill:
            skills = binding.source_card.get("skills") or []
            first = skills[0].get("id") if skills and isinstance(skills[0], dict) else None
            if first:
                request.skill = str(first)
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
                     account_ref: str | None = None,
                     route_choices: list[dict] | None = None) -> ImportedAgent:
        """把网络 Agent 投影成 localhost A2A Agent，供不方便集成 SDK 的工作台使用。"""
        if self.card_verifier:
            self.card_verifier(network_card)
        choices = copy.deepcopy(route_choices or [])
        if choices:
            from .coordination import CandidateKey
            def key_of(card):
                ext = card.get("x-a2n") or {}
                did = (ext.get("projection") or {}).get("node_did") or (ext.get("sovereign") or {}).get("did")
                return CandidateKey.of_card(did, card)
            expected = key_of(network_card)
            for choice in choices:
                if self.card_verifier:
                    self.card_verifier(choice["card"])
                if key_of(choice["card"]) != expected:
                    raise ValueError("通道方案混入了其他商品")
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
                                         "account_ref": account_ref,
                                         "route_choices": choices}),
            network_card=copy.deepcopy(network_card), local_card=card)
        with self._imported_lock:
            self.imported[pid] = item
        return item

    def invoke_projection(self, projection_id: str, request: CallRequest) -> CallOutcome:
        item, target = self._projection_target(projection_id)
        if not item:
            return CallOutcome(ok=False, task_id=request.task_id, state="NOT_FOUND",
                               error=f"本机没有投影 {projection_id}")
        assert target is not None
        return self.pipeline.invoke(target, request)

    def _projection_target(self, projection_id: str) \
            -> tuple[ImportedAgent | None, AgentTarget | None]:
        with self._imported_lock:
            item = self.imported.get(projection_id)
        if not item:
            return None, None
        target = copy.deepcopy(item.target)
        account_ref = target.metadata.get("account_ref")
        if account_ref:
            target.metadata["headers"].update(self.accounts.headers(account_ref))
        return item, target

    @staticmethod
    def _supply_outcome(item_id: str, request: CallRequest,
                        response: CallResponse) -> CallOutcome:
        return CallOutcome(
            ok=response.ok, task_id=request.task_id, state=response.state,
            result=response.result, error=response.error, usage=response.usage,
            receipt=response.receipt, target_ref=f"local:{item_id}",
            metadata={**response.metadata, "technical_delivery": bool(response.ok and
                      response.state.upper() in {"COMPLETED", "ACCEPTED", "SETTLED"})})

    def refresh_remote_task(self, item_id: str, request: CallRequest,
                            current: CallOutcome) -> CallOutcome:
        return self._control_remote_task("get_task", item_id, request, current)

    def cancel_remote_task(self, item_id: str, request: CallRequest,
                           current: CallOutcome) -> CallOutcome:
        return self._control_remote_task("cancel_task", item_id, request, current)

    def _control_remote_task(self, action: str, item_id: str, request: CallRequest,
                             current: CallOutcome) -> CallOutcome:
        remote_id = str(current.metadata.get("a2a_task_id") or "")
        context_id = str(current.metadata.get("a2a_context_id") or request.context_id or "")
        if not remote_id:
            raise ValueError("这条记录没有远端 A2A task id")

        binding = self.bindings.get(item_id)
        if binding:
            method = getattr(binding.upstream, action, None)
            if not callable(method):
                raise ValueError("这份供给的上游不支持远端任务控制")
            response = method(remote_id, context_id=context_id)
            return self._supply_outcome(item_id, request, response)

        item, target = self._projection_target(item_id)
        if not item or not target:
            raise KeyError(f"本机没有任务所属的 Agent：{item_id}")
        # A signed peer route must re-check a late tasks/get receipt against
        # the durable original input, including after a daemon restart.
        target.metadata["_a2n_original_request"] = request
        pinned = current.metadata.get("transport_target")
        if pinned:
            target.card = copy.deepcopy(pinned["card"])
            target.route = pinned["route"]
        method = getattr(self.pipeline.transport, action, None)
        if not callable(method):
            raise ValueError("当前网络传输层不支持远端任务控制")
        response = method(
            target, remote_id, context_id=context_id,
            route_name=str(current.metadata.get("transport_route") or ""))
        if not isinstance(response, CallResponse):
            raise TypeError("任务控制传输层没有返回 CallResponse")
        return self.pipeline.complete(target, request, response)

    def remove_projection(self, projection_id: str) -> ImportedAgent:
        with self._imported_lock:
            item = self.imported.pop(projection_id, None)
        if not item:
            raise KeyError(f"投影不存在：{projection_id}")
        return item

    # ---------------- 本地 A2A 网关 ----------------

    def start_gateway(self, *, host: str = "127.0.0.1", port: int = 0,
                      allow_remote_calls: bool = False, management=None, pairing=None,
                      calls=None, public_card_bases=None, peer_exchange=None):
        if self.gateway:
            return self.gateway
        from .local_api import LocalA2AGateway
        self.gateway = LocalA2AGateway(self, host=host, port=port,
                                       allow_remote_calls=allow_remote_calls,
                                       management=management, pairing=pairing, calls=calls,
                                       public_card_bases=public_card_bases,
                                       peer_exchange=peer_exchange)
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
        # ``ok`` means the upstream exchange was valid; it does not mean a
        # long-running A2A task delivered its final artifact.  Preserve
        # input/auth-required and other non-terminal states verbatim.
        return self._supply_outcome(item_id, request, response)

    def snapshot(self) -> dict[str, Any]:
        with self._imported_lock:
            projections = list(self.imported.values())
        planner = getattr(self.pipeline.transport, "plan", None)
        bindings = []
        for binding in self.bindings.list():
            source = binding.source_card
            ext = source.get("x-a2n") or {}
            bindings.append({
                "service_id": binding.service_id,
                "name": source.get("name"),
                "description": source.get("description") or "",
                "skills": [s.get("id") for s in source.get("skills") or []],
                "source_kind": binding.source_kind,
                "source_url": source.get("url"),
                "upstream_url": binding.metadata.get("endpoint") or source.get("url"),
                "account_ref": binding.account_ref,
                "enabled": binding.enabled,
                "accepts": source.get("accepts") or ext.get("accepts") or [],
                "price_book": ext.get("price_book") or source.get("price_book") or {},
            })
        projection_views = []
        for item in projections:
            network = item.network_card
            ext = network.get("x-a2n") or {}
            network_url = str(network.get("url") or "")
            projection_views.append({
                "projection_id": item.projection_id,
                "provider_did": ((ext.get("projection") or {}).get("node_did") or ""),
                "service_id": ((ext.get("projection") or {}).get("service_id") or ""),
                "target_ref": item.target.ref,
                "name": item.local_card.get("name"),
                "description": network.get("description") or "",
                "skills": [s.get("id") for s in network.get("skills") or []],
                "url": item.local_card.get("url"),
                "network_url": network_url,
                # 买方入口：在卖方节点的公开面上直读它真实交付过的公开样品（只读、分页）。
                "samples_url": _public_samples_url(network_url),
                # 调用前就能看到的试用声明（卖方投影卡带 `x-a2n.trial`，导入时保留）。
                "trial": ext.get("trial") or {},
                "accepts": network.get("accepts") or ext.get("accepts") or [],
                "price_book": ext.get("price_book") or network.get("price_book") or {},
                "routes": planner(item.target) if callable(planner) else [],
            })
        return {
            "node_did": self.node_did,
            "gateway": self.gateway.base_url if self.gateway else None,
            "bindings": bindings,
            "projections": projection_views,
            "accounts": self.accounts.list(),
        }
