"""Provider-side bilateral evidence for the persistent A2A gateway.

The gateway owns HTTP, CallService owns durable execution, and this adapter
owns peer authentication and receipt signing. Ordinary A2A calls remain valid
but never acquire a made-up caller identity or bilateral receipt.
"""
from __future__ import annotations

import time
import hashlib

from a2n_kernel.hashing import now_iso
from a2n_p2p import Identity

from . import peer, receipt, witness
from .card import card_skills
from .feedback_identity import absorb_feedback, offer_feedback


def peer_business_metadata(metadata: dict) -> dict:
    return {key: value for key, value in (metadata or {}).items()
            if key not in {"a2nPeerRequest", "_a2n_verified_peer",
                           "a2nTradeAuthorization", "a2nTaskId", "skill", "_a2n_wire_task_id"}}


def signed_a2a_input(message: dict, context_id: str, metadata: dict) -> dict:
    """Entire business input covered by the peer signature and receipt hash."""
    return {"message": message, "context_id": context_id or "",
            "metadata": peer_business_metadata(metadata)}


class PeerExchange:
    def __init__(self, identity: Identity, runtime, store, feedback=None):
        self.identity, self.runtime, self.store = identity, runtime, store
        # 双方反馈账本（R2，可空：旧装配/单测没接就不捎带）。见 docs/FEEDBACK-API.md §4。
        self.feedback = feedback
        self.guard = peer.ReplayGuard()

    def journal_task_id(self, service_id, task_id, caller_did):
        """Retain legacy rows; colliding parties get separate durable records."""
        row = self.store.task(service_id, task_id)
        if row:
            metadata = (row.get("request") or {}).get("metadata") or {}
            owner = (metadata.get("_a2n_verified_peer") or {}).get("caller_did")
            if owner == caller_did:
                return task_id
        else:
            # Once an alternate key exists, a removed legacy row must not move it.
            alternate = "pj_" + hashlib.sha256((caller_did + "\0" + task_id).encode()).hexdigest()
            if not self.store.task(service_id, alternate):
                return task_id
        return "pj_" + hashlib.sha256((caller_did + "\0" + task_id).encode()).hexdigest()

    def authenticate(self, service_id: str, proof: dict, *, message: dict,
                     context_id: str, metadata: dict,
                     task_id: str, skill: str) -> dict:
        """Verify an optional A2N proof against the exact A2A request and mount."""
        if not isinstance(proof, dict):
            raise PermissionError("节点签名请求必须是对象")
        binding = self.runtime.bindings.get(service_id)
        if (not binding or not binding.enabled or not binding.metadata.get("listed", True)
                or skill not in card_skills(binding.source_card)
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
        journal_id = self.journal_task_id(service_id, task_id, proof["caller_did"])
        if self.store.task(service_id, journal_id):
            if abs(time.time() - float(proof.get("ts") or 0)) > peer.TS_WINDOW:
                raise PermissionError("签名请求已过期；请用 tasks/get 查询旧任务")
        else:
            ok, why = self.guard.check(proof.get("msg_id"), proof.get("ts"))
            if not ok:
                raise PermissionError(f"节点签名请求无效：{why}")
        return {"caller_did": proof["caller_did"],
                "input_hash": proof["payload_hash"], "journal_task_id": journal_id}

    def finalize(self, scope: str, request, outcome):
        wire_task_id = request.metadata.get("_a2n_wire_task_id") or request.task_id
        if wire_task_id != request.task_id:
            outcome.metadata["wire_task_id"] = wire_task_id
        proof = (request.metadata or {}).get("_a2n_verified_peer")
        if (not proof or not self.runtime.bindings.get(scope) or not outcome.ok
                or outcome.state.upper() not in {"COMPLETED", "ACCEPTED", "SETTLED"}):
            return outcome
        body = receipt.make_body(
            task_id=wire_task_id, caller_did=proof["caller_did"],
            provider_did=self.identity.did, skill=request.skill,
            input_hash=proof["input_hash"],
            output_hash=receipt.hash_payload(outcome.result),
            usage=outcome.usage, ts=now_iso())
        outcome.receipt = receipt.sign(self.identity, body)
        digest = receipt.fingerprint(outcome.receipt)
        # R2 捎带（FEEDBACK-API §4）：把**本供给已写好的** seller_to_buyer 反馈随终结结果一起带回。
        # 只捎带已存在的那一份；没写过就不带（不凭空造评价）。
        carried = offer_feedback(self.feedback, task_id=request.task_id,
                                 direction="seller_to_buyer", counterparty_did=proof["caller_did"],
                                 provider_did=self.identity.did)
        extra = {"feedback": carried} if carried else {}
        outcome.metadata = {**(outcome.metadata or {}),
                            "witness_offer": witness.attest(self.identity, digest),
                            **extra}
        return outcome

    def acknowledge(self, service_id: str, acknowledgement: dict,
                    witness_claim: dict | None = None, feedback=None) -> tuple[int, dict]:
        """Keep the caller's signed acknowledgement beside this call's receipt.

        同时是 R2 捎带的一跳（FEEDBACK-API §4）：收下调用方随回执带来的
        `buyer_to_seller` 反馈（验签后原样留存），并把本供给已写好的
        `seller_to_buyer` 反馈随响应带回给调用方。
        """
        task_id = str((acknowledgement or {}).get("task_id") or "")
        journal_id = self.journal_task_id(service_id, task_id, str((acknowledgement or {}).get("by") or ""))
        row = self.store.task(service_id, journal_id) if self.runtime.bindings.get(service_id) else None
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
        # R2 捎带：收下调用方的 buyer_to_seller 反馈（验签后原样留存；坏的就留痕不采信）。
        absorbed = absorb_feedback(self.feedback, feedback)
        if absorbed is not None:
            outcome["metadata"]["feedback_received"] = absorbed.get("feedback_id")
        self.store.finish(service_id, journal_id, outcome)
        carried = offer_feedback(self.feedback, task_id=journal_id, direction="seller_to_buyer",
                                 counterparty_did=outcome["receipt"]["caller_did"], provider_did=self.identity.did)
        result: dict = {"ok": True, "task_id": task_id}
        if carried:
            result["feedback"] = carried
        return 200, result

    def authorize_control(self, service_id: str, task_id: str,
                          method: str, proof: dict | None) -> str:
        """Only the original peer may read or cancel its signed remote task."""
        journal_id = task_id
        if proof is not None:
            ok, why = peer.verify_control(proof, expected_provider=self.identity.did, guard=None)
            if (not ok or proof.get("service_id") != service_id or proof.get("task_id") != task_id or proof.get("method") != method):
                raise PermissionError(f"节点任务控制未授权：{why}")
            journal_id = self.journal_task_id(service_id, task_id, proof["caller_did"])
        row = self.store.task(service_id, journal_id)
        if proof is not None and row is None:
            raise PermissionError("节点任务控制未授权：本调用方没有对应任务")
        original = ((row or {}).get("request") or {}).get("metadata") or {}
        signed = original.get("_a2n_verified_peer")
        if not signed:
            if proof is not None:
                ok, why = self.guard.check(proof["msg_id"], proof["ts"])
                if not ok:
                    raise PermissionError(f"节点任务控制未授权：{why}")
            return journal_id  # Plain third-party A2A compatibility or no such peer task.
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
        return journal_id
