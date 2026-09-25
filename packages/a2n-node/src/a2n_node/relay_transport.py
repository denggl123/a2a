"""Caller-side signed A2A over a volunteer's opaque sealed mailbox."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from a2n_sdk.ports import AgentTarget, CallResponse
from a2n_sdk.upstream import A2AUpstream, network_failure

from .peer_transport import SignedA2ATransport
from .relay_crypto import PROTOCOL, open_response, seal_request


def _aad(did: str, service_id: str, kind: str) -> bytes:
    return f"a2n-relay/1|{did}|{service_id}|{kind}".encode("utf-8")


def relay_post(endpoint: str, provider_pub: str, did: str, service_id: str,
               kind: str, value: dict, *, timeout: float) -> tuple[int, dict]:
    aad = _aad(did, service_id, kind)
    envelope, key = seal_request(provider_pub, value, aad=aad)
    raw = json.dumps({"envelope": envelope}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        endpoint, data=raw, method="POST",
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            sealed = json.loads(response.read(1_400_000))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096))
        except (ValueError, OSError):
            detail = {"error": f"中继 HTTP {exc.code}"}
        return exc.code, detail
    opened = open_response(key, sealed, aad=aad)
    return int(opened.get("status") or 502), opened.get("body") or {}


class RelayA2AUpstream(A2AUpstream):
    def __init__(self, endpoint: str, *, provider_pub: str, did: str,
                 service_id: str, **kwargs):
        super().__init__(endpoint, **kwargs)
        self.provider_pub, self.did, self.service_id = provider_pub, did, service_id

    def _task_response(self, task: dict, remote_id: str,
                       remote_context: str) -> CallResponse:
        response = super()._task_response(task, remote_id, remote_context)
        response.metadata["_relay_public_key"] = self.provider_pub
        return response

    def _request(self, rpc: dict, _headers: dict, deadline: float) -> dict | CallResponse:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CallResponse.failure("中继调用超时", state="TIMEOUT",
                                        metadata={"remote_effect_unknown": True})
        # A relay roundtrip must return quickly; long Agent work is recovered
        # through signed tasks/get over the same sealed route.
        if rpc.get("method") == "message/send":
            rpc = {**rpc, "params": {**rpc.get("params", {}),
                    "configuration": {**(rpc.get("params", {}).get("configuration") or {}),
                                      "blocking": False}}}
        try:
            status, out = relay_post(
                self.endpoint, self.provider_pub, self.did, self.service_id,
                "a2a", rpc, timeout=remaining)
        except Exception as exc:
            return network_failure(exc)
        if status >= 400:
            return CallResponse.failure(
                out, state="DELIVERY_UNKNOWN" if status >= 500 else "FAILED",
                metadata={"stage": "relay", "status": status,
                          "remote_effect_unknown": status >= 500,
                          "replay_safe": False})
        if not isinstance(out, dict):
            return CallResponse.failure("中继供给方没有返回 JSON 对象", state="PROTOCOL_ERROR")
        if out.get("error"):
            return CallResponse.failure(out["error"], metadata={"stage": "provider"})
        task = out.get("result") or {}
        return task if isinstance(task, dict) else CallResponse.failure(
            "中继供给方返回无效任务", state="PROTOCOL_ERROR")


class RelayA2ATransport(SignedA2ATransport):
    def _route(self, target: AgentTarget):
        card = target.card or {}
        relay = ((card.get("x-a2n") or {}).get("relay") or {})
        if relay.get("protocol") != PROTOCOL:
            return None
        route = super()._route(target)
        if not route:
            return None
        did, service_id, base = route
        node = str(relay.get("node") or "").rstrip("/")
        public_key = relay.get("public_key")
        if (not node.startswith(("http://", "https://"))
                or not isinstance(public_key, str) or len(public_key) < 40
                or base != f"{node}/relay/v1/{did}"):
            return None
        return route

    def invoke(self, target: AgentTarget, request):
        declared = (((target.card or {}).get("x-a2n") or {}).get("relay") or {}).get("protocol")
        if declared != PROTOCOL:
            return CallResponse.failure("不是密封中继卡", state="UNREACHABLE")
        return super().invoke(target, request)

    def _upstream(self, target: AgentTarget):
        route = self._route(target)
        if not route:
            return None
        did, service_id, _base = route
        relay = target.card["x-a2n"]["relay"]
        from . import peer
        return RelayA2AUpstream(
            str(target.route or target.card["url"]),
            provider_pub=relay["public_key"], did=did, service_id=service_id,
            timeout=self.timeout, poll_interval=0.1, use_system_proxy=False,
            control_signer=lambda method, task_id: peer.sign_control(
                self.identity, provider_did=did, service_id=service_id,
                task_id=task_id, method=method))

    def _send_ack(self, base: str, service_id: str, acknowledgement: dict,
                  witness_claim: dict, response: CallResponse) -> bool:
        did = base.rsplit("/", 1)[-1]
        provider_pub = response.metadata.get("_relay_public_key")
        if not isinstance(provider_pub, str):
            return False
        try:
            status, body = relay_post(
                f"{base}/a2n/ack/{service_id}", provider_pub, did, service_id,
                "ack", {"service_id": service_id, "ack": acknowledgement,
                        "witness_claim": witness_claim}, timeout=min(self.timeout, 10.0))
            return status == 200 and bool(body.get("ok"))
        except Exception:
            return False
