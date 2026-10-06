"""Explicit one-time agreed rework; creates a new linked trade, never replays one."""
from __future__ import annotations

import copy

from a2n_sdk.messages import canonical_message
from a2n_sdk.ports import CallRequest
from a2n_sdk.trade_facts import digest


def work_digest(request):
    message = canonical_message(request)
    message.pop("messageId", None)
    return digest([request.skill, message, request.context_id])


class ReworkService:
    def __init__(self, daemon):
        self.node = daemon

    def execute(self, proposal_id):
        agreement, original = self.node.resolutions.rework_agreement(proposal_id)
        if original["role"] != "buyer":
            raise ValueError("ONLY_BUYER_CAN_START_REWORK")
        imported = self.node.runtime.imported.get(original["scope"])
        if not imported or str(imported.network_card.get("version") or "") != original["version"]:
            raise ValueError("REWORK_AGENT_VERSION_CHANGED")
        row = self.node.store.task(original["scope"], original["task_id"])
        request = CallRequest(**copy.deepcopy(row["request"]))
        request.task_id = "rw_" + digest([proposal_id, original["trade_uid"]])[:32]
        for key in ("a2nPeerRequest", "a2nTradeAuthorization", "a2nAdmissionAuthorization", "a2nResolutionEndpoint"):
            request.metadata.pop(key, None)
        request.metadata = {k: v for k, v in request.metadata.items() if not k.startswith("_a2n_")}
        request.metadata["a2nReworkAuthorization"] = {"proposal_id": proposal_id, "original_trade_uid": original["trade_uid"]}
        return self.node.calls.invoke(original["scope"], request).to_dict()

    def reserve(self, service_id, request, buyer_did, version):
        auth = request.metadata.get("a2nReworkAuthorization")
        if not auth:
            return False
        if not isinstance(auth, dict) or set(auth) != {"proposal_id", "original_trade_uid"}:
            raise ValueError("INVALID_REWORK_AUTHORIZATION")
        _, original = self.node.resolutions.rework_agreement(auth["proposal_id"])
        if (original["trade_uid"] != auth["original_trade_uid"] or original["provider_did"] != self.node.identity.did
            or original["buyer_did"] != buyer_did or original["service_id"] != service_id or original["version"] != version):
            raise ValueError("REWORK_PARTY_OR_VERSION_MISMATCH")
        row = self.node.store.task(original["scope"], original["task_id"])
        if not row or not row.get("request") or work_digest(request) != work_digest(CallRequest(**row["request"])):
            raise ValueError("REWORK_INPUT_CHANGED: 新条件须另行协商")
        expected = "rw_" + digest([auth["proposal_id"], original["trade_uid"]])[:32]
        wire_id = request.metadata.get("_a2n_wire_task_id") or request.task_id
        if wire_id != expected:
            raise ValueError("REWORK_TASK_MISMATCH")
        reservation = self.node.store.get("rework_reservations", auth["proposal_id"])
        if reservation and reservation["task_id"] != request.task_id:
            raise ValueError("REWORK_ALREADY_USED")
        self.node.store.put("rework_reservations", auth["proposal_id"], {"task_id": request.task_id, "service_id": service_id})
        return True
