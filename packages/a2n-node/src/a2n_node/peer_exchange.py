"""Provider-side bilateral evidence for the persistent A2A gateway.

The gateway owns HTTP, CallService owns durable execution, and this adapter
owns peer authentication and receipt signing. Ordinary A2A calls remain valid
but never acquire a made-up caller identity or bilateral receipt.
"""
from __future__ import annotations

import time

from a2n_kernel.hashing import now_iso
from a2n_p2p import Identity

from . import peer, receipt, witness
from .card import card_skills


def peer_business_metadata(metadata: dict) -> dict:
    return {key: value for key, value in (metadata or {}).items()
            if key not in {"a2nPeerRequest", "_a2n_verified_peer",
                           "a2nTaskId", "skill"}}


def signed_a2a_input(message: dict, context_id: str, metadata: dict) -> dict:
    """Entire business input covered by the peer signature and receipt hash."""
    return {"message": message, "context_id": context_id or "",
            "metadata": peer_business_metadata(metadata)}


class PeerExchange:
    def __init__(self, identity: Identity, runtime, store):
        self.identity, self.runtime, self.store = identity, runtime, store
        self.guard = peer.ReplayGuard()

    def authenticate(self, service_id: str, proof: dict, *, message: dict,
                     context_id: str, metadata: dict,
                     task_id: str, skill: str) -> dict:
        """Verify an optional A2N proof against the exact A2A request and mount."""
        if not isinstance(proof, dict):
            raise PermissionError("节点签名请求必须是对象")
        binding = self.runtime.bindings.get(service_id)
        if (not binding or not binding.enabled or skill not in card_skills(binding.source_card)
                or proof.get("service_id") != service_id
                or proof.get("task_id") != task_id
                or proof.get("skill") != skill):
            raise PermissionError("节点签名请求与 Agent、任务或能力不一致")
        candidate = {**proof, "payload": signed_a2a_input(
            message, context_id, metadata)}
        ok, why = peer.verify_request(candidate, guard=None,
                                      expected_provider=self.identity.did)
        if not ok:
            raise PermissionError(f"节点签名请求无效：{why}")
        # A retry of an already claimed task returns the durable result. The
        # normal nonce guard must not turn that safe retry into a false failure.
        # New tasks still consume one nonce and enforce the clock window.
        if self.store.task(service_id, task_id):
            if abs(time.time() - float(proof.get("ts") or 0)) > peer.TS_WINDOW:
                raise PermissionError("签名请求已过期；请用 tasks/get 查询旧任务")
        else:
            ok, why = self.guard.check(proof.get("msg_id"), proof.get("ts"))
            if not ok:
                raise PermissionError(f"节点签名请求无效：{why}")
        return {"caller_did": proof["caller_did"],
                "input_hash": proof["payload_hash"]}

    def finalize(self, scope: str, request, outcome):
        proof = (request.metadata or {}).get("_a2n_verified_peer")
        if (not proof or not self.runtime.bindings.get(scope) or not outcome.ok
                or outcome.state.upper() not in {"COMPLETED", "ACCEPTED", "SETTLED"}):
            return outcome
        body = receipt.make_body(
            task_id=request.task_id, caller_did=proof["caller_did"],
            provider_did=self.identity.did, skill=request.skill,
            input_hash=proof["input_hash"],
            output_hash=receipt.hash_payload(outcome.result),
            usage=outcome.usage, ts=now_iso())
        outcome.receipt = receipt.sign(self.identity, body)
        digest = receipt.fingerprint(outcome.receipt)
        outcome.metadata = {**(outcome.metadata or {}),
                            "witness_offer": witness.attest(self.identity, digest)}
        return outcome

    def acknowledge(self, service_id: str, acknowledgement: dict,
                    witness_claim: dict | None = None) -> tuple[int, dict]:
        """Keep the caller's signed acknowledgement beside this call's receipt."""
        task_id = str((acknowledgement or {}).get("task_id") or "")
        row = self.store.task(service_id, task_id) if self.runtime.bindings.get(service_id) else None
        if not row or not row.get("outcome") or not row["outcome"].get("receipt"):
            return 404, {"error": "本节点没有这份交付收据"}
        outcome = row["outcome"]
        ok, why = receipt.verify_ack(acknowledgement, outcome["receipt"])
        if not ok:
            return 401, {"error": why}
        if (not witness.verify_claim(witness_claim, receipt=outcome["receipt"])
                or witness_claim["provider"] !=
                (outcome.get("metadata") or {}).get("witness_offer")):
            return 401, {"error": "双方哈希见证声明无效"}
        outcome["metadata"] = {**(outcome.get("metadata") or {}),
                               "bilateral_ack": acknowledgement,
                               "bilateral_ack_confirmed": True,
                               "witness_claim": witness_claim}
        self.store.finish(service_id, task_id, outcome)
        return 200, {"ok": True, "task_id": task_id}

    def authorize_control(self, service_id: str, task_id: str,
                          method: str, proof: dict | None) -> None:
        """Only the original peer may read or cancel its signed remote task."""
        row = self.store.task(service_id, task_id)
        original = ((row or {}).get("request") or {}).get("metadata") or {}
        signed = original.get("_a2n_verified_peer")
        if not signed:
            return  # Plain third-party A2A retains its existing compatibility path.
        ok, why = peer.verify_control(proof, expected_provider=self.identity.did,
                                      guard=None)
        if (not ok or proof.get("service_id") != service_id
                or proof.get("task_id") != task_id
                or proof.get("method") != method
                or proof.get("caller_did") != signed.get("caller_did")):
            raise PermissionError(f"节点任务控制未授权：{why if not ok else '原调用方不匹配'}")
        ok, why = self.guard.check(proof["msg_id"], proof["ts"])
        if not ok:
            raise PermissionError(f"节点任务控制未授权：{why}")
