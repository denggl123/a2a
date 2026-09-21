"""Persistent local node contracts: restart, origin isolation and single execution."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from a2n_node.card import verify_card
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk import (AgentTarget, CallPipeline, CallRequest, CallResponse,
                     FallbackTransport, NodeRuntime)
from a2n_sdk.calls import CallService
from a2n_sdk.pairing import PairingService
from a2n_sdk.protection import WindowsProtector
from a2n_sdk.storage import LocalStore


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


def test_pairing_sessions_are_origin_bound_single_use_and_revocable(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base, origin = daemon.runtime.local_base_url, "http://127.0.0.1:8000"
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
