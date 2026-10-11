"""Authenticated client for the single local node management and A2A API."""
from __future__ import annotations
import ipaddress
import json
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote, urlsplit, urlencode
from .serialization import strict_object, strict_value
from .ports import CallOutcome
from .task_view import task_view

class NodeRequestError(RuntimeError):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"node HTTP {status}: {body}")

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class SafeRetryError(NodeRequestError):
    """The node proved that execution never reached the remote Agent."""
    safe_to_retry = True

class NodeClient:
    def __init__(self, base_url="http://127.0.0.1:8771", *, token=None, timeout=60):
        parsed = urlsplit(base_url)
        try:
            loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            parsed.port
        except (ValueError, TypeError):
            loopback = False
        if (not loopback or parsed.scheme != "http" or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("NodeClient 必须连接本机 HTTP 回环入口")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, method, path, body=None, *, headers=None):
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise ValueError("请求路径必须是本机绝对路径")
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        hs = {"Content-Type": "application/json", **(headers or {})}
        if self.token:
            hs["X-A2N-Local-Token"] = self.token
        req = urllib.request.Request(self.base_url + path, data=data, headers=hs, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise NodeRequestError(502, {"error": "response_too_large"})
                return strict_value(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read(65536)
            try:
                error = strict_object(raw)
            except ValueError:
                error = {"error": "invalid_response"}
            raise NodeRequestError(exc.code, error) from exc

    def snapshot(self):
        return self.request("GET", "/v1/runtime")

    def query_experience(self, subject, *, budget=None, command_id=None):
        return self.request("POST", "/v1/experience/queries", {"subject": subject, "budget": budget or {}},
                            headers={"Idempotency-Key": command_id or uuid.uuid4().hex})

    def experience_query(self, query_id):
        return self.request("GET", "/v1/experience/queries/" + quote(query_id, safe=""))

    def publish_feedback(self, feedback_id, *, public_note="", visibility="PUBLIC"):
        return self.request("POST", "/v1/feedback/publications", {"feedback_id": feedback_id,
            "public_note": public_note, "visibility": visibility})

    def rebuild_reputation(self, subject, *, current_version="", config=None, command_id=None):
        return self.request("POST", "/v1/reputation/rebuild", {"subject": subject,
            "current_version": current_version, "config": config or {}},
            headers={"Idempotency-Key": command_id or uuid.uuid4().hex})

    def reputations(self):
        return self.request("GET", "/v1/reputation")

    def mount(self, card, endpoint, *, protocol="json", service_id=None, listed=True):
        body = {"card": card, "endpoint": endpoint, "protocol": protocol, "listed": listed}
        if service_id:
            body["service_id"] = service_id
        return self.request("POST", "/v1/bindings/http", body)

    def publish(self, service_id):
        return self.request("POST", "/v1/publish", {"service_id": service_id})

    def unpublish(self, service_id):
        return self.request("POST", "/v1/unpublish", {"service_id": service_id})

    def search(self, spec, *, command_id=None):
        return self.request("POST", "/v1/coord/searches", spec,
                            headers={"Idempotency-Key": command_id or uuid.uuid4().hex})

    def import_agent(self, card, **selection):
        return self.request("POST", "/v1/projections", {"card": card, **selection})

    def rpc(self, scope, method, params):
        result = self.request("POST", "/a2a/" + quote(scope, safe=""),
            {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params})
        if "error" in result:
            raise NodeRequestError(400, result["error"])
        return result["result"]

    def call(self, scope, skill, payload, *, task_id=None, metadata=None, automatic=None):
        """Use the owner's settlement policy; a stable task_id resumes the original call."""
        task_id = task_id or uuid.uuid4().hex
        outcome = self.request("POST", "/v1/trades/call", {"scope": scope, "automatic": automatic,
            "request": {"skill": skill, "payload": payload, "task_id": task_id,
                "message": {"role": "user", "messageId": task_id,
                            "parts": [{"kind": "data", "data": payload}]},
                "metadata": metadata or {}}})
        return task_view(CallOutcome(**outcome))

    def get_task(self, scope, task_id):
        outcome = self.request("GET", "/v1/trades/call?" + urlencode({"scope": scope, "task_id": task_id}))
        if outcome:
            return task_view(CallOutcome(**outcome))
        return self.rpc(scope, "tasks/get", {"id": task_id})

    def cancel_task(self, scope, task_id):
        return self.rpc(scope, "tasks/cancel", {"id": task_id})

    def call_agent(self, agent_id, *, skill, payload=None, task_id=None, metadata=None, automatic=None):
        task = self.call(agent_id, skill, payload, task_id=task_id, metadata=metadata, automatic=automatic)
        state = task.get("metadata", {}).get("a2nState")
        if str(state).upper() not in {"COMPLETED", "ACCEPTED", "SETTLED", "SETTLEMENT_PENDING"}:
            error_class = SafeRetryError if str(state).upper() in {"UNREACHABLE", "SIGNED_UNREACHABLE"} else NodeRequestError
            raise error_class(409, {"error": "task_not_completed", "state": state, "task": task})
        parts = [p for artifact in task.get("artifacts", []) for p in artifact.get("parts", [])]
        return next((p.get("data", p.get("text")) for p in parts), None)

Client = NodeClient
