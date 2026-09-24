"""Local HTTP/A2A adapter. Configuration and durable execution live in services."""
from __future__ import annotations

import hmac
import ipaddress
import json
import sys
from http.cookies import SimpleCookie
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .calls import CallService
from .pairing import PairingService, loopback_url
from .ports import CallRequest
from .storage import LocalStore

MAX_BODY = 16 * 1024 * 1024
LOCAL_COOKIE = "A2N_LOCAL_TOKEN"


def _task_view(outcome, context_id: str = "") -> dict:
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
        artifacts = [{"artifactId": f"art_{outcome.task_id}", "parts": [part]}]
    metadata = {"a2nState": outcome.state, "targetRef": outcome.target_ref,
                "acceptance": outcome.verdict, "settlement": outcome.settlement,
                "network": outcome.metadata}
    for key in ("cancel_requested", "cancel_acknowledged",
                "remote_effect_unknown", "remote_terminal", "cancel_note"):
        if key in outcome.metadata:
            metadata[key] = outcome.metadata[key]
    status = {"state": state}
    remote_status = ((outcome.metadata.get("a2a_task") or {}).get("status") or {})
    if isinstance(remote_status, dict) and remote_status.get("message") is not None:
        status["message"] = remote_status["message"]
    return {"kind": "task", "id": outcome.task_id, "contextId": context_id,
            "status": status, "artifacts": artifacts,
            "error": None if outcome.ok else outcome.error,
            "metadata": metadata}


class _LocalServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 允许第二个 socket 静默绑定同一端口（行为未定义）——
    # 个人电脑上"启动成功其实是别人在听"比报错更糟。POSIX 保留 reuseaddr 以便快速重启。
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


