"""本机 A2A 网关：让任何支持 A2A 的工作台通过 localhost 使用 A2N。"""
from __future__ import annotations

import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .ports import CallRequest


MAX_BODY = 16 * 1024 * 1024
MAX_TASKS = 1000


def _payload_of(params: dict) -> tuple[Any, dict]:
    message = params.get("message") or {}
    payload: Any = None
    for part in message.get("parts") or []:
        if "data" in part:
            payload = part.get("data")
            break
        if "text" in part:
            payload = part.get("text")
            break
    return (message if payload is None else payload), message


def _task_view(outcome, context_id: str = "") -> dict[str, Any]:
    state = "completed" if outcome.ok else "failed"
    artifacts = []
    # 验收/结算失败也不能抹掉已经交付的事实；调用方仍需看到产物才能申诉或重试结算。
    if outcome.result is not None:
        part = ({"kind": "text", "text": outcome.result}
                if isinstance(outcome.result, str)
                else {"kind": "data", "data": outcome.result})
        artifacts = [{"artifactId": f"art_{outcome.task_id}", "parts": [part]}]
    return {
        "id": outcome.task_id,
        "contextId": context_id,
        "status": {"state": state},
        "artifacts": artifacts,
        "error": None if outcome.ok else outcome.error,
        "metadata": {
            "a2nState": outcome.state,
            "targetRef": outcome.target_ref,
            "acceptance": outcome.verdict,
            "settlement": outcome.settlement,
        },
    }


