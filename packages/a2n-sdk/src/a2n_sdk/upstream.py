"""真实 Agent 接入层：本地函数、本地 HTTP 与远程 A2A 统一成一个端口。"""
from __future__ import annotations

import copy
import errno
from dataclasses import dataclass, field
import json
import threading
import socket
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from .ports import CallRequest, CallResponse, UpstreamPort


def network_failure(exc: Exception) -> CallResponse:
    """Only a proven pre-connect failure is safe to retry on another route."""
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, (TimeoutError, socket.timeout)):
        state = "TIMEOUT"
    elif isinstance(cause, socket.gaierror) or (
        isinstance(cause, OSError) and cause.errno in {
            errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, 10061, 10051, 10065
        }
    ):
        state = "UNREACHABLE"
    else:
        state = "DELIVERY_UNKNOWN"
    metadata = {"stage": "transport"}
    if state in {"TIMEOUT", "DELIVERY_UNKNOWN"}:
        metadata.update({
            "remote_effect_unknown": True,
            "replay_safe": False,
            "same_task_replay": "returns_recorded_outcome",
        })
    return CallResponse.failure(f"{type(exc).__name__}: {exc}", state=state,
                                metadata=metadata)


def _opener(url: str, use_system_proxy: bool):
    if use_system_proxy and not url.startswith(("http://127.0.0.1", "http://localhost")):
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(url: str, body: dict, headers: dict[str, str], timeout: float,
               use_system_proxy: bool) -> tuple[int, Any]:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with _opener(url, use_system_proxy).open(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(text or "null")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text or "null")
        except ValueError:
            detail = text[:500]
        return exc.code, detail


class CallableUpstream:
    """把已有 Python Agent/工作流挂进节点。"""

    def __init__(self, handler: Callable[[Any], Any], *, pass_request: bool = False) -> None:
        self.handler = handler
        self.pass_request = pass_request

    def invoke(self, request: CallRequest) -> CallResponse:
        try:
            result = self.handler(request if self.pass_request else request.payload)
            if isinstance(result, CallResponse):
                return result
            return CallResponse.success(result)
        except Exception as exc:
            return CallResponse.failure(f"{type(exc).__name__}: {exc}",
                                        metadata={"stage": "upstream"})


class HttpJsonUpstream:
    """转发给普通 JSON Agent；可用于本机、局域网或远程服务。"""

    def __init__(self, endpoint: str, *, headers: dict[str, str] | None = None,
                 header_provider: Callable[[], dict[str, str]] | None = None,
                 timeout: float = 60.0, use_system_proxy: bool = True) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("上游地址必须是 http:// 或 https://")
        self.endpoint = endpoint
        self._headers = dict(headers or {})
        self._header_provider = header_provider
        self.timeout = timeout
        self.use_system_proxy = use_system_proxy

    def invoke(self, request: CallRequest) -> CallResponse:
        headers = dict(self._headers)
        if self._header_provider:
            headers.update(self._header_provider())
        body = {
            "task_id": request.task_id,
            "skill": request.skill,
            "payload": request.payload,
            "message": request.message,
            "context_id": request.context_id,
            "metadata": request.metadata,
        }
        try:
            status, out = _http_json(self.endpoint, body, headers, self.timeout,
                                     self.use_system_proxy)
        except Exception as exc:
            return network_failure(exc)
        if status >= 400:
            return CallResponse.failure(out, metadata={"stage": "upstream", "status": status})
        if isinstance(out, dict) and out.get("ok") is False:
            return CallResponse.failure(out.get("error") or out,
                                        state=str(out.get("state") or "FAILED"),
                                        usage=out.get("usage") or {})
        if isinstance(out, dict) and "result" in out:
            return CallResponse.success(out.get("result"),
                                        state=str(out.get("state") or "COMPLETED"),
                                        usage=out.get("usage") or {},
                                        receipt=out.get("receipt"),
                                        metadata={"upstream_status": status})
        return CallResponse.success(out, metadata={"upstream_status": status})