class LocalA2AGateway:
    def __init__(self, runtime, *, host="127.0.0.1", port=0, allow_remote_calls=False,
                 management=None, pairing=None, calls=None, public_card_bases=None):
        self.runtime, self.management = runtime, management
        self.allow_remote_calls = allow_remote_calls
        self.pairing = pairing or PairingService()
        self._owned_store = None if calls else LocalStore()
        self.calls = calls or CallService(
            runtime.invoke_local_id, self._owned_store,
            refresh_remote=runtime.refresh_remote_task,
            cancel_remote=runtime.cancel_remote_task)
        self.public_card_bases = tuple(str(value).rstrip("/")
                                       for value in (public_card_bases or ()) if value)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "a2n-local-gateway/0.2"

            def setup(self):
                super().setup()
                self.connection.settimeout(120)

            def log_message(self, *_args):
                pass

            def _local(self):
                return ipaddress.ip_address(self.client_address[0]).is_loopback

            def _origin(self):
                origin = self.headers.get("Origin")
                if origin:
                    return origin
                # Browsers omit Origin on same-origin GET; Referer still identifies
                # the paired console. Cross-origin calls continue to use Origin.
                referer = urlsplit(self.headers.get("Referer") or "")
                return f"{referer.scheme}://{referer.netloc}" if referer.netloc else ""

            def _host_ok(self):
                return loopback_url("http://" + (self.headers.get("Host") or ""))

            def _token(self):
                header = self.headers.get("X-A2N-Local-Token") or ""
                if header:
                    return header
                try:
                    cookie = SimpleCookie(self.headers.get("Cookie") or "")
                    return cookie[LOCAL_COOKIE].value if LOCAL_COOKIE in cookie else ""
                except (KeyError, TypeError):
                    return ""

            def _management_ok(self):
                if not self._local() or not self._host_ok():
                    return False
                if not outer.pairing.origin_allowed(self._origin()):
                    return False
                token = self._token()
                return bool(token and (secrets_compare(token, runtime.management_token)
                                       or outer.pairing.verify(token, self._origin())))

            def _calls_ok(self):
                if not outer.pairing.origin_allowed(self._origin()):
                    return False
                return (outer.allow_remote_calls
                        or (self._local() and self._host_ok())
                        # Explicit public_card_bases authorizes one exact
                        # loopback reverse proxy route for both Card and A2A.
                        # Management endpoints still require _management_ok.
                        or bool(self._public_card_base()))

            def _public_card_base(self):
                """Recognize an explicitly configured, loopback reverse proxy."""
                if not self._local():
                    return None
                forwarded_host = (self.headers.get("X-Forwarded-Host") or "").strip()
                forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "").strip()
                raw_host = (self.headers.get("Host") or "").strip()
                for base in outer.public_card_bases:
                    wanted = urlsplit(base)
                    if raw_host == wanted.netloc:
                        return base
                    if (forwarded_host == wanted.netloc
                            and forwarded_proto == wanted.scheme):
                        return base
                return None

            def _send(self, code, obj, *, html=False, headers=None):
                raw = obj.encode("utf-8") if html else json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html else
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if self._origin() and outer.pairing.origin_allowed(self._origin()):
                    self.send_header("Access-Control-Allow-Origin", self._origin())
                    self.send_header("Vary", "Origin")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Execution is journaled independently of browser lifetime.

            def _read(self):
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("请使用 application/json")
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    raise ValueError("请求体为空或过大")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求必须是 JSON 对象")
                return body

            def do_OPTIONS(self):
                if not self._calls_ok():
                    return self._send(403, {"error": "控制台来源不被允许"})
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self._origin() or "null")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-A2N-Local-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self):
                parsed = urlsplit(self.path)
                path = parsed.path.rstrip("/") or "/"
                card_item = card_id_from_path(path)
                if path == "/.well-known/agent.json":
                    card_item = (parse_qs(parsed.query).get("id") or [""])[0]
                public_card_base = self._public_card_base() if card_item is not None else None
                if not self._calls_ok() and not public_card_base:
                    return self._send(403, {"error": "本机网关没有开放此访问"})
                if path in {"/", "/console"}:
                    if not self._local() or not self._host_ok():
                        return self._send(403, {"error": "管理页只在本机开放"})
                    return self._send(200, (Path(__file__).parent / "web" / "runtime.html").read_text(
                        encoding="utf-8"), html=True, headers={
                            "Set-Cookie": (
                                f"{LOCAL_COOKIE}={runtime.management_token}; "
                                "Path=/; HttpOnly; SameSite=Strict"
                            )
                        })
                if path == "/health":
                    return self._send(200, {"ok": True, "service": "a2n-runtime", "version": 2})
                if path in {"/v1/runtime", "/v1/accounts"}:
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    return self._send(200, runtime.accounts.list() if path.endswith("accounts") else
                                      outer.management.snapshot() if outer.management else runtime.snapshot())
                if path == "/v1/calls/detail":
                    # 收据与凭证详情：管理面受保护；只带出凭证事实，不带请求载荷。
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    if not outer.management:
                        return self._send(404, {"error": "非持久模式没有本地调用记录"})
                    q = parse_qs(parsed.query)
                    scope = (q.get("scope") or [""])[0]
                    task_id = (q.get("task_id") or [""])[0]
                    row = outer.management.store.task(scope, task_id)
                    if not row:
                        return self._send(404, {"error": "本地没有这条调用记录"})
                    outcome = row.get("outcome") or {}
                    return self._send(200, {
                        "scope": scope, "task_id": task_id, "state": row["state"],
                        "receipt": outcome.get("receipt"),
                        "settlement": outcome.get("settlement") or {},
                        "verdict": outcome.get("verdict") or {},
                        "metadata": outcome.get("metadata") or {}})
                item_id = card_item
                if item_id is not None:
                    card = runtime.card_for(item_id, public_base=public_card_base)
                    return self._send(200, card) if card else self._send(404, {"error": "Agent 不存在"})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._calls_ok():
                    return self._send(403, {"error": "本机网关没有开放此访问"})
                path = urlsplit(self.path).path.rstrip("/")
                try:
                    body = self._read()
                    if path == "/v1/pairing":
                        if not self._local() or not self._host_ok():
                            raise PermissionError("配对只允许本机访问")
                        return self._send(200, outer.pairing.exchange(body.get("code") or "", self._origin()))
                    if path.startswith("/v1/"):
                        if not self._management_ok():
                            return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                        if path == "/v1/disconnect":
                            outer.pairing.revoke(self._token())
                            return self._send(200, {"ok": True})
                        if path == "/v1/pairing/new":
                            return self._send(200, {"code": outer.pairing.new_code(), "expires_in": 300})
                        if outer.management:
                            status, result = outer.management.command(path, body)
                            return self._send(status, result)
                        # Lightweight in-process runtime retains its existing management API.
                        if path == "/v1/accounts":
                            return self._send(201, runtime.add_account(**body))
                        if path == "/v1/projections":
                            item = runtime.import_agent(body.get("card") or {},
                                target_ref=body.get("target_ref"), projection_id=body.get("projection_id"),
                                headers=body.get("headers"), account_ref=body.get("account_ref"))
                            return self._send(201, {"projection_id": item.projection_id, "card": item.local_card})
                        if path == "/v1/bindings/http":
                            config = dict(body)
                            config["source_card"] = config.pop("card", {})
                            item = runtime.mount_http(**config)
                            return self._send(201, {"service_id": item.service_id,
                                                    "card": runtime.project_binding(item.service_id)})
                    upstream_id = upstream_id_from_path(path)
                    if upstream_id is not None:
                        result = outer.calls.invoke(upstream_id, request_from_plain(body))
                        return self._send(200, result.result) if result.ok else self._send(
                            502, {"error": result.error, "state": result.state})
                    item_id = invoke_id_from_path(path)
                    if item_id is not None:
                        return self._a2a(item_id, body)
                    return self._send(404, {"error": "not found"})
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    self._send(400, {"error": str(exc)})

            def _a2a(self, item_id, rpc):
                rid = rpc.get("id")
                if rpc.get("jsonrpc") != "2.0":
                    return self._send(200, rpc_error(rid, -32600, "只支持 JSON-RPC 2.0"))
                params = rpc.get("params") or {}
                if not isinstance(params, dict):
                    return self._send(200, rpc_error(rid, -32602, "params 必须是对象"))
                if rpc.get("method") == "tasks/get":
                    result = outer.calls.get(
                        item_id, str(params.get("id") or params.get("taskId") or ""),
                        refresh_remote=True)
                    return self._send(200, rpc_ok(rid, _task_view(result)) if result else
                                      rpc_error(rid, -32602, "任务不存在"))
                if rpc.get("method") == "tasks/cancel":
                    task_id = str(params.get("id") or params.get("taskId") or "")
                    if not task_id:
                        return self._send(200, rpc_error(rid, -32602, "任务标识不能为空"))
                    result = outer.calls.cancel(item_id, task_id)
                    return self._send(200, rpc_ok(rid, _task_view(result)) if result else
                                      rpc_error(rid, -32602, "任务不存在"))
                if rpc.get("method") != "message/send":
                    return self._send(200, rpc_error(rid, -32601, "不支持的方法"))
                try:
                    message = params.get("message") or {}
                    parts = message.get("parts") or []
                    payload = message
                    for part in parts:
                        if "data" in part or "text" in part:
                            payload = part.get("data") if "data" in part else part.get("text")
                            break
                    meta = dict(params.get("metadata") or {})
                    task_id = str(meta.pop("a2nTaskId", "") or message.get("messageId") or CallRequest().task_id)
                    if len(task_id) > 200:
                        raise ValueError("任务标识过长")
                    request = CallRequest(skill=str(meta.pop("skill", "")), payload=payload,
                                          message=message, context_id=str(params.get("contextId") or ""),
                                          task_id=task_id, metadata=meta)
                    blocking = (params.get("configuration") or {}).get("blocking", True) is not False
                    result = outer.calls.invoke(item_id, request, blocking=blocking)
                    return self._send(200, rpc_ok(rid, _task_view(result, request.context_id)))
                except (ValueError, TypeError, AttributeError) as exc:
                    return self._send(200, rpc_error(rid, -32602, str(exc)))

        self.http = _LocalServer((host, port), Handler)
        self.thread = None

    @property
    def base_url(self):
        host, port = self.http.server_address[:2]
        return f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"

    def start(self):
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name="a2n-local-a2a")
        self.thread.start()
        return self

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self._owned_store:
            self.calls.stop()
            self._owned_store.close()


def secrets_compare(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def card_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) == 4 and parts[0] == "a2a" and parts[2:] == [".well-known", "agent.json"] else None


def invoke_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "a2a" else None


def upstream_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[2] if len(parts) == 4 and parts[:2] == ["_a2n", "upstream"] and parts[3] == "invoke" else None


def request_from_plain(body):
    return CallRequest(skill=str(body.get("skill") or ""), payload=body.get("payload"),
                       message=body.get("message"), context_id=str(body.get("context_id") or ""),
                       task_id=str(body.get("task_id") or CallRequest().task_id),
                       metadata=dict(body.get("metadata") or {}))


def rpc_ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
