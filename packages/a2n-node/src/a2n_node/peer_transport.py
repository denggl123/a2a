"""Caller-side signed A2A path between two persistent A2N nodes.

This is a transport adapter, not a payment policy. A plain third-party A2A
card continues over the SDK's direct transport without claiming mutual proof.
"""
from __future__ import annotations

from dataclasses import replace

from a2n_sdk.adapters import DirectA2ATransport
from a2n_sdk.ports import AgentTarget, CallRequest, CallResponse
from a2n_sdk.upstream import A2AUpstream, a2a_message

from . import peer, receipt, witness
from .card import card_did, verify_card
from .peer_exchange import peer_business_metadata, signed_a2a_input

PROTOCOL = "a2n-bilateral-a2a/1"


class SignedA2ATransport(DirectA2ATransport):
    def __init__(self, identity, **kwargs):
        super().__init__(**kwargs)
        self.identity = identity

    @staticmethod
    def _route(target: AgentTarget):
        card = target.card or {}
        ext = card.get("x-a2n") or {}
        projection = ext.get("projection") or {}
        endpoint = str(target.route or card.get("url") or "")
        service_id = str(projection.get("service_id") or "")
        suffix = "/a2a/" + service_id
        if (ext.get("peer_protocol") != PROTOCOL
                or projection.get("role") != "supply"
                or not service_id or not endpoint.endswith(suffix)):
            return None
        ok, _why = verify_card(card, require_endpoint=True)
        provider_did = card_did(card)
        if not ok or provider_did != projection.get("node_did"):
            return None
        return provider_did, service_id, endpoint[:-len(suffix)]

    def probe(self, target: AgentTarget, *, timeout: float = 1.0):
        if not self._route(target):
            return {"usable": False, "rtt_ms": None, "detail": "对方未声明双边收据协议"}
        return super().probe(target, timeout=timeout)

    def _upstream(self, target: AgentTarget) -> A2AUpstream | None:
        route = self._route(target)
        if not route:
            return None
        provider_did, service_id, _base = route
        endpoint = target.route or target.card.get("url")
        return A2AUpstream(
            str(endpoint), headers=target.metadata.get("headers") or {},
            timeout=self.timeout, use_system_proxy=self.use_system_proxy,
            control_signer=lambda method, task_id: peer.sign_control(
                self.identity, provider_did=provider_did,
                service_id=service_id, task_id=task_id, method=method))

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallResponse:
        route = self._route(target)
        if not route:
            if ((target.card.get("x-a2n") or {}).get("peer_protocol") == PROTOCOL):
                return CallResponse.failure(
                    "双边签名 Agent Card 或地址无效，拒绝降级为普通调用",
                    state="PROTOCOL_ERROR")
            return CallResponse.failure("对方未声明双边收据协议", state="UNREACHABLE")
        provider_did, service_id, base = route
        skill = request.skill or next(
            (str(s.get("id") or s.get("name") or "")
             for s in target.card.get("skills") or [] if isinstance(s, dict)), "")
        if not skill:
            return CallResponse.failure("Agent Card 没有可签名的能力标识", state="PROTOCOL_ERROR")
        message = a2a_message(request)
        signed_input = signed_a2a_input(message, request.context_id, request.metadata)
        signed = peer.sign_request(
            self.identity, provider_did=provider_did, skill=skill,
            payload=signed_input, task_id=request.task_id, service_id=service_id)
        signed.pop("payload")  # A2A message already carries it; do not duplicate large bodies.
        wire = replace(request, skill=skill, message=message,
                       metadata={**peer_business_metadata(request.metadata),
                                 "a2nPeerRequest": signed})
        response = super().invoke(target, wire)
        if response.state.upper() == "UNREACHABLE":
            # FallbackTransport retries UNREACHABLE. A signed A2N provider
            # must not be re-invoked anonymously through its plain A2A route.
            response.state = "SIGNED_UNREACHABLE"
        return self._confirm(response, request, provider_did, service_id, base, skill)

    def get_task(self, target: AgentTarget, remote_task_id: str, *,
                 context_id: str = "", route_name: str = "") -> CallResponse:
        response = super().get_task(target, remote_task_id,
                                    context_id=context_id, route_name=route_name)
        original = target.metadata.get("_a2n_original_request")
        route = self._route(target)
        if not route or not isinstance(original, CallRequest):
            return CallResponse.failure("恢复双边收据时缺少原请求", state="PROTOCOL_ERROR")
        provider_did, service_id, base = route
        skill = original.skill or next(
            (str(s.get("id") or s.get("name") or "")
             for s in target.card.get("skills") or [] if isinstance(s, dict)), "")
        return self._confirm(response, original, provider_did, service_id, base, skill)

    def _confirm(self, response: CallResponse, request: CallRequest,
                 provider_did: str, service_id: str, base: str,
                 skill: str) -> CallResponse:
        if not response.ok or response.state.upper() not in {
                "COMPLETED", "ACCEPTED", "SETTLED"}:
            return response
        proof = response.receipt
        ok, why = receipt.verify(proof)
        if not ok:
            return CallResponse.failure(f"交付收据无效：{why}", state="INVALID",
                                        metadata={**response.metadata, "stage": "receipt"})
        if (proof.get("by") != provider_did
                or proof.get("provider_did") != provider_did
                or proof.get("caller_did") != self.identity.did
                or proof.get("task_id") != request.task_id
                or proof.get("skill") != skill
                or proof.get("input_hash") != receipt.hash_payload(signed_a2a_input(
                    a2a_message(request), request.context_id, request.metadata))
                or proof.get("output_hash") != receipt.hash_payload(response.result)):
            return CallResponse.failure("交付收据与本次调用不一致", state="INVALID",
                                        metadata={**response.metadata, "stage": "receipt"})
        acknowledgement = receipt.ack(self.identity, proof)
        digest = receipt.fingerprint(proof)
        try:
            witness_claim = witness.claim(
                digest, (response.metadata or {}).get("witness_offer"),
                witness.attest(self.identity, digest))
        except (ValueError, TypeError):
            return CallResponse.failure("供给方哈希见证签名无效", state="INVALID",
                                        metadata={**response.metadata, "stage": "witness_offer"})
        if not witness.verify_claim(witness_claim, receipt=proof):
            return CallResponse.failure("哈希见证与交付收据不一致", state="INVALID",
                                        metadata={**response.metadata, "stage": "witness_offer"})
        delivered = self._send_ack(base, service_id, acknowledgement,
                                   witness_claim, response)
        response.metadata = {**response.metadata,
                             "bilateral_ack": acknowledgement,
                             "witness_claim": witness_claim,
                             "bilateral_ack_confirmed": delivered}
        return response

    def _send_ack(self, base: str, service_id: str, acknowledgement: dict,
                  witness_claim: dict, _response: CallResponse) -> bool:
        return peer.send_ack(base, acknowledgement,
                             service_id=service_id, witness_claim=witness_claim,
                             timeout=min(self.timeout, 10.0))