def _artifact_result(task: dict) -> Any:
    artifacts = task.get("artifacts") or []
    if not artifacts:
        return task.get("result")
    values = []
    for artifact in artifacts:
        for part in artifact.get("parts") or []:
            if "data" in part:
                values.append(part["data"])
            elif "text" in part:
                values.append(part["text"])
    if len(values) == 1:
        return values[0]
    return values


class A2AUpstream:
    """调用任意远程 A2A JSON-RPC Agent。"""

    def __init__(self, endpoint: str, *, headers: dict[str, str] | None = None,
                 header_provider: Callable[[], dict[str, str]] | None = None,
                 timeout: float = 60.0, poll_interval: float = 0.5,
                 use_system_proxy: bool = True) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("A2A 地址必须是 http:// 或 https://")
        if timeout <= 0:
            raise ValueError("A2A 超时必须大于 0")
        if poll_interval < 0:
            raise ValueError("A2A 轮询间隔不能小于 0")
        self.endpoint = endpoint.rstrip("/")
        self._headers = dict(headers or {})
        self._header_provider = header_provider
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.use_system_proxy = use_system_proxy

    def invoke(self, request: CallRequest) -> CallResponse:
        deadline = time.monotonic() + self.timeout
        headers = dict(self._headers)
        if self._header_provider:
            headers.update(self._header_provider())
        message = copy.deepcopy(request.message) if request.message else {
            "role": "user",
            "messageId": f"msg_{request.task_id}",
            "parts": ([{"kind": "data", "data": request.payload}]
                      if not isinstance(request.payload, str)
                      else [{"kind": "text", "text": request.payload}]),
        }
        params: dict[str, Any] = {
            "message": message,
            "metadata": {**request.metadata, "skill": request.skill,
                         "a2nTaskId": request.task_id},
        }
        if request.context_id:
            params["contextId"] = request.context_id
        rpc = {"jsonrpc": "2.0", "id": request.task_id,
               "method": "message/send", "params": params}
        response = self._request(rpc, headers, deadline)
        if isinstance(response, CallResponse):
            return response
        task = response
        if task.get("kind") == "message" or ("parts" in task and "status" not in task):
            return CallResponse.success(_artifact_result({"artifacts": [task]}))

        remote_id = str(task.get("id") or "")
        remote_context = str(task.get("contextId") or request.context_id or "")
        state = self._state(task)
        settled = {"completed", "failed", "rejected", "canceled", "cancelled"}
        caller_action = {"input-required", "auth-required"}
        if state not in settled | caller_action:
            if not remote_id:
                return CallResponse.failure(
                    "A2A 非终态任务缺少 id，无法继续查询", state="PROTOCOL_ERROR",
                    metadata={"a2a_task": task})
            sequence = 0
            while state not in settled | caller_action:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._poll_timeout(task, remote_id, remote_context)
                if self.poll_interval:
                    time.sleep(min(self.poll_interval, remaining))
                if time.monotonic() >= deadline:
                    return self._poll_timeout(task, remote_id, remote_context)
                sequence += 1
                poll = {
                    "jsonrpc": "2.0",
                    "id": f"{request.task_id}:get:{sequence}",
                    "method": "tasks/get",
                    "params": {"id": remote_id},
                }
                response = self._request(poll, headers, deadline)
                if isinstance(response, CallResponse):
                    response.metadata = dict(response.metadata)
                    response.metadata.setdefault("a2a_task", task)
                    response.metadata.setdefault("a2a_task_id", remote_id)
                    response.metadata.setdefault("a2a_context_id", remote_context)
                    if response.state in {"TIMEOUT", "DELIVERY_UNKNOWN"}:
                        response.metadata.setdefault("remote_terminal", False)
                        response.metadata.setdefault(
                            "resume_hint",
                            "仅用 a2a_task_id 调用 tasks/get；不要重发 message/send")
                    return response
                if response.get("kind") == "message":
                    return CallResponse.failure(
                        "A2A tasks/get 返回了消息而不是任务", state="PROTOCOL_ERROR")
                new_id = str(response.get("id") or remote_id)
                if new_id != remote_id:
                    return CallResponse.failure(
                        "A2A tasks/get 返回了不同的任务 id", state="PROTOCOL_ERROR",
                        metadata={"expected_task_id": remote_id, "a2a_task": response})
                response.setdefault("id", remote_id)
                if remote_context:
                    response.setdefault("contextId", remote_context)
                elif response.get("contextId"):
                    remote_context = str(response["contextId"])
                task = response
                state = self._state(task)

        metadata = {"a2a_task": task, "a2a_task_id": remote_id,
                    "a2a_context_id": remote_context}
        if state in {"failed", "rejected", "canceled", "cancelled"}:
            normalized = "CANCELED" if state in {"canceled", "cancelled"} else state.upper()
            return CallResponse.failure(task.get("error") or task,
                                        state=normalized, metadata=metadata)
        return CallResponse.success(_artifact_result(task), state=state.upper(),
                                    metadata=metadata)

    @staticmethod
    def _state(task: dict) -> str:
        return str(((task.get("status") or {}).get("state") or task.get("state")
                    or "unknown")).lower()

    @staticmethod
    def _poll_timeout(task: dict, remote_id: str, remote_context: str) -> CallResponse:
        return CallResponse.failure(
            "A2A 远端任务在本次调用期限内没有完成", state="TIMEOUT",
            metadata={"stage": "tasks/get", "a2a_task": task,
                      "a2a_task_id": remote_id, "a2a_context_id": remote_context,
                      "remote_effect_unknown": True, "remote_terminal": False,
                      "replay_safe": False,
                      "same_task_replay": "returns_recorded_outcome",
                      "resume_hint": "仅用 a2a_task_id 调用 tasks/get；不要重发 message/send"})

    def _request(self, rpc: dict, headers: dict[str, str], deadline: float) \
            -> dict | CallResponse:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CallResponse.failure("A2A 调用超时", state="TIMEOUT",
                                        metadata={
                                            "stage": "transport",
                                            "remote_effect_unknown": True,
                                            "replay_safe": False,
                                            "same_task_replay": "returns_recorded_outcome",
                                        })
        try:
            status, out = _http_json(self.endpoint, rpc, headers, remaining,
                                     self.use_system_proxy)
        except Exception as exc:
            return network_failure(exc)
        if status >= 400:
            return CallResponse.failure(out, metadata={"stage": "upstream", "status": status})
        if not isinstance(out, dict):
            return CallResponse.failure("A2A 上游没有返回 JSON 对象")
        if out.get("error"):
            return CallResponse.failure(out["error"], metadata={"stage": "upstream"})
        task = out.get("result") or {}
        if not isinstance(task, dict):
            return CallResponse.failure("A2A 返回了无效的任务", state="PROTOCOL_ERROR")
        return task


@dataclass(slots=True)
class AgentBinding:
    """一个人挂在节点后面的一份供给。"""

    service_id: str
    source_card: dict[str, Any]
    upstream: UpstreamPort
    source_kind: str
    account_ref: str | None = None
    enabled: bool = True
    published_card: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BindingTable:
    """线程安全的多 Agent 挂载表。"""

    def __init__(self) -> None:
        self._items: dict[str, AgentBinding] = {}
        self._lock = threading.RLock()

    def add(self, binding: AgentBinding) -> AgentBinding:
        if not binding.service_id:
            raise ValueError("service_id 不能为空")
        with self._lock:
            if binding.service_id in self._items:
                raise ValueError(f"service_id 已存在：{binding.service_id}")
            self._items[binding.service_id] = binding
        return binding

    def get(self, service_id: str) -> AgentBinding | None:
        with self._lock:
            return self._items.get(service_id)

    def remove(self, service_id: str) -> AgentBinding | None:
        with self._lock:
            return self._items.pop(service_id, None)

    def list(self) -> list[AgentBinding]:
        with self._lock:
            return list(self._items.values())
