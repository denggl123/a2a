"""Pure A2A representation of a normalized call outcome."""

def task_view(outcome, context_id: str = "") -> dict:
    wire_task_id = outcome.metadata.get("wire_task_id") or outcome.task_id
    pending = {"WORKING": "working", "SUBMITTED": "submitted", "INPUT-REQUIRED": "input-required",
               "AUTH-REQUIRED": "auth-required", "UNKNOWN": "unknown",
               # 上游 408/5xx 说明"交付结果未知"，不是业务失败 —— 线上状态必须与
               # metadata.a2nState 同一口径，不能在这里偷偷落成 failed。
               "DELIVERY_UNKNOWN": "unknown",
               # A2A has no standard "cancel requested" state.  Keep the wire
               # state honest (still working) and expose the local request in metadata.
               "CANCEL_REQUESTED": "working"}
    terminal = {"CANCELED": "canceled", "CANCELLED": "canceled",
                "REJECTED": "rejected", "FAILED": "failed", "INTERRUPTED": "failed"}
    state = pending.get(outcome.state, terminal.get(
        outcome.state, "completed" if outcome.ok else "failed"))
    # A remote A2A server may allocate/replace the context.  Expose that value
    # to the workbench so a follow-up after input/auth-required continues the
    # same remote conversation while the local task id remains pollable here.
    context_id = str(outcome.metadata.get("a2a_context_id")
                     or outcome.metadata.get("a2aContextId")
                     or context_id or "")
    artifacts = []
    if outcome.result is not None:
        part = ({"kind": "text", "text": outcome.result} if isinstance(outcome.result, str)
                else {"kind": "data", "data": outcome.result})
        artifacts = [{"artifactId": f"art_{wire_task_id}", "parts": [part]}]
    metadata = {"a2nState": outcome.state, "targetRef": outcome.target_ref,
                "acceptance": outcome.verdict, "settlement": outcome.settlement,
                "network": outcome.metadata}
    if outcome.receipt:
        metadata["a2nReceipt"] = outcome.receipt
    if outcome.metadata.get("witness_offer"):
        metadata["a2nWitnessOffer"] = outcome.metadata["witness_offer"]
    if outcome.metadata.get("public_trade_anchor"):
        metadata["a2nPublicTradeAnchor"] = outcome.metadata["public_trade_anchor"]
    if outcome.metadata.get("admission_contract"):
        metadata["a2nAdmissionContract"] = outcome.metadata["admission_contract"]
    if outcome.metadata.get("points_delivery"):
        metadata["a2nPointsDelivery"] = outcome.metadata["points_delivery"]
    for key in ("cancel_requested", "cancel_acknowledged",
                "remote_effect_unknown", "remote_terminal", "cancel_note"):
        if key in outcome.metadata:
            metadata[key] = outcome.metadata[key]
    status = {"state": state}
    remote_status = ((outcome.metadata.get("a2a_task") or {}).get("status") or {})
    if isinstance(remote_status, dict) and remote_status.get("message") is not None:
        status["message"] = remote_status["message"]
    return {"kind": "task", "id": wire_task_id, "contextId": context_id,
            "status": status, "artifacts": artifacts,
            "error": None if outcome.ok else outcome.error,
            "metadata": metadata}
