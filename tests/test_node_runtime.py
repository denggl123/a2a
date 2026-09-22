"""一人一个 SDK 运行时：多 Agent、本地 A2A 投影与远程转发。"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from a2n_node.card import sign_card, verify_card
from a2n_node.sdk_adapter import SovereignTransport, runtime_from_sovereign
from a2n_p2p import Identity
from a2n_registry import UNATTESTED, card_hash as registry_card_hash, card_verdict
from a2n_server.card_projection import platform_projection
from a2n_sdk import (AgentTarget, CallPipeline, CallRequest, CallResponse,
                     FallbackTransport, NodeRuntime, RuntimePlatformBridge,
                     local_projection, supply_projection)


def _card(name: str, skill: str, url: str = "http://source.invalid/a2a") -> dict:
    return {
        "name": name,
        "description": f"{name} 的原始卡",
        "version": "1.0.0",
        "url": url,
        "skills": [{"id": skill, "name": skill}],
        "x-a2n": {"uid": "22222222-2222-4222-8222-222222222222"},
    }


def _post(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _a2a_call(url: str, skill: str, payload) -> dict:
    _, out = _post(url, {
        "jsonrpc": "2.0", "id": "rpc-1", "method": "message/send",
        "params": {"message": {"role": "user", "parts": [
            {"kind": "data", "data": payload}]}, "metadata": {"skill": skill}},
    })
    return out["result"]


def test_projection_is_a_new_signed_card_not_a_mutated_signature():
    identity = Identity.generate()
    source = _card("原始 OCR", "ocr")
    before = json.dumps(source, sort_keys=True)
    projected = supply_projection(
        source, node_did=identity.did, public_url="http://127.0.0.1:9100/a2a/svc",
        service_id="svc_ocr", source_kind="remote",
        signer=lambda card: sign_card(identity, card))

    assert json.dumps(source, sort_keys=True) == before
    assert projected["url"].endswith("/a2a/svc")
    ext = projected["x-a2n"]
    assert ext["origin"]["card_hash"]
    assert ext["projection"]["source_kind"] == "remote"
    assert ext["projection"]["attested"] is True
    assert ext["sovereign"]["did"] == identity.did
    assert verify_card(projected, require_endpoint=True) == (True, "ok")


def test_platform_projection_drops_stale_signature_and_keeps_origin_reference():
    identity = Identity.generate()
    source = sign_card(identity, _card("原始 OCR", "ocr"))
    assert verify_card(source)[0] is True
    projected = platform_projection(
        source, entry="https://gateway.example/a2a/ag_1", agent_id="ag_1",
        source_hash=registry_card_hash(source), relay="https://gateway.example/v1/relay/ag_1")
    assert "sovereign" not in projected["x-a2n"]
    assert projected["x-a2n"]["origin"]["did"] == identity.did
    assert projected["x-a2n"]["origin"]["card_hash"] == registry_card_hash(source)
    assert projected["x-a2n"]["projection"]["attested"] is False
    assert projected["x-a2n"]["projection"]["chain"][-1]["role"] == "platform-compat"
    assert card_verdict(projected)["selfproof"] == UNATTESTED
    assert verify_card(source)[0] is True       # 原卡没有被投影函数改写


def test_one_runtime_serves_multiple_agents_and_workbench_uses_local_projection():
    provider = NodeRuntime("did:a2n:provider")
    consumer = NodeRuntime("did:a2n:consumer")
    try:
        provider.start_gateway()
        provider.mount_callable(_card("OCR", "ocr"),
                                lambda payload: {"text": payload["image"]},
                                service_id="svc_ocr")
        provider.mount_callable(_card("翻译", "translate"),
                                lambda payload: {"translated": payload["text"].upper()},
                                service_id="svc_translate")
        ocr_network = provider.project_binding("svc_ocr")
        translate_network = provider.project_binding("svc_translate")
        assert ocr_network["x-a2n"]["projection"]["attested"] is False
        assert ocr_network["url"].split("/a2a/")[0] == translate_network["url"].split("/a2a/")[0]

        consumer.start_gateway()
        imported = consumer.import_agent(ocr_network, target_ref=ocr_network["url"])
        # 工作台拿到的是自己电脑的 localhost 地址，不是供给节点地址。
        assert imported.local_card["url"].startswith(consumer.local_base_url)
        assert imported.local_card["url"] != ocr_network["url"]

        task = _a2a_call(imported.local_card["url"], "ocr", {"image": "invoice.png"})
        assert task["status"]["state"] == "completed"
        assert task["artifacts"][0]["parts"][0]["data"] == {"text": "invoice.png"}
        assert task["metadata"]["acceptance"]["quality_measured"] is False
    finally:
        consumer.stop()
        provider.stop()


def test_node_can_publish_a_remote_agent_and_forward_the_last_hop():
    upstream = NodeRuntime("did:a2n:upstream")
    provider = NodeRuntime("did:a2n:provider")
    consumer = NodeRuntime("did:a2n:consumer")
    try:
        upstream.start_gateway()
        upstream.mount_callable(_card("云端成片", "video"),
                                lambda payload: {"asset": f"https://cdn/{payload['brief']}.mp4"},
                                service_id="svc_cloud_video")
        upstream_card = upstream.project_binding("svc_cloud_video")

        provider.start_gateway()
        provider.mount_http(upstream_card, upstream_card["url"], protocol="a2a",
                            service_id="svc_resold_video", source_kind="remote")
        network_card = provider.project_binding("svc_resold_video")
        assert network_card["x-a2n"]["projection"]["source_kind"] == "remote"
        assert len(network_card["x-a2n"]["projection"]["chain"]) == 2

        consumer.start_gateway()
        local = consumer.import_agent(network_card, target_ref=network_card["url"])
        task = _a2a_call(local.local_card["url"], "video", {"brief": "launch"})
        assert task["status"]["state"] == "completed"
        assert task["artifacts"][0]["parts"][0]["data"]["asset"].endswith("launch.mp4")
    finally:
        consumer.stop()
        provider.stop()
        upstream.stop()


def test_local_management_accounts_are_redacted_and_token_protected():
    runtime = NodeRuntime("did:a2n:me")
    try:
        runtime.start_gateway()
        url = runtime.local_base_url + "/v1/accounts"
        status, item = _post(url, {
            "account_id": "video-cloud", "label": "视频云账户",
            "headers": {"Authorization": "Bearer very-secret", "X-Tenant": "t1"},
            "metadata": {"region": "cn", "refresh_token": "also-secret"}},
            {"X-A2N-Local-Token": runtime.management_token})
        assert status == 201
        assert "very-secret" not in json.dumps(item) and "also-secret" not in json.dumps(item)
        assert item["metadata"] == {"region": "cn", "refresh_token": "***"}
        assert {h["name"] for h in item["headers"]} == {"Authorization", "X-Tenant"}
        assert next(h for h in item["headers"] if h["name"] == "Authorization")["sensitive"] is True
    finally:
        runtime.stop()


def test_pipeline_keeps_transport_acceptance_and_settlement_separate():
    calls: list[str] = []

    class Transport:
        def invoke(self, target, request):
            calls.append("transport")
            return CallResponse.success({"draft": "x"})

    class Reject:
        def evaluate(self, target, request, response):
            calls.append("acceptance")
            return {"passed": False, "reasons": ["缺少成片"]}

    class MustNotSettle:
        def settle(self, *args):
            calls.append("settlement")
            raise AssertionError("验收失败不应结算")

    pipeline = CallPipeline(Transport(), Reject(), MustNotSettle())
    out = pipeline.invoke(AgentTarget("svc", _card("x", "video")),
                          CallRequest(skill="video", payload={}))
    assert out.state == "REJECTED"
    assert calls == ["transport", "acceptance"]


def test_network_routes_fallback_only_before_a_connection_is_established():
    calls = []

    class Route:
        def __init__(self, name, response):
            self.name, self.response = name, response

        def invoke(self, _target, _request):
            calls.append(self.name)
            return self.response

    direct = Route("direct", CallResponse.failure("no route", state="UNREACHABLE"))
    platform = Route("platform", CallResponse.success({"ok": True}))
    transport = FallbackTransport([("direct", direct), ("platform", platform)])
    response = transport.invoke(AgentTarget("svc", _card("x", "ocr")), CallRequest())
    assert response.ok and calls == ["direct", "platform"]
    assert response.metadata["transport_route"] == "platform"
    assert len(response.metadata["transport_attempts"]) == 2

    calls.clear()
    rejected = Route("direct", CallResponse.failure("denied", state="REJECTED"))
    response = FallbackTransport([
        ("direct", rejected), ("platform", platform),
    ]).invoke(AgentTarget("svc", _card("x", "ocr")), CallRequest())
    assert not response.ok and calls == ["direct"]


def test_settlement_state_is_not_hidden_behind_accepted_delivery():
    class Transport:
        def invoke(self, _target, _request):
            return CallResponse.success({"asset": "done"})

    class Settlement:
        def __init__(self, state):
            self.state = state

        def settle(self, *_args):
            return {"state": self.state, "reason": "rail unavailable"}

    target = AgentTarget("svc", _card("x", "video"))
    pending = CallPipeline(Transport(), settlement=Settlement("PENDING")).invoke(
        target, CallRequest())
    assert pending.ok and pending.state == "SETTLEMENT_PENDING"
    failed = CallPipeline(Transport(), settlement=Settlement("FAILED")).invoke(
        target, CallRequest())
    assert not failed.ok and failed.state == "SETTLEMENT_FAILED"
    assert failed.result == {"asset": "done"}  # 已交付事实不能因扣款失败被抹掉


def test_sovereign_direct_adapter_plugs_into_the_same_call_pipeline():
    class Outcome:
        ok = True
        result = {"text": "P2P"}
        state = "ACCEPTED"
        usage = {"call_count": 1}
        receipt = {"sig": "provider-signed"}
        error = ""
        peer_did = "did:a2n:provider"
        chain_ok = True

    class Node:
        def call(self, skill, payload, **kwargs):
            assert skill == "ocr" and payload == {"image": "x"}
            assert kwargs["card"]["name"] == "OCR"
            return Outcome()

    pipeline = CallPipeline(SovereignTransport(Node()))
    out = pipeline.invoke(AgentTarget("did:a2n:provider/svc", _card("OCR", "ocr")),
                          CallRequest(skill="ocr", payload={"image": "x"}))
    assert out.ok and out.result == {"text": "P2P"}
    assert out.receipt == {"sig": "provider-signed"}


def test_sovereign_node_factory_owns_projection_signing_without_sdk_crypto():
    class Node:
        identity = Identity.generate()
        port = 9123
        card = {"x-a2n": {"sovereign": {"p2p_port": 9234}}}

        def call(self, *_args, **_kwargs):  # pragma: no cover - this test only signs
            raise AssertionError("不应发起网络调用")

    runtime = runtime_from_sovereign(Node())
    runtime.start_gateway()
    try:
        binding = runtime.mount_callable(_card("OCR", "ocr"), lambda payload: payload)
        projected = runtime.project_binding(binding.service_id)
        assert projected["x-a2n"]["sovereign"]["did"] == Node.identity.did
        assert projected["x-a2n"]["sovereign"]["p2p_port"] == 9234
        assert verify_card(projected, require_endpoint=True) == (True, "ok")
    finally:
        runtime.stop()


def test_platform_bridge_hides_current_one_tunnel_per_card_constraint():
    clients = []

    class FakeClient:
        def __init__(self, base_url, principal=None):
            self.base = base_url
            self.principal = principal
            self.node_id = None
            self.heartbeats = []
            clients.append(self)

        def register(self, card, visibility, discover_limit):
            self.node_id = "ag_" + card["x-a2n"]["projection"]["service_id"]
            return {"agent_id": self.node_id}

        def heartbeat(self, report):
            self.heartbeats.append(report)
            return report

    class FakeTunnel:
        def __init__(self, client, on_task, local_base=None, attest_fn=None):
            self.client = client
            self.on_task = on_task
            self.local_base = local_base
            self.connected = threading.Event()
            self.last_error = None

        def start(self):
            self.connected.set()

        def stop(self):
            self.connected.clear()

    runtime = NodeRuntime("did:a2n:one-person")
    bridge = RuntimePlatformBridge(runtime, client_factory=FakeClient,
                                   tunnel_factory=FakeTunnel, heartbeat_interval=2)
    try:
        runtime.mount_callable(_card("OCR", "ocr"), lambda x: x, service_id="ocr")
        runtime.mount_callable(_card("翻译", "translate"), lambda x: x, service_id="translate")
        first = bridge.publish("ocr")
        second = bridge.publish("translate")
        assert runtime.snapshot()["gateway"]
        assert first.tunnel.local_base.split("/_a2n/")[0] == second.tunnel.local_base.split("/_a2n/")[0]
        assert {x["service_id"] for x in bridge.snapshot()} == {"ocr", "translate"}
        time.sleep(0.15)
        assert all(c.heartbeats and c.heartbeats[0]["mode"] == "relay" for c in clients)
    finally:
        bridge.stop()
        runtime.stop()


def test_route_specific_supply_cards_do_not_overwrite_local_projection():
    runtime = NodeRuntime("did:a2n:route-specific")
    runtime.start_gateway()
    try:
        runtime.mount_callable(_card("OCR", "ocr"), lambda value: value,
                               service_id="ocr")
        local_before = runtime.card_for("ocr")
        public = runtime.project_binding(
            "ocr", public_base="https://agents.example/network")
        local_after = runtime.card_for("ocr")
        assert local_after == local_before
        assert local_after["url"].startswith("http://127.0.0.1:")
        assert public["url"] == "https://agents.example/network/a2a/ocr"
        assert public != local_before
    finally:
        runtime.stop()


def test_explicit_loopback_proxy_can_fetch_exact_public_projection_card():
    runtime = NodeRuntime("did:a2n:public-card")
    runtime.start_gateway(public_card_bases=["https://agents.example/network"])
    try:
        runtime.mount_callable(_card("OCR", "ocr"), lambda value: value,
                               service_id="ocr")
        request = urllib.request.Request(
            runtime.local_base_url + "/a2a/ocr/.well-known/agent.json",
            headers={"Host": "agents.example"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=5) as response:
            card = json.loads(response.read().decode("utf-8"))
        expected = runtime.project_binding(
            "ocr", public_base="https://agents.example/network")
        assert card == expected
        assert card["url"] == "https://agents.example/network/a2a/ocr"
        _, rpc = _post(runtime.local_base_url + "/a2a/ocr", {
            "jsonrpc": "2.0", "id": "public-call", "method": "message/send",
            "params": {"message": {"messageId": "public-task", "parts": [
                {"kind": "data", "data": {"text": "hello"}}]},
                "metadata": {"skill": "ocr"}},
        }, headers={"Host": "agents.example"})
        assert rpc["result"]["status"]["state"] == "completed"
        assert rpc["result"]["artifacts"][0]["parts"][0]["data"] == {
            "text": "hello"}
    finally:
        runtime.stop()
