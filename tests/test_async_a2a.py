"""Long-running A2A calls remain identifiable, bounded and cancelable."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.request

from a2n_sdk import (AgentTarget, CallPipeline, CallRequest, CallResponse,
                     FallbackTransport, NodeRuntime)
from a2n_sdk.calls import CallService
from a2n_sdk.ports import CallOutcome
from a2n_sdk.storage import LocalStore
from a2n_sdk.upstream import A2AUpstream
import a2n_sdk.upstream as upstream_module


class FakeA2A:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                rpc = json.loads(self.rfile.read(length))
                outer.requests.append(rpc)
                result = outer.responder(rpc, len(outer.requests))
                raw = json.dumps({"jsonrpc": "2.0", "id": rpc.get("id"),
                                  "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}/a2a"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def rpc(base, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": f"rpc-{method}",
                       "method": method, "params": params}).encode()
    request = urllib.request.Request(base, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=2) as response:
        return json.loads(response.read())


def test_upstream_polls_to_completion_and_preserves_remote_identity():
    states = iter(["working", "completed"])

    def respond(request, _number):
        if request["method"] == "message/send":
            return {"kind": "task", "id": "remote-42", "contextId": "remote-context",
                    "status": {"state": "submitted"}}
        state = next(states)
        task = {"kind": "task", "id": "remote-42", "status": {"state": state}}
        if state == "completed":
            task["artifacts"] = [{"parts": [{"kind": "data", "data": {"answer": 42}}]}]
        return task

    with FakeA2A(respond) as fake:
        result = A2AUpstream(fake.url, timeout=1, poll_interval=0.005,
                             use_system_proxy=False).invoke(
            CallRequest(task_id="local-7", context_id="local-context", payload={"q": 1}))

    assert result.ok and result.state == "COMPLETED"
    assert result.result == {"answer": 42}
    assert result.metadata["a2a_task_id"] == "remote-42"
    assert result.metadata["a2a_context_id"] == "remote-context"
    assert [item["method"] for item in fake.requests] == [
        "message/send", "tasks/get", "tasks/get"]
    assert fake.requests[0]["params"]["contextId"] == "local-context"
    assert all(item["params"]["id"] == "remote-42" for item in fake.requests[1:])


def test_upstream_reports_remote_failure_and_bounded_timeout():
    def failed(request, _number):
        state = "working" if request["method"] == "message/send" else "failed"
        return {"kind": "task", "id": "remote-fail", "contextId": "ctx",
                "status": {"state": state},
                **({"error": {"message": "boom"}} if state == "failed" else {})}

    with FakeA2A(failed) as fake:
        result = A2AUpstream(fake.url, timeout=1, poll_interval=0,
                             use_system_proxy=False).invoke(CallRequest(task_id="failure"))
    assert not result.ok and result.state == "FAILED"
    assert result.error == {"message": "boom"}
    assert result.metadata["a2a_task_id"] == "remote-fail"

    def never_done(_request, _number):
        return {"kind": "task", "id": "remote-slow", "contextId": "slow-context",
                "status": {"state": "working"}}

    started = time.monotonic()
    with FakeA2A(never_done) as fake:
        result = A2AUpstream(fake.url, timeout=0.08, poll_interval=0.01,
                             use_system_proxy=False).invoke(CallRequest(task_id="slow"))
    elapsed = time.monotonic() - started
    assert not result.ok and result.state == "TIMEOUT"
    assert result.metadata["a2a_task_id"] == "remote-slow"
    assert result.metadata["a2a_context_id"] == "slow-context"
    assert result.metadata["remote_terminal"] is False
    assert result.metadata["replay_safe"] is False
    assert result.metadata["same_task_replay"] == "returns_recorded_outcome"
    assert elapsed < 1


def test_upstream_returns_caller_action_states_without_polling():
    def action_required(request, _number):
        state = "input-required" if request["id"] == "needs-input" else "auth-required"
        return {"kind": "task", "id": f"remote-{request['id']}",
                "contextId": f"context-{request['id']}",
                "status": {"state": state},
                "artifacts": [{"parts": [{"kind": "text", "text": "请继续"}]}]}

    with FakeA2A(action_required) as fake:
        needs_input = A2AUpstream(fake.url, timeout=0.05, poll_interval=0.01,
                                  use_system_proxy=False).invoke(
            CallRequest(task_id="needs-input"))
        needs_auth = A2AUpstream(fake.url, timeout=0.05, poll_interval=0.01,
                                 use_system_proxy=False).invoke(
            CallRequest(task_id="needs-auth"))

    assert needs_input.ok and needs_input.state == "INPUT-REQUIRED"
    assert needs_auth.ok and needs_auth.state == "AUTH-REQUIRED"
    assert needs_input.metadata["a2a_task_id"] == "remote-needs-input"
    assert needs_auth.metadata["a2a_context_id"] == "context-needs-auth"
    assert [item["method"] for item in fake.requests] == ["message/send", "message/send"]


def test_local_binding_preserves_remote_caller_action_state_and_metadata():
    runtime = NodeRuntime("did:a2n:action-state")
    runtime.mount_callable(
        {"name": "Needs input", "url": "http://127.0.0.1/unused",
         "skills": [{"id": "clarify", "name": "Clarify"}]},
        lambda _payload: CallResponse.success(
            {"question": "请补充格式"}, state="INPUT-REQUIRED",
            metadata={"a2a_task_id": "remote-waiting"}),
        service_id="needs_input",
    )
    outcome = runtime.invoke_local_id(
        "needs_input", CallRequest(task_id="local-waiting", payload={"q": 1}))
    assert outcome.ok is True
    assert outcome.state == "INPUT-REQUIRED"
    assert outcome.metadata["a2a_task_id"] == "remote-waiting"


def test_local_gateway_exposes_remote_context_and_action_message():
    def action_required(_request, _number):
        return {"kind": "task", "id": "remote-action",
                "contextId": "remote-conversation",
                "status": {"state": "input-required",
                           "message": "请补充输出格式"}}

    with FakeA2A(action_required) as fake:
        runtime = NodeRuntime("did:a2n:gateway-action")
        runtime.mount_http(
            {"name": "Remote", "url": fake.url,
             "skills": [{"id": "ask", "name": "Ask"}]},
            fake.url, protocol="a2a", service_id="remote_action", timeout=1)
        runtime.start_gateway()
        try:
            result = rpc(runtime.local_base_url + "/a2a/remote_action", "message/send", {
                "message": {"messageId": "local-action",
                            "parts": [{"kind": "text", "text": "开始"}]},
            })["result"]
            assert result["id"] == "local-action"
            assert result["contextId"] == "remote-conversation"
            assert result["status"] == {
                "state": "input-required", "message": "请补充输出格式"}
            assert result["metadata"]["network"]["a2a_task_id"] == "remote-action"
        finally:
            runtime.stop()


def test_a2a_http_server_error_is_delivery_unknown(monkeypatch):
    monkeypatch.setattr(
        upstream_module, "_http_json",
        lambda *_args, **_kwargs: (502, {"error": "temporary gateway failure"}))
    outcome = A2AUpstream("https://agent.invalid/a2a", timeout=0.2).invoke(
        CallRequest(task_id="unknown-delivery"))
    assert outcome.state == "DELIVERY_UNKNOWN"
    assert outcome.metadata["remote_effect_unknown"] is True
    assert outcome.metadata["remote_terminal"] is False


def test_pipeline_persists_failure_metadata_and_same_id_never_resends_timeout():
    class TimeoutTransport:
        def __init__(self):
            self.count = 0

        def invoke(self, _target, _request):
            self.count += 1
            return CallResponse.failure(
                "still working remotely", state="TIMEOUT",
                metadata={"a2a_task_id": "remote-timeout",
                          "a2a_context_id": "remote-context"})

    transport = TimeoutTransport()
    pipeline = CallPipeline(transport)
    target = AgentTarget("remote-agent", {"name": "Remote"})
    store = LocalStore()
    service = CallService(lambda _scope, request: pipeline.invoke(target, request), store)
    request = CallRequest(task_id="stable-timeout")
    try:
        first = service.invoke("projection", request)
        replay = service.invoke("projection", request)
        assert transport.count == 1
        assert first.state == replay.state == "TIMEOUT"
        assert replay.metadata["a2a_task_id"] == "remote-timeout"
        assert replay.metadata["a2a_context_id"] == "remote-context"
        assert replay.metadata["remote_terminal"] is False
        assert replay.metadata["replay_safe"] is False
        assert replay.metadata["same_task_replay"] == "returns_recorded_outcome"
    finally:
        service.stop()
        store.close()


def test_acceptance_rejection_preserves_remote_task_identity():
    class Delivered:
        def invoke(self, _target, _request):
            return CallResponse.success(
                {"draft": True},
                metadata={"a2a_task_id": "remote-rejected",
                          "a2a_context_id": "remote-context"})

    class Reject:
        def evaluate(self, _target, _request, _response):
            return {"passed": False, "reasons": ["格式不符"]}

    outcome = CallPipeline(Delivered(), acceptance=Reject()).invoke(
        AgentTarget("remote", {"name": "Remote"}),
        CallRequest(task_id="local-rejected"))
    assert outcome.state == "REJECTED"
    assert outcome.metadata["a2a_task_id"] == "remote-rejected"


def test_call_service_cancels_queued_and_running_without_late_overwrite():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    executed = []

    def execute(scope, request):
        executed.append(request.task_id)
        if request.task_id == "running":
            started.set()
            release.wait(2)
            finished.set()
        return CallOutcome(ok=True, task_id=request.task_id, state="COMPLETED",
                           result=scope)

    store = LocalStore()
    store.claim("agent", "interrupted", "prior-process")
    service = CallService(execute, store, workers=1)
    try:
        assert service.cancel("agent", "interrupted").state == "INTERRUPTED"
        assert service.invoke("agent", CallRequest(task_id="running"),
                              blocking=False).state == "WORKING"
        assert started.wait(1)
        assert service.invoke("agent", CallRequest(task_id="queued"),
                              blocking=False).state == "WORKING"

        queued = service.cancel("agent", "queued")
        running = service.cancel("agent", "running")
        assert queued.state == "CANCELED" and not queued.metadata["remote_effect_unknown"]
        assert running.state == "CANCEL_REQUESTED" and running.ok
        assert running.metadata["cancel_requested"] is True
        assert running.metadata["remote_effect_unknown"] is True
        assert "可能仍在执行" in running.metadata["cancel_note"]
        # Queued cancellation is final. Running cancellation is only a request;
        # the eventual execution/settlement truth must replace the interim view.
        assert service.invoke("agent", CallRequest(task_id="queued")).state == "CANCELED"
        release.set()
        assert finished.wait(1)
        final = service.get("agent", "running")
        assert final.state == "COMPLETED"
        assert final.metadata["cancel_requested"] is True
        assert final.metadata["cancel_acknowledged"] is False
        assert service.get("agent", "queued").state == "CANCELED"
        assert executed == ["running"]
    finally:
        release.set()
        service.stop()
        store.close()


def test_restart_turns_orphan_cancel_request_into_honest_interrupted_record():
    store = LocalStore()
    store.claim("agent", "cancel-crash", "fingerprint")
    store.finish("agent", "cancel-crash", CallOutcome(
        ok=True, task_id="cancel-crash", state="CANCEL_REQUESTED",
        target_ref="agent", metadata={"cancel_requested": True,
                                       "remote_effect_unknown": True},
    ).to_dict())
    service = CallService(lambda _scope, _request: None, store)
    try:
        recovered = service.get("agent", "cancel-crash")
        assert recovered.state == "INTERRUPTED"
        assert not recovered.ok
        assert recovered.metadata["cancel_requested"] is True
        assert recovered.metadata["cancel_acknowledged"] is False
        assert recovered.metadata["same_task_replay"] == "returns_recorded_outcome"
    finally:
        service.stop()
        store.close()


def test_unserializable_result_becomes_terminal_failure_not_forever_working():
    store = LocalStore()
    service = CallService(lambda _scope, request: CallOutcome(
        ok=True, task_id=request.task_id, state="COMPLETED", result=object()), store)
    try:
        outcome = service.invoke("agent", CallRequest(task_id="bad-result"))
        assert outcome.state == "FAILED"
        assert outcome.metadata["stage"] == "persistence"
        assert store.task("agent", "bad-result")["state"] == "FAILED"
    finally:
        service.stop()
        store.close()


def test_local_gateway_exposes_a2a_tasks_cancel():
    started = threading.Event()
    release = threading.Event()

    def work(payload):
        started.set()
        release.wait(2)
        return payload

    runtime = NodeRuntime("did:a2n:cancel-test")
    runtime.mount_callable(
        {"name": "Slow", "url": "http://127.0.0.1/unused",
         "skills": [{"id": "slow", "name": "Slow"}]},
        work, service_id="slow")
    runtime.start_gateway()
    try:
        sent = rpc(runtime.local_base_url + "/a2a/slow", "message/send", {
            "message": {"messageId": "cancel-me", "parts": [{"kind": "text", "text": "go"}]},
            "contextId": "conversation-9", "configuration": {"blocking": False}})
        assert sent["result"]["status"]["state"] == "working"
        assert started.wait(1)

        canceled = rpc(runtime.local_base_url + "/a2a/slow", "tasks/cancel",
                       {"id": "cancel-me"})["result"]
        assert canceled["status"]["state"] == "working"
        assert canceled["contextId"] == "conversation-9"
        assert canceled["metadata"]["a2nState"] == "CANCEL_REQUESTED"
        assert canceled["metadata"]["cancel_requested"] is True
        assert canceled["metadata"]["remote_effect_unknown"] is True
        release.set()
        time.sleep(0.02)
        looked_up = rpc(runtime.local_base_url + "/a2a/slow", "tasks/get",
                        {"id": "cancel-me"})["result"]
        assert looked_up["status"]["state"] == "completed"
        assert looked_up["metadata"]["cancel_requested"] is True
        assert looked_up["contextId"] == "conversation-9"
    finally:
        release.set()
        runtime.stop()


def test_timed_out_remote_task_can_be_refreshed_without_resending_message():
    def respond(request, _number):
        if request["method"] == "message/send":
            return {"kind": "task", "id": "remote-refresh", "contextId": "ctx-refresh",
                    "status": {"state": "working"}}
        assert request["method"] == "tasks/get"
        return {"kind": "task", "id": "remote-refresh", "contextId": "ctx-refresh",
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "data", "data": {"done": True}}]}]}

    with FakeA2A(respond) as fake:
        runtime = NodeRuntime("did:a2n:remote-refresh")
        runtime.mount_http(
            {"name": "Remote", "url": fake.url,
             "skills": [{"id": "slow", "name": "Slow"}]},
            fake.url, protocol="a2a", service_id="remote", timeout=0.03)
        runtime.start_gateway()
        try:
            sent = rpc(runtime.local_base_url + "/a2a/remote", "message/send", {
                "message": {"messageId": "local-refresh",
                            "parts": [{"kind": "text", "text": "go"}]}})["result"]
            assert sent["metadata"]["a2nState"] == "TIMEOUT"

            refreshed = rpc(runtime.local_base_url + "/a2a/remote", "tasks/get",
                            {"id": "local-refresh"})["result"]
            assert refreshed["status"]["state"] == "completed"
            assert refreshed["artifacts"][0]["parts"][0]["data"] == {"done": True}
            assert refreshed["metadata"]["network"]["a2a_task_id"] == "remote-refresh"
            assert refreshed["metadata"]["network"]["remote_refreshed"] is True
            assert [item["method"] for item in fake.requests].count("message/send") == 1
            assert fake.requests[-1]["method"] == "tasks/get"
        finally:
            runtime.stop()


def test_timed_out_remote_task_cancel_is_forwarded_and_acknowledged():
    def respond(request, _number):
        if request["method"] == "message/send":
            return {"kind": "task", "id": "remote-cancel", "contextId": "ctx-cancel",
                    "status": {"state": "working"}}
        assert request["method"] == "tasks/cancel"
        return {"kind": "task", "id": "remote-cancel", "contextId": "ctx-cancel",
                "status": {"state": "canceled"}}

    with FakeA2A(respond) as fake:
        runtime = NodeRuntime("did:a2n:remote-cancel")
        runtime.mount_http(
            {"name": "Remote", "url": fake.url,
             "skills": [{"id": "slow", "name": "Slow"}]},
            fake.url, protocol="a2a", service_id="remote", timeout=0.03)
        runtime.start_gateway()
        try:
            sent = rpc(runtime.local_base_url + "/a2a/remote", "message/send", {
                "message": {"messageId": "local-cancel-remote",
                            "parts": [{"kind": "text", "text": "go"}]}})["result"]
            assert sent["metadata"]["a2nState"] == "TIMEOUT"
            canceled = rpc(runtime.local_base_url + "/a2a/remote", "tasks/cancel",
                           {"id": "local-cancel-remote"})["result"]
            assert canceled["status"]["state"] == "canceled"
            assert canceled["metadata"]["cancel_acknowledged"] is True
            assert canceled["metadata"]["remote_effect_unknown"] is False
            assert [item["method"] for item in fake.requests].count("message/send") == 1
            assert fake.requests[-1]["method"] == "tasks/cancel"
        finally:
            runtime.stop()


def test_imported_projection_refresh_uses_normal_acceptance_and_settlement_pipeline():
    evaluated = []

    class Accept:
        def evaluate(self, target, request, response):
            evaluated.append((target.ref, request.task_id, response.result))
            return {"passed": True, "policy": "test", "quality_measured": True}

    def respond(request, _number):
        if request["method"] == "message/send":
            return {"kind": "task", "id": "remote-projection",
                    "status": {"state": "working"}}
        return {"kind": "task", "id": "remote-projection",
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": "finished"}]}]}

    with FakeA2A(respond) as fake:
        runtime = NodeRuntime("did:a2n:projection-refresh", acceptance=Accept())
        runtime.start_gateway()
        item = runtime.import_agent({
            "name": "Remote", "url": fake.url, "version": "1.0.0",
            "skills": [{"id": "slow", "name": "Slow"}]})
        # Use a short direct transport budget so the first local wait expires.
        runtime.pipeline.transport.routes[0].transport.timeout = 0.03
        try:
            sent = rpc(runtime.local_base_url + f"/a2a/{item.projection_id}",
                       "message/send", {
                           "message": {"messageId": "projection-local",
                                       "parts": [{"kind": "text", "text": "go"}]}})["result"]
            assert sent["metadata"]["a2nState"] == "TIMEOUT"
            assert evaluated == []
            refreshed = rpc(runtime.local_base_url + f"/a2a/{item.projection_id}",
                            "tasks/get", {"id": "projection-local"})["result"]
            assert refreshed["metadata"]["a2nState"] == "ACCEPTED"
            assert refreshed["artifacts"][0]["parts"][0]["text"] == "finished"
            assert refreshed["metadata"]["acceptance"]["policy"] == "test"
            assert evaluated == [(item.target.ref, "projection-local", "finished")]
            assert [item["method"] for item in fake.requests].count("message/send") == 1
        finally:
            runtime.stop()


def test_fallback_task_control_stays_on_original_route():
    calls = []

    class Route:
        def __init__(self, name):
            self.name = name

        def invoke(self, _target, _request):
            calls.append((self.name, "invoke"))
            return CallResponse.failure(
                "remote still running", state="TIMEOUT",
                metadata={"a2a_task_id": "remote-route"})

        def get_task(self, _target, remote_id, **_kwargs):
            calls.append((self.name, "get", remote_id))
            return CallResponse.success({"route": self.name})

    transport = FallbackTransport([("direct", Route("direct")),
                                   ("relay", Route("relay"))])
    target = AgentTarget("remote", {"name": "Remote"})
    response = transport.invoke(target, CallRequest(task_id="route-task"))
    assert response.metadata["transport_route"] == "direct"
    refreshed = transport.get_task(
        target, "remote-route", route_name=response.metadata["transport_route"])
    assert refreshed.result == {"route": "direct"}
    assert calls == [("direct", "invoke"), ("direct", "get", "remote-route")]
    unknown = transport.get_task(target, "remote-route", route_name="missing")
    assert unknown.state == "ROUTE_UNKNOWN"


def test_call_service_stop_is_bounded_for_stuck_callable_and_result_stays_interrupted():
    started = threading.Event()
    release = threading.Event()

    def stuck(_scope, request):
        started.set()
        release.wait(5)
        return CallOutcome(ok=True, task_id=request.task_id,
                           state="COMPLETED", result="too late")

    store = LocalStore()
    service = CallService(stuck, store, workers=1, stop_timeout=0.02)
    try:
        service.invoke("agent", CallRequest(task_id="stuck"), blocking=False)
        assert started.wait(1)
        before = time.monotonic()
        service.stop()
        assert time.monotonic() - before < 0.3
        assert service.get("agent", "stuck").state == "INTERRUPTED"
        release.set()
        time.sleep(0.03)
        assert service.get("agent", "stuck").state == "INTERRUPTED"
    finally:
        release.set()
        service.stop()
        store.close()


def test_failed_remote_cancel_does_not_relabel_the_task_as_failed():
    def invoke(_scope, request):
        return CallOutcome(
            ok=False, task_id=request.task_id, state="TIMEOUT",
            metadata={"a2a_task_id": "remote-uncertain",
                      "remote_terminal": False})

    def cancel_remote(_scope, request, _current):
        return CallOutcome(
            ok=False, task_id=request.task_id, state="DELIVERY_UNKNOWN",
            error="cancel response lost",
            metadata={"rpc_method": "tasks/cancel", "remote_terminal": False})

    store = LocalStore()
    service = CallService(invoke, store, cancel_remote=cancel_remote)
    try:
        assert service.invoke("agent", CallRequest(task_id="uncertain")).state == "TIMEOUT"
        outcome = service.cancel("agent", "uncertain")
        assert outcome.state == "TIMEOUT"
        assert outcome.metadata["cancel_requested"] is True
        assert outcome.metadata["cancel_acknowledged"] is False
        assert outcome.metadata["remote_effect_unknown"] is True
        assert outcome.metadata["remote_cancel_error"] == "cancel response lost"
    finally:
        service.stop()
        store.close()


def test_delivery_unknown_is_not_reported_as_a_business_failure_on_the_wire(monkeypatch):
    monkeypatch.setattr(
        upstream_module, "_http_json",
        lambda *_args, **_kwargs: (502, {"error": "temporary gateway failure"}))
    runtime = NodeRuntime("did:a2n:wire-unknown")
    runtime.mount_http(
        {"name": "Flaky", "url": "http://agent.invalid/a2a",
         "skills": [{"id": "flaky", "name": "Flaky"}]},
        "http://agent.invalid/a2a", protocol="a2a", service_id="flaky", timeout=0.2)
    runtime.start_gateway()
    try:
        sent = rpc(runtime.local_base_url + "/a2a/flaky", "message/send", {
            "message": {"messageId": "wire-unknown",
                        "parts": [{"kind": "text", "text": "go"}]}})["result"]
        assert sent["status"]["state"] == "unknown"
        view = rpc(runtime.local_base_url + "/a2a/flaky", "tasks/get",
                   {"id": "wire-unknown"})["result"]
        # 线上状态必须与 a2nState 同口径：交付未知 ≠ 业务失败。
        assert view["status"]["state"] == "unknown"
        assert view["metadata"]["a2nState"] == "DELIVERY_UNKNOWN"
        assert view["metadata"]["remote_effect_unknown"] is True
    finally:
        runtime.stop()
