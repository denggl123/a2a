"""Persistent local node contracts: restart, origin isolation and single execution."""
from concurrent.futures import ThreadPoolExecutor
import base64
from http.cookiejar import CookieJar
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from a2n_node.card import pub_unb64, verify_card
from a2n_node.card import sign_card
from a2n_node.daemon import Daemon
from a2n_node import receipt as receipt_proof
from a2n_node import peer as peer_protocol
from a2n_node.protection import EnvironmentProtector
from a2n_node.witness import attest, claim, verify_witness
from a2n_node.public_directory import PublicDirectoryClient
from a2n_node.peer_exchange import signed_a2a_input
from a2n_node.peer_transport import SignedA2ATransport
from a2n_p2p import Identity
from a2n_sdk import (AgentTarget, CallPipeline, CallRequest, CallResponse,
                     FallbackTransport, NodeRuntime)
from a2n_sdk.calls import CallService
from a2n_sdk.pairing import PairingService
from a2n_sdk.protection import WindowsProtector
from a2n_sdk.storage import LocalStore
from a2n_sdk.upstream import AgentBinding


def http(base, path, body=None, token="", origin="", extra=None):
    headers = {"Content-Type": "application/json", "X-A2N-Local-Token": token, **(extra or {})}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(base + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def card(url="http://127.0.0.1:9999/a2a/echo"):
    return {"name": "Echo", "url": url, "skills": [{"id": "echo", "name": "Echo"}], "version": "1.0.0"}


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def test_restart_keeps_identity_accounts_mounts_and_local_projection(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    did = daemon.identity.did
    try:
        base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
        assert http(base, "/v1/accounts", {"account_id": "cloud", "headers": {
            "Authorization": "Bearer restart-secret"}}, token)[0] == 201
        status, mounted = http(base, "/v1/bindings/http", {"card": card(), "endpoint": card()["url"],
                                                       "account_ref": "cloud"}, token)
        assert status == 201
        assert verify_card(mounted["card"])[0]
        status, imported = http(base, "/v1/projections", {"card": card(), "account_ref": "cloud"}, token)
        assert status == 201
        assert http(base, "/v1/bindings/state", {"service_id": mounted["service_id"], "enabled": False}, token)[0] == 200
    finally:
        daemon.stop()
    assert b"restart-secret" not in (tmp_path / "runtime.db").read_bytes()
    restarted = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        assert restarted.identity.did == did
        assert restarted.runtime.accounts.headers("cloud")["Authorization"] == "Bearer restart-secret"
        assert not restarted.runtime.bindings.get(mounted["service_id"]).enabled
        new_card = restarted.runtime.card_for(imported["projection_id"])
        assert new_card["url"].startswith(restarted.runtime.local_base_url)
        assert verify_card(new_card)[0]
        snapshot = restarted.management.snapshot()
        assert "restart-secret" not in json.dumps(snapshot)
    finally:
        restarted.stop()


def test_public_directory_is_opt_in_on_same_node_and_survives_restart(tmp_path, protector):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        p2p_port = probe.getsockname()[1]
    daemon = Daemon(tmp_path, port=0, protector=protector, p2p_port=p2p_port,
                    beacon=False).start()
    try:
        base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
        daemon.management.discovery_public_base = base
        status, _ = http(base, "/v1/bindings/http", {
            "card": card(), "endpoint": card()["url"], "service_id": "public_echo"}, token)
        assert status == 201
        daemon.runtime.import_agent({"name": "Private imported card", "url": "http://private.invalid/a2a",
                                     "skills": [{"id": "echo"}], "version": "1.0.0"})
        assert http(base, "/public/v1/agents?skill=echo")[0] == 403
        assert http(base, "/v1/public-service", {"enabled": True})[0] == 401
        for _ in range(3):
            assert http(base, "/v1/public-service", {"enabled": True}, token,
                        origin="https://attacker.example")[0] == 403
        assert http(base, "/v1/public-service", {"enabled": "true"}, token)[0] == 400
        assert http(base, "/v1/public-service", {"enabled": True}, token)[0] == 200
        status, listing = http(base, "/public/v1/agents?skill=echo")
        assert status == 200 and listing["count"] == 1
        assert listing["cards"][0]["url"] == base + "/a2a/public_echo"
        assert verify_card(listing["cards"][0])[0]
        status, routes = http(base, "/public/v1/routes?did=" + daemon.identity.did)
        assert status == 200 and routes["routes"][0]["endpoint"] == \
            base + "/a2a/public_echo"
        assert "Private imported card" not in json.dumps(listing)
        assert http(base, "/v1/runtime")[0] == 401
    finally:
        daemon.stop()
    restarted = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        assert restarted.management.snapshot()["public_service"]["enabled"] is True
        base, token = restarted.runtime.local_base_url, restarted.runtime.management_token
        assert http(base, "/v1/public-service", {"enabled": False}, token)[0] == 200
        assert http(base, "/public/v1/agents?skill=echo")[0] == 403
    finally:
        restarted.stop()


def test_public_witness_only_keeps_signed_hashes_and_obeys_switch(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        daemon.management.discovery_public_base = base
        seller, buyer = Identity.generate(), Identity.generate()
        receipt = receipt_proof.sign(seller, receipt_proof.make_body(
            task_id="witnessed-task", caller_did=buyer.did,
            provider_did=seller.did, skill="private-skill",
            input_hash=receipt_proof.hash_payload("private-input"),
            output_hash=receipt_proof.hash_payload("private-output")))
        digest = receipt_proof.fingerprint(receipt)
        signed_claim = claim(digest, attest(seller, digest), attest(buyer, digest))
        endpoint = "/public/v1/witness"
        assert http(base, endpoint, {"claim": signed_claim})[0] == 403
        daemon.management.command("/v1/public-service", {"enabled": True})
        assert http(base, endpoint, {"receipt": receipt})[0] == 400
        bad_claim = {**signed_claim, "caller": {**signed_claim["caller"], "sig": "wrong"}}
        assert http(base, endpoint, {"claim": bad_claim})[0] == 400
        assert http(base, endpoint, {"claim": {**signed_claim, "receipt": receipt}})[0] == 400
        status, witnessed = http(base, endpoint, {"claim": signed_claim})
        assert status == 201
        body = witnessed["body"]
        assert body["receipt_hash"] == digest
        assert body["claim_hash"] == receipt_proof.fingerprint(signed_claim)
        assert "private-skill" not in json.dumps(witnessed)
        assert "witnessed-task" not in json.dumps(witnessed)
        from a2n_p2p import verify_pub
        assert verify_pub(pub_unb64(witnessed["pub"]), body, witnessed["sig"])
        assert verify_witness(witnessed, receipt_hash=digest)
        assert not verify_witness({**witnessed, "body": {**body, "claim_hash": "0" * 64}})
        assert http(base, endpoint + "?receipt_hash=" + digest)[1] == witnessed
        daemon.management.command("/v1/public-service", {"enabled": False})
        assert http(base, endpoint + "?receipt_hash=" + digest)[0] == 403
    finally:
        daemon.stop()


def test_optional_volunteer_directory_discovers_then_calls_provider_directly(tmp_path, protector):
    provider = Daemon(tmp_path / "volunteer", port=0, protector=protector).start()
    try:
        base = provider.runtime.local_base_url
        provider.management.discovery_public_base = base
        provider.runtime.mount_callable(card(), lambda payload: {"done": payload},
                                        service_id="volunteer_echo")
        provider.management.command("/v1/public-service", {"enabled": True})
        buyer = Daemon(tmp_path / "buyer", port=0, protector=protector,
                       public_nodes=[base]).start()
        try:
            status, search = buyer.management.command("/v1/discovery/search", {
                "skill": "echo", "timeout": 0.2})
            assert status == 200 and search["count"] == 1
            assert search["results"][0]["source"] == "public-node"
            found = search["cards"][0]
            assert found["url"] == base + "/a2a/volunteer_echo"
            imported = buyer.runtime.import_agent(found)
            outcome = buyer.runtime.invoke_projection(
                imported.projection_id,
                CallRequest(task_id="from-volunteer-directory", skill="echo", payload="work"))
            assert outcome.ok and outcome.result == {"done": "work"}
            assert outcome.metadata["bilateral_ack_confirmed"] is True
            provider.management.public_directory = lambda *_args, **_kwargs: {
                "cards": [{**found, "name": "forged-name"}], "count": 1}
            assert buyer.management.command("/v1/discovery/search", {
                "skill": "echo"})[1]["count"] == 0, "目录不能替供给方改写签名 Card"
        finally:
            buyer.stop()
    finally:
        provider.stop()


def test_optional_sealed_relay_calls_nat_provider_without_exposing_payload(tmp_path, protector,
                                                                            monkeypatch):
    relay = Daemon(tmp_path / "relay", port=0, protector=protector).start()
    provider = None
    caller = None
    try:
        relay_base = relay.runtime.local_base_url
        relay.management.discovery_public_base = relay_base
        relay.management.command("/v1/public-service", {"enabled": True})
        provider = Daemon(tmp_path / "provider", port=0, protector=protector,
                          relay_node=relay_base).start()
        provider.runtime.mount_callable(
            card(), lambda payload: {"private-result": payload},
            service_id="behind_nat")
        provider.relay_provider.request_refresh()
        deadline = time.monotonic() + 8
        projected = None
        while time.monotonic() < deadline:
            projected = relay.relay_service.card(provider.identity.did, "behind_nat")
            if projected:
                break
            time.sleep(0.05)
        assert projected, provider.relay_provider.last_error
        assert projected["url"] == (relay_base + "/relay/v1/" + provider.identity.did
                                    + "/a2a/behind_nat")
        captured = []
        original_submit = relay.relay_service.submit
        def capture(*args, **kwargs):
            captured.append(json.dumps(args[3]))
            return original_submit(*args, **kwargs)
        monkeypatch.setattr(relay.relay_service, "submit", capture)
        caller = Daemon(tmp_path / "caller", port=0, protector=protector,
                        public_nodes=[relay_base]).start()
        found = caller.management.command("/v1/discovery/search", {
            "skill": "echo"})[1]["cards"]
        assert any(c["url"] == projected["url"] for c in found)
        imported = caller.runtime.import_agent(projected)
        outcome = caller.runtime.invoke_projection(imported.projection_id,
            CallRequest(task_id="relay-private-task", skill="echo",
                        payload="very-private-payload"))
        assert outcome.ok, outcome.error
        assert outcome.result == {"private-result": "very-private-payload"}
        assert outcome.metadata["transport_route"] == "sealed-relay"
        assert outcome.metadata["bilateral_ack_confirmed"] is True
        assert provider.store.task("behind_nat", "relay-private-task")["outcome"][
            "metadata"]["bilateral_ack_confirmed"] is True
        assert captured and "very-private-payload" not in "".join(captured)
        assert "private-result" not in "".join(captured)
        relay.management.command("/v1/public-service", {"enabled": False})
        assert http(relay_base, "/public/v1/agents?skill=echo")[0] == 403
        blocked = caller.runtime.invoke_projection(imported.projection_id,
            CallRequest(task_id="after-relay-disabled", skill="echo", payload="no-delivery"))
        assert not blocked.ok and blocked.metadata["transport_route"] == "sealed-relay"
        assert provider.store.task("behind_nat", "after-relay-disabled") is None
    finally:
        if caller:
            caller.stop()
        if provider:
            provider.stop()
        relay.stop()


def test_remote_volunteer_directory_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        PublicDirectoryClient(["http://example.com"])
    assert PublicDirectoryClient(["http://127.0.0.1:8771"]).bases == (
        "http://127.0.0.1:8771",)


def test_console_evidence_distinguishes_provider_signature_from_bilateral_ack(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        provider = Identity.generate()
        imported = daemon.runtime.import_agent(sign_card(provider, card()))
        scope, task_id = imported.projection_id, "signed-delivery"
        payload, result = {"question": "hello"}, {"answer": "world"}
        receipt = receipt_proof.sign(provider, receipt_proof.make_body(
            task_id=task_id, caller_did=daemon.identity.did,
            provider_did=provider.did, skill="echo",
            input_hash=receipt_proof.hash_payload(payload),
            output_hash=receipt_proof.hash_payload(result)))
        daemon.store.claim(scope, task_id, "fingerprint", {"payload": payload})
        outcome = {"state": "ACCEPTED", "result": result, "receipt": receipt}
        daemon.store.finish(scope, task_id, outcome)
        assert daemon.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_provider_signed"
        outcome["metadata"] = {"bilateral_ack": receipt_proof.ack(daemon.identity, receipt)}
        daemon.store.finish(scope, task_id, outcome)
        assert daemon.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_provider_signed"
        outcome["metadata"]["bilateral_ack_confirmed"] = True
        daemon.store.finish(scope, task_id, outcome)
        assert daemon.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_bilateral_verified"
        outcome["metadata"]["bilateral_ack"]["of"] = "different-receipt"
        daemon.store.finish(scope, task_id, outcome)
        assert daemon.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_provider_signed"
    finally:
        daemon.stop()


def test_two_persistent_nodes_complete_signed_a2a_and_keep_both_receipts(tmp_path, protector):
    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    caller = Daemon(tmp_path / "caller", port=0, protector=protector).start()
    try:
        provider.runtime.mount_callable(card(), lambda payload: {"echo": payload},
                                        service_id="peer_echo")
        projected = provider.runtime.project_binding(
            "peer_echo", public_base=provider.runtime.local_base_url)
        imported = caller.runtime.import_agent(projected)
        task_id = "signed-two-node-call"
        status, rpc = http(caller.runtime.local_base_url,
                           "/a2a/" + imported.projection_id, {
                               "jsonrpc": "2.0", "id": "test", "method": "message/send",
                               "params": {"message": {"messageId": task_id,
                                                      "parts": [{"kind": "data", "data": {"x": 7}}]},
                                          "metadata": {"skill": "echo"},
                                          "configuration": {"blocking": True}}})
        assert status == 200 and "error" not in rpc, rpc
        assert rpc["result"]["artifacts"][0]["parts"][0]["data"] == {"echo": {"x": 7}}
        bought = caller.store.task(imported.projection_id, task_id)["outcome"]
        sold = provider.store.task("peer_echo", task_id)["outcome"]
        assert bought["metadata"]["transport_route"] == "signed-a2a"
        assert bought["metadata"]["bilateral_ack_confirmed"] is True
        assert sold["metadata"]["bilateral_ack_confirmed"] is True
        assert bought["receipt"] == sold["receipt"]
        assert receipt_proof.verify(bought["receipt"])[0]
        assert receipt_proof.verify_ack(sold["metadata"]["bilateral_ack"], sold["receipt"])[0]
        assert sold["metadata"]["witness_claim"] == bought["metadata"]["witness_claim"]
        provider_path = "/a2a/peer_echo"
        def control(method, proof=None):
            params = {"id": task_id}
            if proof is not None:
                params["a2nPeerControl"] = proof
            return http(provider.runtime.local_base_url, provider_path, {
                "jsonrpc": "2.0", "id": "control-test", "method": method,
                "params": params})
        assert control("tasks/get")[0] == 403
        assert control("tasks/cancel")[0] == 403
        get_proof = peer_protocol.sign_control(
            caller.identity, provider_did=provider.identity.did,
            service_id="peer_echo", task_id=task_id, method="tasks/get")
        assert control("tasks/cancel", get_proof)[0] == 403
        assert control("tasks/get", get_proof)[0] == 200
        assert control("tasks/get", get_proof)[0] == 403, "控制签名不得重放"
        stranger = peer_protocol.sign_control(
            Identity.generate(), provider_did=provider.identity.did,
            service_id="peer_echo", task_id=task_id, method="tasks/get")
        assert control("tasks/get", stranger)[0] == 403
        assert caller.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_bilateral_verified"
        assert provider.management.snapshot()["settlements"][0]["evidence_type"] == \
            "receipt_bilateral_verified"
        # The two-party exchange was already complete without a third node.
        # A volunteer can independently witness just the pair's hashes later.
        witness = Daemon(tmp_path / "witness", port=0, protector=protector).start()
        try:
            witness.management.discovery_public_base = witness.runtime.local_base_url
            witness.management.command("/v1/public-service", {"enabled": True})
            status, stamp = http(witness.runtime.local_base_url,
                                 "/public/v1/witness", {
                                     "claim": sold["metadata"]["witness_claim"]})
            assert status == 201
            assert verify_witness(stamp, receipt_hash=receipt_proof.fingerprint(sold["receipt"]))
            assert "echo" not in json.dumps(stamp)
            assert caller.management.snapshot()["settlements"][0]["evidence_type"] == \
                "receipt_bilateral_verified"
        finally:
            witness.stop()
    finally:
        caller.stop()
        provider.stop()


def test_forged_peer_proof_is_rejected_before_agent_runs(tmp_path, protector):
    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    caller = Identity.generate()
    executions = []
    try:
        provider.runtime.mount_callable(card(), lambda payload: executions.append(payload),
                                        service_id="protected_echo")
        message = {"messageId": "forged-task",
                   "parts": [{"kind": "data", "data": {"first": "unchanged"}},
                             {"kind": "text", "text": "original second part"}]}
        proof = peer_protocol.sign_request(
            caller, provider_did=provider.identity.did, skill="echo",
            payload=signed_a2a_input(message, "", {}), task_id="forged-task",
            service_id="protected_echo")
        proof.pop("payload")
        tampered = {**message, "parts": [message["parts"][0],
                                         {"kind": "text", "text": "changed second part"}]}
        status, result = http(provider.runtime.local_base_url, "/a2a/protected_echo", {
            "jsonrpc": "2.0", "id": "forged", "method": "message/send",
            "params": {"message": tampered,
                       "metadata": {"skill": "echo", "a2nPeerRequest": proof}}})
        assert status == 403 and "签名请求无效" in result["error"]
        assert executions == []
    finally:
        provider.stop()


def test_signed_same_task_retry_returns_record_without_second_execution(tmp_path, protector):
    provider = Daemon(tmp_path, port=0, protector=protector).start()
    caller = Identity.generate()
    executions = []
    try:
        provider.runtime.mount_callable(
            card(), lambda payload: executions.append(payload) or {"ok": True},
            service_id="retry_echo")
        message = {"messageId": "same-signed-task",
                   "parts": [{"kind": "text", "text": "once"}]}
        proof = peer_protocol.sign_request(
            caller, provider_did=provider.identity.did, skill="echo",
            payload=signed_a2a_input(message, "", {}),
            task_id="same-signed-task", service_id="retry_echo")
        proof.pop("payload")
        rpc = {"jsonrpc": "2.0", "id": "retry", "method": "message/send",
               "params": {"message": message,
                          "metadata": {"skill": "echo", "a2nPeerRequest": proof}}}
        first = http(provider.runtime.local_base_url, "/a2a/retry_echo", rpc)
        second = http(provider.runtime.local_base_url, "/a2a/retry_echo", rpc)
        assert first[0] == second[0] == 200
        assert first[1]["result"]["id"] == second[1]["result"]["id"]
        assert executions == ["once"]
    finally:
        provider.stop()


def test_plain_upstream_adapter_cannot_spoof_verified_peer_metadata(tmp_path, protector):
    provider = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        provider.runtime.mount_callable(card(), lambda payload: payload,
                                        service_id="plain_upstream")
        status, result = http(provider.runtime.local_base_url,
                              "/_a2n/upstream/plain_upstream/invoke", {
                                  "task_id": "spoofed-peer", "skill": "echo", "payload": "hi",
                                  "metadata": {"_a2n_verified_peer": {
                                      "caller_did": "did:a2n:fake", "input_hash": "fake"}}})
        assert status == 200 and result == "hi"
        assert provider.store.task("plain_upstream", "spoofed-peer")["outcome"]["receipt"] is None
    finally:
        provider.stop()


def test_signed_card_never_downgrades_to_anonymous_a2a(monkeypatch):
    provider, caller = Identity.generate(), Identity.generate()
    projection = {"service_id": "echo", "role": "supply",
                  "node_did": provider.did}
    signed = sign_card(provider, {**card(), "x-a2n": {
        "projection": projection, "peer_protocol": "a2n-bilateral-a2a/1"}})
    class AnonymousRoute:
        def invoke(self, _target, _request):
            raise AssertionError("不能降级为匿名 A2A 调用")

    transport = FallbackTransport([
        ("signed-a2a", SignedA2ATransport(caller)),
        ("direct", AnonymousRoute())])
    target = AgentTarget("peer", signed, signed["url"])
    invalid = transport.invoke(AgentTarget("peer", signed, "http://127.0.0.1:9999/other"),
                               CallRequest(task_id="invalid-route", skill="echo", payload="hi"))
    assert invalid.state == "PROTOCOL_ERROR"
    monkeypatch.setattr("a2n_sdk.upstream.A2AUpstream.invoke",
                        lambda _self, _request: CallResponse.failure(
                            "connect refused", state="UNREACHABLE"))
    unavailable = transport.invoke(target, CallRequest(
        task_id="unavailable", skill="echo", payload="hi"))
    assert unavailable.state == "SIGNED_UNREACHABLE"


def test_peer_control_signature_binds_task_method_and_replay_window():
    provider, caller = Identity.generate(), Identity.generate()
    proof = peer_protocol.sign_control(
        caller, provider_did=provider.did, service_id="one",
        task_id="task-1", method="tasks/get")
    guard = peer_protocol.ReplayGuard()
    assert peer_protocol.verify_control(
        proof, expected_provider=provider.did, guard=guard)[0]
    assert not peer_protocol.verify_control(
        proof, expected_provider=provider.did, guard=guard)[0]
    assert not peer_protocol.verify_control(
        {**proof, "method": "tasks/cancel"}, expected_provider=provider.did)[0]
    assert not peer_protocol.verify_control(
        {**proof, "service_id": "two"}, expected_provider=provider.did)[0]
    assert not peer_protocol.verify_control(
        {**proof, "sig": 5}, expected_provider=provider.did)[0]
    assert not guard.check("nan-clock", float("nan"))[0]


def test_plain_a2a_card_keeps_compatible_direct_path(tmp_path, protector):
    plain = NodeRuntime("did:a2n:plain")
    plain.start_gateway()
    plain.mount_callable(card(), lambda payload: {"plain": payload}, service_id="plain_echo")
    buyer = Daemon(tmp_path / "buyer", port=0, protector=protector).start()
    try:
        imported = buyer.runtime.import_agent(plain.project_binding("plain_echo"))
        status, rpc = http(buyer.runtime.local_base_url,
                           "/a2a/" + imported.projection_id, {
                               "jsonrpc": "2.0", "id": "plain", "method": "message/send",
                               "params": {"message": {"messageId": "plain-task",
                                                      "parts": [{"kind": "text", "text": "hi"}]},
                                          "metadata": {"skill": "echo"}}})
        assert status == 200 and "error" not in rpc
        outcome = buyer.store.task(imported.projection_id, "plain-task")["outcome"]
        assert outcome["result"] == {"plain": "hi"}
        assert outcome["metadata"]["transport_route"] == "direct"
        assert outcome["receipt"] is None
        status, looked_up = http(buyer.runtime.local_base_url,
                                 "/a2a/" + imported.projection_id, {
                                     "jsonrpc": "2.0", "id": "plain-get",
                                     "method": "tasks/get",
                                     "params": {"id": "plain-task"}})
        assert status == 200 and looked_up["result"]["status"]["state"] == "completed"
    finally:
        buyer.stop()
        plain.stop()


def test_signed_peer_receipt_survives_late_tasks_get(tmp_path, protector):
    class AsyncAgent:
        def invoke(self, _request):
            return CallResponse.success(None, state="WORKING",
                                        metadata={"a2a_task_id": "upstream-task"})

        def get_task(self, _task_id, *, context_id=""):
            return CallResponse.success({"ready": True}, state="COMPLETED")

    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    caller = Daemon(tmp_path / "caller", port=0, protector=protector).start()
    try:
        provider.runtime.bindings.add(AgentBinding(
            "async_echo", card(), AsyncAgent(), "local"))
        projected = provider.runtime.project_binding(
            "async_echo", public_base=provider.runtime.local_base_url)
        imported = caller.runtime.import_agent(projected)
        signed_route = next(route.transport for route in
                            caller.runtime.pipeline.transport.routes
                            if route.name == "signed-a2a")
        signed_route.timeout = 0.05  # first call returns a known remote task, then times out
        task_id = "late-signed-task"
        status, first = http(caller.runtime.local_base_url,
                             "/a2a/" + imported.projection_id, {
                                 "jsonrpc": "2.0", "id": "first", "method": "message/send",
                                 "params": {"message": {"messageId": task_id,
                                                        "parts": [{"kind": "text", "text": "go"}]},
                                            "metadata": {"skill": "echo"}}})
        assert status == 200
        assert first["result"]["metadata"]["a2nState"] == "TIMEOUT"
        status, later = http(caller.runtime.local_base_url,
                             "/a2a/" + imported.projection_id, {
                                 "jsonrpc": "2.0", "id": "later", "method": "tasks/get",
                                 "params": {"id": task_id}})
        assert status == 200 and later["result"]["status"]["state"] == "completed"
        bought = caller.store.task(imported.projection_id, task_id)["outcome"]
        sold = provider.store.task("async_echo", task_id)["outcome"]
        assert bought["metadata"]["bilateral_ack_confirmed"] is True
        assert sold["metadata"]["bilateral_ack_confirmed"] is True
        assert bought["receipt"] == sold["receipt"]
    finally:
        caller.stop()
        provider.stop()


def test_signed_peer_cancel_requires_original_caller_and_forwards(tmp_path, protector):
    class CancellableAgent:
        def invoke(self, _request):
            return CallResponse.success(None, state="WORKING",
                                        metadata={"a2a_task_id": "upstream-cancel"})

        def get_task(self, _task_id, *, context_id=""):
            return CallResponse.success(None, state="WORKING",
                                        metadata={"a2a_task_id": "upstream-cancel"})

        def cancel_task(self, _task_id, *, context_id=""):
            return CallResponse.failure("已取消", state="CANCELED",
                                        metadata={"remote_terminal": True})

    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    caller = Daemon(tmp_path / "caller", port=0, protector=protector).start()
    try:
        provider.runtime.bindings.add(AgentBinding(
            "cancel_echo", card(), CancellableAgent(), "local"))
        imported = caller.runtime.import_agent(provider.runtime.project_binding(
            "cancel_echo", public_base=provider.runtime.local_base_url))
        next(route.transport for route in caller.runtime.pipeline.transport.routes
             if route.name == "signed-a2a").timeout = 0.05
        task_id = "signed-cancel-task"
        status, first = http(caller.runtime.local_base_url,
                             "/a2a/" + imported.projection_id, {
                                 "jsonrpc": "2.0", "id": "first", "method": "message/send",
                                 "params": {"message": {"messageId": task_id,
                                                        "parts": [{"kind": "text", "text": "go"}]},
                                            "metadata": {"skill": "echo"}}})
        assert status == 200 and first["result"]["metadata"]["a2nState"] == "TIMEOUT"
        assert http(provider.runtime.local_base_url, "/a2a/cancel_echo", {
            "jsonrpc": "2.0", "id": "stranger", "method": "tasks/cancel",
            "params": {"id": task_id}})[0] == 403
        status, canceled = http(caller.runtime.local_base_url,
                                "/a2a/" + imported.projection_id, {
                                    "jsonrpc": "2.0", "id": "cancel", "method": "tasks/cancel",
                                    "params": {"id": task_id}})
        assert status == 200 and canceled["result"]["status"]["state"] == "canceled"
        assert caller.store.task(imported.projection_id, task_id)["outcome"]["metadata"][
            "cancel_acknowledged"] is True
        assert provider.store.task("cancel_echo", task_id)["outcome"]["state"] == "CANCELED"
    finally:
        caller.stop()
        provider.stop()


def test_pairing_sessions_are_origin_bound_single_use_and_revocable(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base, origin = daemon.runtime.local_base_url, "http://127.0.0.1:18787"
        code = daemon.pairing.new_code()
        status, pair = http(base, "/v1/pairing", {"code": code}, origin=origin)
        assert status == 200
        assert http(base, "/v1/pairing", {"code": code}, origin=origin)[0] == 403
        assert http(base, "/v1/runtime", token=pair["token"], origin=origin)[0] == 200
        assert http(base, "/v1/runtime", token=pair["token"],
                    extra={"Referer": origin + "/console"})[0] == 200
        assert http(base, "/v1/runtime", token=pair["token"], origin="http://127.0.0.1:9000")[0] == 401
        assert http(base, "/v1/runtime", token=pair["token"], origin="https://attacker.example")[0] == 403
        assert http(base, "/v1/runtime", token=pair["token"], origin=origin,
                    extra={"Host": "rebound.example"})[0] == 403
        assert http(base, "/v1/disconnect", {}, pair["token"], origin)[0] == 200
        assert http(base, "/v1/runtime", token=pair["token"], origin=origin)[0] == 401
    finally:
        daemon.stop()


def test_local_console_bootstraps_its_own_same_site_management_session(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        jar = CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
        with opener.open(base + "/console", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
        assert "连接这台电脑的节点" not in html
        assert any(cookie.name == "A2N_LOCAL_TOKEN" for cookie in jar)
        request = urllib.request.Request(
            base + "/v1/runtime", headers={"Referer": base + "/console"})
        with opener.open(request, timeout=5) as response:
            snapshot = json.loads(response.read())
        assert snapshot["node_did"] == daemon.identity.did

        # The cookie remains host-only and does not weaken the explicit remote
        # origin pairing contract tested above.
        assert http(base, "/v1/runtime")[0] == 401
    finally:
        daemon.stop()


def test_pair_code_expires_and_locks_after_five_wrong_attempts():
    now = [100.0]
    pairing = PairingService(clock=lambda: now[0])
    code = pairing.new_code()
    for _ in range(5):
        with pytest.raises(PermissionError):
            pairing.exchange("wrong", "")
    with pytest.raises(PermissionError):
        pairing.exchange(code, "")
    code = pairing.new_code()
    now[0] += 301
    with pytest.raises(PermissionError):
        pairing.exchange(code, "")


def test_concurrent_http_retries_execute_once_and_scope_task_lookup():
    runtime = NodeRuntime("did:a2n:test")
    started, release = threading.Event(), threading.Event()
    count = []

    def work(payload):
        count.append(payload)
        started.set()
        assert release.wait(4)
        return {"echo": payload}

    runtime.mount_callable(card(), work, service_id="echo")
    runtime.mount_callable(card(), lambda x: x, service_id="other")
    runtime.start_gateway()
    message = {"jsonrpc": "2.0", "id": "rpc", "method": "message/send", "params": {
        "message": {"messageId": "once", "parts": [{"kind": "data", "data": {"x": 1}}]},
        "metadata": {"skill": "echo"}, "configuration": {"blocking": False}}}
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: http(runtime.local_base_url, "/a2a/echo", message), range(10)))
        assert started.wait(1)
        assert all(r[1]["result"]["status"]["state"] == "working" for r in results)
        assert len(count) == 1
        lookup = {"jsonrpc": "2.0", "id": "get", "method": "tasks/get", "params": {"id": "once"}}
        assert "error" in http(runtime.local_base_url, "/a2a/other", lookup)[1]
        changed = json.loads(json.dumps(message))
        changed["params"]["message"]["parts"][0]["data"] = {"x": 2}
        assert "error" in http(runtime.local_base_url, "/a2a/echo", changed)[1]
        release.set()
        message["params"]["configuration"]["blocking"] = True
        final = http(runtime.local_base_url, "/a2a/echo", message)[1]["result"]
        assert final["status"]["state"] == "completed" and len(count) == 1
    finally:
        release.set()
        runtime.stop()


def test_restart_replays_completed_result_and_never_reexecutes_unknown_task(tmp_path, protector):
    path = tmp_path / "calls.db"
    store = LocalStore(path, protector)
    calls = []
    from a2n_sdk.ports import CallOutcome

    def execute(scope, request):
        calls.append(request.task_id)
        return CallOutcome(ok=True, state="ACCEPTED", task_id=request.task_id, result={"value": 1})

    service = CallService(execute, store)
    request = CallRequest(task_id="done", payload={})
    assert service.invoke("echo", request).ok
    store.claim("echo", "unknown", "hash")
    service.stop()
    store.close()
    store = LocalStore(path, protector)
    service = CallService(execute, store)
    try:
        assert service.invoke("echo", request).result == {"value": 1}
        assert calls == ["done"]
        assert service.get("echo", "unknown").state == "INTERRUPTED"
    finally:
        service.stop()
        store.close()


def test_persisted_request_allows_safe_remote_refresh_after_restart(tmp_path, protector):
    path = tmp_path / "remote-refresh.db"
    request = CallRequest(task_id="resume-after-restart",
                          payload={"private": "sealed-value"})

    def timed_out(_scope, req):
        from a2n_sdk.ports import CallOutcome
        return CallOutcome(
            ok=False, task_id=req.task_id, state="TIMEOUT",
            metadata={"a2a_task_id": "remote-after-restart",
                      "a2a_context_id": "remote-context"})

    first_store = LocalStore(path, protector)
    first = CallService(timed_out, first_store)
    assert first.invoke("projection", request).state == "TIMEOUT"
    first.stop()
    first_store.close()
    assert b"sealed-value" not in path.read_bytes()

    refreshed_requests = []

    def refresh(scope, req, current):
        from a2n_sdk.ports import CallOutcome
        refreshed_requests.append((scope, req.payload, current.metadata["a2a_task_id"]))
        return CallOutcome(ok=True, task_id=req.task_id, state="COMPLETED",
                           result={"recovered": True}, target_ref=scope,
                           metadata={"a2a_task_id": "remote-after-restart",
                                     "remote_terminal": True})

    second_store = LocalStore(path, protector)
    second = CallService(lambda *_args: None, second_store, refresh_remote=refresh)
    try:
        outcome = second.get("projection", request.task_id, refresh_remote=True)
        assert outcome.state == "COMPLETED"
        assert outcome.result == {"recovered": True}
        assert refreshed_requests == [
            ("projection", {"private": "sealed-value"}, "remote-after-restart")]
    finally:
        second.stop()
        second_store.close()


def test_timeout_and_parse_failure_never_retry_or_settle_pending_delivery():
    routes = []
    class Broken:
        def invoke(self, *_):
            routes.append("direct")
            raise TimeoutError("response lost after execution")
    class Backup:
        def invoke(self, *_):
            routes.append("backup")
            return CallResponse.success("duplicate")
    response = FallbackTransport([("direct", Broken()), ("backup", Backup())]).invoke(
        AgentTarget("x", card()), CallRequest())
    assert response.state == "TIMEOUT" and routes == ["direct"]
    class Pending:
        def invoke(self, *_):
            return CallResponse.success(None, state="WORKING")
    class Never:
        def evaluate(self, *_):
            raise AssertionError("pending delivery must not be accepted")
        def settle(self, *_):
            raise AssertionError("pending delivery must not be settled")
    out = CallPipeline(Pending(), Never(), Never()).invoke(AgentTarget("x", card()), CallRequest())
    assert out.state == "WORKING" and not out.verdict and not out.settlement


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_windows_dpapi_roundtrip_and_tamper_detection():
    protector = WindowsProtector()
    raw = b"not-a-real-key"
    sealed = protector.seal(raw)
    assert raw not in sealed and protector.open(sealed) == raw
    with pytest.raises(OSError):
        protector.open(sealed[:-1])


def test_node_home_cannot_be_opened_by_two_daemons(tmp_path, protector):
    first = Daemon(tmp_path, port=0, protector=protector)
    try:
        with pytest.raises(RuntimeError, match="运行中的实例"):
            Daemon(tmp_path, port=0, protector=protector)
    finally:
        first.stop()


def test_partial_sovereign_claim_cannot_be_downgraded_to_unsigned(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        forged = card()
        forged["x-a2n"] = {"sovereign": {
            "did": "did:a2n:ag_someone_else", "pub": "not-a-key"}}
        with pytest.raises(ValueError, match="原始卡片验签失败"):
            daemon.runtime.import_agent(forged)
    finally:
        daemon.stop()