class LocalA2AGateway:
    """一个端口承载本机全部投影和供给，不再“一张卡占一个端口”。"""

    def __init__(self, runtime, *, host: str = "127.0.0.1", port: int = 0,
                 allow_remote_calls: bool = False) -> None:
        self.runtime = runtime
        self.allow_remote_calls = allow_remote_calls
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "a2n-local-gateway/0.1"

            def log_message(self, *_args) -> None:
                pass

            def _is_local(self) -> bool:
                try:
                    return ipaddress.ip_address(self.client_address[0]).is_loopback
                except ValueError:
                    return False

            def _origin(self) -> str | None:
                origin = self.headers.get("Origin")
                if not origin:
                    return None
                try:
                    host = urlsplit(origin).hostname or ""
                    if ipaddress.ip_address(host).is_loopback:
                        return origin
                except ValueError:
                    if host == "localhost":
                        return origin
                return None

            def _send(self, code: int, obj: Any) -> None:
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                origin = self._origin()
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(raw)

            def _read(self) -> dict | None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return None
                if length <= 0 or length > MAX_BODY:
                    return None
                try:
                    obj = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError, OSError):
                    return None
                return obj if isinstance(obj, dict) else None

            def _management_ok(self) -> bool:
                return self._is_local() and secrets_compare(
                    self.headers.get("X-A2N-Local-Token") or "",
                    outer.runtime.management_token)

            def _calls_ok(self) -> bool:
                return outer.allow_remote_calls or self._is_local()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                origin = self._origin()
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers",
                                     "Content-Type, X-A2N-Local-Token")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path == "/v1/runtime":
                    if not self._is_local():
                        return self._send(403, {"error": "运行时状态只对本机开放"})
                    return self._send(200, outer.runtime.snapshot())
                if path == "/v1/accounts":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理令牌"})
                    return self._send(200, outer.runtime.accounts.list())
                if path == "/.well-known/agent.json":
                    item_id = (parse_qs(parsed.query).get("id") or [""])[0]
                    return self._send_card(item_id)
                item_id = card_id_from_path(path)
                if item_id is not None:
                    return self._send_card(item_id)
                if path.startswith("/v1/tasks/"):
                    if not self._calls_ok():
                        return self._send(403, {"error": "本机网关没有开放远程访问"})
                    task_id = path.split("/", 3)[-1]
                    with outer._lock:
                        task = outer._tasks.get(task_id)
                    return self._send(200, task) if task else self._send(404, {"error": "任务不存在"})
                return self._send(404, {"error": "not found", "path": path})

            def _send_card(self, item_id: str) -> None:
                if not self._calls_ok():
                    return self._send(403, {"error": "本机网关没有开放远程访问"})
                card = outer.runtime.card_for(item_id)
                return self._send(200, card) if card else self._send(404, {"error": "Agent 不存在"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                path = parsed.path.rstrip("/") or "/"
                body = self._read()
                if body is None:
                    return self._send(400, {"error": "请求必须是非空 JSON 对象"})

                if path == "/v1/accounts":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理令牌"})
                    try:
                        item = outer.runtime.add_account(
                            body.get("account_id") or "", kind=body.get("kind") or "agent",
                            label=body.get("label") or "", headers=body.get("headers") or {},
                            metadata=body.get("metadata") or {})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                    return self._send(201, item)

                if path == "/v1/projections":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理令牌"})
                    try:
                        item = outer.runtime.import_agent(
                            body.get("card") or {}, target_ref=body.get("target_ref"),
                            projection_id=body.get("projection_id"),
                            headers=body.get("headers") or {})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                    return self._send(201, {"projection_id": item.projection_id,
                                            "card": item.local_card})

                if path == "/v1/bindings/http":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理令牌"})
                    try:
                        item = outer.runtime.mount_http(
                            body.get("card") or {}, body.get("endpoint") or "",
                            protocol=body.get("protocol") or "a2a",
                            service_id=body.get("service_id"),
                            account_ref=body.get("account_ref"),
                            headers=body.get("headers") or {},
                            source_kind=body.get("source_kind") or "auto",
                            metadata=body.get("metadata") or {})
                        card = outer.runtime.project_binding(item.service_id)
                    except (ValueError, TypeError, KeyError) as exc:
                        return self._send(400, {"error": str(exc)})
                    return self._send(201, {"service_id": item.service_id, "card": card})

                upstream_id = upstream_id_from_path(path)
                if upstream_id is not None:
                    if not self._calls_ok():
                        return self._send(403, {"error": "本机网关没有开放远程访问"})
                    request = request_from_plain(body)
                    response = outer.runtime.invoke_binding(upstream_id, request)
                    if not response.ok:
                        return self._send(502, {"error": response.error,
                                                "state": response.state})
                    return self._send(200, response.result)

                item_id = invoke_id_from_path(path)
                if item_id is not None:
                    if not self._calls_ok():
                        return self._send(403, {"error": "本机网关没有开放远程访问"})
                    return self._a2a(item_id, body)
                return self._send(404, {"error": "not found", "path": path})

            def _a2a(self, item_id: str, rpc: dict) -> None:
                rid = rpc.get("id")
                if rpc.get("jsonrpc") != "2.0":
                    return self._send(200, rpc_error(rid, -32600, "只支持 JSON-RPC 2.0"))
                method = rpc.get("method")
                params = rpc.get("params") or {}
                if method == "tasks/get":
                    task_id = params.get("id") or params.get("taskId")
                    with outer._lock:
                        task = outer._tasks.get(task_id)
                    return self._send(200, rpc_ok(rid, task) if task else
                                      rpc_error(rid, -32602, "任务不存在"))
                if method != "message/send":
                    return self._send(200, rpc_error(rid, -32601, f"不支持的方法：{method}"))
                payload, message = _payload_of(params)
                metadata = dict(params.get("metadata") or {})
                task_id = str(metadata.pop("a2nTaskId", "") or CallRequest().task_id)
                with outer._lock:
                    existing = outer._tasks.get(task_id)
                if existing:
                    return self._send(200, rpc_ok(rid, existing))
                request = CallRequest(
                    skill=str(metadata.pop("skill", "")), payload=payload,
                    message=message, context_id=str(params.get("contextId") or ""),
                    task_id=task_id, metadata=metadata)
                outcome = outer.runtime.invoke_local_id(item_id, request)
                task = _task_view(outcome, request.context_id)
                with outer._lock:
                    outer._tasks[request.task_id] = task
                    while len(outer._tasks) > MAX_TASKS:
                        outer._tasks.pop(next(iter(outer._tasks)))
                return self._send(200, rpc_ok(rid, task))

        self.http = ThreadingHTTPServer((host, port), Handler)
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self.http.server_address[:2]
        display = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{display}:{port}"

    def start(self) -> "LocalA2AGateway":
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.http.serve_forever, daemon=True,
                                           name="a2n-local-a2a")
            self.thread.start()
        return self

    def stop(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


def secrets_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def card_id_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "a2a" and parts[2:] == [".well-known", "agent.json"]:
        return parts[1]
    return None


def invoke_id_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "a2a" else None


def upstream_id_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["_a2n", "upstream"] and parts[3] == "invoke":
        return parts[2]
    return None


def request_from_plain(body: dict) -> CallRequest:
    return CallRequest(skill=str(body.get("skill") or ""), payload=body.get("payload"),
                       message=body.get("message"),
                       context_id=str(body.get("context_id") or ""),
                       task_id=str(body.get("task_id") or CallRequest().task_id),
                       metadata=dict(body.get("metadata") or {}))


def rpc_ok(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": code, "message": message}}
