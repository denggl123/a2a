"""Canonical structured request content, shared by hashes and wire adapters."""
import copy


def canonical_message(request):
    return copy.deepcopy(request.message) if request.message else {
        "role": "user", "messageId": f"msg_{request.task_id}",
        "parts": ([{"kind": "data", "data": request.payload}] if not isinstance(request.payload, str)
                  else [{"kind": "text", "text": request.payload}])}
