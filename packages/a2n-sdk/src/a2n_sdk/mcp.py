"""MCP Streamable HTTP adapter. Protocol IO only; no market or payment rules."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .ports import CallResponse
from .upstream import network_failure
from .version import __version__

VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
MAX_BYTES = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("MCP_REDIRECT_REQUIRES_EXPLICIT_ENDPOINT")


class MCPClient:
    def __init__(self, endpoint, *, headers=None, header_provider=None, timeout=60.0,
                 use_system_proxy=True):
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or not 0 < timeout <= 300):
            raise ValueError("INVALID_MCP_ENDPOINT")
        self.endpoint, self.timeout = endpoint, timeout
        self.headers, self.header_provider = dict(headers or {}), header_provider
        handlers = [NoRedirect()]
        if not use_system_proxy or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self.session, self.version, self.ready = None, None, False
        self.lock = threading.RLock()

    def _post(self, body):
        headers = {**self.headers, **(self.header_provider() if self.header_provider else {}),
                   "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        if self.session:
            headers["MCP-Session-Id"] = self.session
        req = urllib.request.Request(self.endpoint, method="POST", headers=headers,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        with self.opener.open(req, timeout=self.timeout) as response:
            session = response.headers.get("MCP-Session-Id")
            if session:
                if len(session) > 1024 or any(not 0x21 <= ord(c) <= 0x7e for c in session):
                    raise ValueError("INVALID_MCP_SESSION")
                self.session = session
            if "id" not in body:
                if response.status != 202:
                    raise ValueError("INVALID_MCP_NOTIFICATION_RESPONSE")
                return None
            kind = response.headers.get("Content-Type", "").split(";")[0]
            if kind == "text/event-stream":
                total, parts, started = 0, [], time.monotonic()
                while True:
                    raw = response.readline(MAX_BYTES + 1)
                    total += len(raw)
                    if total > MAX_BYTES or time.monotonic() - started > self.timeout:
                        raise ValueError("MCP_RESPONSE_TOO_LARGE")
                    if not raw:
                        break
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if line.startswith("data:"):
                        parts.append(line[5:].lstrip(" "))
                    if not line and parts:
                        data = "\n".join(parts); parts = []
                        if not data:
                            continue
                        frame = json.loads(data)
                        if frame.get("id") == body["id"]:
                            return self._result(frame, body["id"])
                raise ConnectionError("MCP_STREAM_ENDED_WITHOUT_RESULT")
            if kind != "application/json":
                raise ValueError("INVALID_MCP_CONTENT_TYPE")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("MCP_RESPONSE_TOO_LARGE")
            return self._result(json.loads(raw), body["id"])

    @staticmethod
    def _result(frame, request_id):
        if (not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0"
                or frame.get("id") != request_id):
            raise ValueError("INVALID_MCP_RPC_RESPONSE")
        if "error" in frame:
            raise ValueError("MCP_RPC_ERROR: " + str(frame["error"])[:500])
        if not isinstance(frame.get("result"), dict):
            raise ValueError("INVALID_MCP_RPC_RESULT")
        return frame["result"]

    def initialize(self):
        with self.lock:
            if self.ready:
                return
            self.session, self.version = None, None
            result = self._post({"jsonrpc": "2.0", "id": "a2n-init", "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "A2N", "version": __version__}}})
            if result.get("protocolVersion") not in VERSIONS or "tools" not in result.get("capabilities", {}):
                raise ValueError("MCP_TOOLS_OR_VERSION_UNSUPPORTED")
            self.version = result["protocolVersion"]
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.ready = True

    def tools(self):
        with self.lock:
            self.initialize()
            tools, seen, cursor = [], set(), None
            for page in range(16):
                result = self._post({"jsonrpc": "2.0", "id": "tools-" + str(page), "method": "tools/list",
                                     "params": {"cursor": cursor} if cursor else {}})
                rows = result.get("tools")
                if not isinstance(rows, list):
                    raise ValueError("INVALID_MCP_TOOLS")
                for tool in rows:
                    name = tool.get("name") if isinstance(tool, dict) else None
                    if not isinstance(name, str) or not 1 <= len(name) <= 128 or name in seen:
                        raise ValueError("INVALID_OR_DUPLICATE_MCP_TOOL")
                    if not isinstance(tool.get("inputSchema"), dict):
                        raise ValueError("INVALID_MCP_INPUT_SCHEMA")
                    seen.add(name); tools.append(tool)
                if len(tools) > 256:
                    raise ValueError("MCP_TOOL_CAPACITY_LIMIT")
                next_cursor = result.get("nextCursor")
                if not next_cursor:
                    return tools
                if not isinstance(next_cursor, str) or next_cursor == cursor or len(next_cursor) > 2048:
                    raise ValueError("INVALID_MCP_CURSOR")
                cursor = next_cursor
            raise ValueError("MCP_PAGE_CAPACITY_LIMIT")

    def call(self, tool, arguments, task_id):
        with self.lock:
            self.initialize()
            return self._post({"jsonrpc": "2.0", "id": task_id, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments}})


class MCPUpstream:
    def __init__(self, endpoint, *, allowed_tools=(), **kwargs):
        self.client = MCPClient(endpoint, **kwargs)
        self.allowed_tools = frozenset(allowed_tools)

    def invoke(self, request):
        if not request.skill or not isinstance(request.payload, dict):
            return CallResponse.failure("MCP_TOOL_NAME_AND_OBJECT_ARGUMENTS_REQUIRED", state="PROTOCOL_ERROR")
        if request.skill not in self.allowed_tools:
            return CallResponse.failure("MCP_TOOL_NOT_IN_SERVICE_CARD", state="PROTOCOL_ERROR")
        try:
            result = self.client.call(request.skill, request.payload, request.task_id)
        except Exception as exc:
            # MCP does not guarantee tools/call idempotence. Never resend a lost call.
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                self.client.ready = False
            response = network_failure(exc)
            if response.state != "UNREACHABLE":
                response.state = "DELIVERY_UNKNOWN"
                response.metadata.update(remote_effect_unknown=True, replay_safe=False)
            return response
        if result.get("isError"):
            return CallResponse.failure(result.get("content") or "MCP_TOOL_ERROR",
                                        metadata={"protocol": "mcp", "remote_terminal": True})
        output = result.get("structuredContent")
        if output is None:
            output = result.get("content") or []
        return CallResponse.success(output, metadata={"protocol": "mcp", "mcp_result": result})


def tool_card(endpoint, tool):
    return {"name": str(tool.get("title") or tool["name"]), "description": tool.get("description", ""),
        "version": "1", "url": endpoint, "skills": [{"id": tool["name"], "name": tool["name"],
            "description": tool.get("description", ""), "inputSchema": tool["inputSchema"]}],
        "defaultInputModes": ["application/json"], "defaultOutputModes": ["application/json"],
        "x-a2n": {"upstream_protocol": "mcp", "mcp_tool": tool["name"]}}
