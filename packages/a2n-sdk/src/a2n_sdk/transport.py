"""SDK 传输层：反向长连接（隧道）+ 中继转发 + 本地服务挂载。

职责一句话：**节点永远只出站。**
- TunnelClient：出站挂一条长通道（生产 WSS；v1 用长轮询下行，接口同构），
  平台把任务和中继转发请求从这条通道推下来。
- serve_local_agent：把本机的处理函数挂成一个 127.0.0.1 的 HTTP 服务，
  配合 relay 模式，外部就能通过平台公网入口调用你电脑上的 agent——
  而你的电脑不开任何端口。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .client import Client


class TunnelClient(threading.Thread):
    """反向长连接客户端（守护线程，自动重连）。

    下行消息两类：
      {"type":"forward", "req_id", "method", "path", "body"}  → 转发给本地服务，回包上行
      {"type":"task", ...}                                    → 交给 on_task 回调（同 pull）
    """

    def __init__(self, client: Client, on_task: Callable[[dict], Any],
                 local_base: str | None = None, poll_wait: float = 25.0) -> None:
        super().__init__(daemon=True, name="a2n-tunnel")
        self.client = client
        self.on_task = on_task
        self.local_base = local_base.rstrip("/") if local_base else None
        self.poll_wait = poll_wait
        self.tunnel_id: str | None = None
        self.connected = threading.Event()
        self._stop = threading.Event()
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._run_once()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 - 隧道断了就退避重连
                self.connected.clear()
                self.last_error = f"{type(e).__name__}: {e}"[:160]
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _run_once(self) -> None:
        assert self.client.node_id, "请先注册"
        meta = {"pid": True, "local_base": self.local_base}
        r = self.client._req("POST", f"/v1/nodes/{self.client.node_id}/tunnel",
                             {"mode": "relay" if self.local_base else "tunnel", "meta": meta})
        self.tunnel_id = r["tunnel_id"]
        self.connected.set()
        while not self._stop.is_set():
            msg = self.client._req("GET", f"/v1/nodes/{self.client.node_id}"
                                          f"/tunnel/next?tid={self.tunnel_id}&wait={self.poll_wait}")
            mtype = (msg or {}).get("type")
            if mtype == "tunnel.closed":
                self.connected.clear()
                return  # 服务端不认这条隧道了，重开
            if mtype == "forward":
                self._handle_forward(msg)
            elif mtype == "task":
                try:
                    self.on_task(msg)
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"任务处理失败: {e}"

    def _handle_forward(self, msg: dict) -> None:
        """中继转发：平台公网入口进来的调用 → 本地服务 → 回包上行。"""
        req_id = msg["req_id"]
        status, body = 502, {"error": "no_local_service"}
        if self.local_base:
            try:
                data = json.dumps(msg.get("body")).encode() if msg.get("body") is not None else None
                req = urllib.request.Request(self.local_base + (msg.get("path") or "/"),
                                             data=data, method=msg.get("method", "POST"))
                req.add_header("Content-Type", "application/json")
                req.add_header("X-A2N-Forwarded", "1")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status = resp.status
                    body = json.loads(resp.read().decode() or "null")
            except urllib.error.HTTPError as e:
                status = e.code
                body = {"error": f"upstream {e.code}"}
            except Exception as e:  # noqa: BLE001
                status = 502
                body = {"error": f"{type(e).__name__}: {e}"[:160]}
        try:
            self.client._req("POST", f"/v1/nodes/{self.client.node_id}/tunnel/up",
                             {"req_id": req_id, "status": status, "body": body})
        except Exception as e:  # noqa: BLE001
            self.last_error = f"回包失败: {e}"


def serve_local_agent(port: int, handler, verify=None,
                      bind: str = "127.0.0.1",
                      token_header: str = "X-A2N-Call"):
    """把处理函数挂成本地 HTTP 服务（默认 127.0.0.1，配合 relay 中继转发用）。

    verify(headers, payload) -> bool 是**节点自守门**的钩子：
    - relay/tunnel 模式不需要（请求从隧道进来必然已过平台门禁，且只监听本机）；
    - **direct 模式必须开** —— 公网地址一暴露谁都能直接打，平台替它守不住，
      零信任下只能节点自己验：收到请求把 X-A2N-Call 交给平台
      `POST /v1/transport/verify-token` 问真伪，验不过直接 401。

    bind 是"服务监听在哪"，direct 场景由节点自己决定（如 0.0.0.0），
    但开到公网就必须同时给 verify —— 否则等于把能力白送给路人。
    """

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else "{}"
            try:
                payload = json.loads(raw) if raw else {}
                if verify is not None and not verify(dict(self.headers), payload):
                    self._send(401, {"error": "调用凭据无效：该节点要求出示 X-A2N-Call"})
                    return
                out = handler(self.path, payload)
                code, body = 200, json.dumps(out, ensure_ascii=False).encode()
            except Exception as e:  # noqa: BLE001
                code, body = 500, json.dumps({"error": str(e)}).encode()
            self._send(code, body)

        def _send(self, code: int, body) -> None:
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((bind, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="a2n-local").start()
    return srv


def platform_verifier(client, agent_id: str, token_header: str = "X-A2N-Call"):
    """生成一个"问平台验票"的校验函数，供 direct 节点自守门用。

    平台不替节点做决定，只回答"这票是不是真的、是不是给你的"。
    """
    def _verify(headers: dict, payload: dict) -> bool:
        tok = headers.get(token_header) or headers.get(token_header.lower())
        if not tok:
            return False
        try:
            r = client._req("POST", "/v1/transport/verify-token",
                            {"agent_id": agent_id, "token": tok})
        except Exception:  # noqa: BLE001 - 验票失败一律按无效处理
            return False
        return bool(r.get("ok"))
    return _verify
