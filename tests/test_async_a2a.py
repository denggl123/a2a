"""Long-running A2A calls remain identifiable, bounded and cancelable."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.request

from a2n_sdk import (AgentTarget, CallPipeline, CallRequest, CallResponse,
                     NodeRuntime)
from a2n_sdk.calls import CallService
from a2n_sdk.ports import CallOutcome
from a2n_sdk.storage import LocalStore
from a2n_sdk.upstream import A2AUpstream


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
