"""自持 P2P 节点 → SDK ``TransportPort`` 适配器。

这层只翻译结果，不把 P2P 身份、签名或收据逻辑复制进 SDK。网络实现仍然只有
``SovereignNode.call`` 一份，调用业务只看 ``CallResponse``。
"""
from __future__ import annotations

from a2n_sdk.ports import (AcceptancePort, AgentTarget, CallRequest, CallResponse,
                           SettlementPort, TransportPort)
from a2n_sdk.runtime import NodeRuntime

from .card import sign_card
from .node import FREE, SovereignNode


class SovereignTransport:
    """让 ``NodeRuntime`` 通过签名直连调用自持节点，不经过平台或中继。"""

    def __init__(self, node: SovereignNode, *, timeout: float = 30.0,
                 settle_mode: str = FREE) -> None:
        self.node = node
        self.timeout = timeout
        self.settle_mode = settle_mode

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        outcome = self.node.call(
            request.skill, request.payload, card=target.card,
            timeout=float(request.metadata.get("timeout") or self.timeout),
            settle_mode=str(request.metadata.get("settle_mode") or self.settle_mode))
        meta = {"peer_did": outcome.peer_did, "chain_ok": outcome.chain_ok,
                "transport": "sovereign-direct"}
        if not outcome.ok:
            return CallResponse.failure(outcome.error, state=outcome.state,
                                        usage=outcome.usage, receipt=outcome.receipt,
                                        metadata=meta)
        return CallResponse.success(outcome.result, state=outcome.state,
                                    usage=outcome.usage, receipt=outcome.receipt,
                                    metadata=meta)


def runtime_from_sovereign(node: SovereignNode, *,
                           transport: TransportPort | None = None,
                           acceptance: AcceptancePort | None = None,
                           settlement: SettlementPort | None = None) -> NodeRuntime:
    """把已有自持节点装配成多 Agent ``NodeRuntime``。

    身份和签名仍由 ``a2n-node`` 掌握；SDK 只收到一个 signer 回调。默认网络出口是
    签名直连，也可以注入组合网络适配器，在不改调用/验收/结算层的前提下加入
    平台、QUIC 或日后的 NAT 打洞实现。
    """
    sovereign = ((node.card.get("x-a2n") or {}).get("sovereign") or {})
    node_port = int(getattr(node, "port", 0) or sovereign.get("port") or 0)
    p2p_port = int(sovereign.get("p2p_port") or 0)

    def signer(card: dict) -> dict:
        return sign_card(node.identity, card, port=node_port, p2p_port=p2p_port)

    return NodeRuntime(
        node.identity.did,
        transport=transport or SovereignTransport(node),
        acceptance=acceptance,
        settlement=settlement,
        signer=signer,
    )
