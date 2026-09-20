"""SDK 传输层：反向长连接（隧道）+ 中继转发 + 本地服务挂载。

职责一句话：**节点永远只出站。**
- TunnelClient：出站挂一条长通道（生产 WSS；v1 用长轮询下行，接口同构），
  平台把任务和中继转发请求从这条通道推下来。
- serve_local_agent：把本机的处理函数挂成一个 127.0.0.1 的 HTTP 服务，
  配合 relay 模式，外部就能通过平台公网入口调用你电脑上的 agent——
  而你的电脑不开任何端口。

另外一件事：**计量连署**。inline 交付下"提交结果"是平台代节点做的，节点如果
只在那一刻被问"这条计量你签了吗"，就永远签不出东西。所以连署落在节点真正
干活的边界上（见 TunnelClient._handle_forward），而签名格式由调用方注入
（SDK 零依赖，口径唯一源在 a2n_p2p.attest）。
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


def _upstream_error(e: urllib.error.HTTPError) -> dict:
    """本地服务回的错，**照它自己的原话说**。

    本地服务是**有话说**才回 4xx/5xx 的（"这张卡上的能力没接真实 agent"、
    "科目金额读不出来"…）。以前这里统一换成 `upstream 500` 这种通用噪声，
    等于把可诊断的原因扔了：调用方与供给方都只看到"上游 500"，
    分不清是参数错、节点坏了、还是按设计就不通，最后都得去翻容器日志 ——
    与"失败不许翻成空态"是同一条纪律。

    原文照传（截断到 200 字），解析不出 JSON 就当文本传；真空了才退回通用措辞。
    """
    try:
        raw = (e.read() or b"").decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001 - 读不到就算了，别在错误处理里再抛
        raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return parsed if isinstance(parsed, dict) else {"error": raw[:200]}
    return {"error": f"upstream {e.code}"}


class TunnelClient(threading.Thread):
    """反向长连接客户端（守护线程，自动重连）。

    下行消息：
      {"type":"ping", "req_id"}                             → SDK 自动回包，不调用应用、不计费
      {"type":"forward", "req_id", "method", "path", "body"}  → 转发给本地服务，回包上行
      {"type":"task", ...}                                    → 交给 on_task 回调（同 pull）
    """

    def __init__(self, client: Client, on_task: Callable[[dict], Any],
                 local_base: str | None = None, poll_wait: float = 25.0,
                 on_execute: Callable[[bool, int, str], Any] | None = None,
                 attest_fn: Callable[[str, str, dict], dict | None] | None = None) -> None:
        """on_execute(ok, ms, skill)：本地服务处理完一次转发调用后的回调。

        这是 **inline 交付**（平台编排转发就地执行）路径的观测入口——
        v1 主链路上任务不经推送通道，服务端 SDK 只有在这里才看得到"我干了多少活、
        首响多快"，供心跳 self-reported metrics 用。失败绝不拖垮转发。

        attest_fn(task_id, node_id, dims) -> attestation | None：**计量连署**钩子。

        inline 交付下"提交结果"这一步是平台代节点做的（职责在通道，见
        a2n-gateway.call），节点若只在那一步之后才被问"这条计量是不是你签的"，
        就永远签不出东西 —— 计量签名会变成一条**算了却没人看得到**的能力。
        所以签名的位置必须落在**节点真正干活的边界上**：转发下来时一并被告知
        这一单的任务号与计费口径，本机执行完就地连署，随回包上行。

        签名格式不在这里定义（SDK 零依赖，不认识任何加密算法）：调用方传入
        一个"给 (任务号, 节点号, 计量口径) 就返回签名信封"的函数，实现方是
        唯一的口径源 `a2n_p2p.attest.sign_metering`。
        """
        super().__init__(daemon=True, name="a2n-tunnel")
        self.client = client
        self.on_task = on_task
        self.local_base = local_base.rstrip("/") if local_base else None
        self.poll_wait = poll_wait
        self.on_execute = on_execute
        self.attest_fn = attest_fn
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
            if mtype == "ping":
                self._handle_ping(msg)
            elif mtype == "forward":
                self._handle_forward(msg)
            elif mtype == "task":
                try:
                    self.on_task(msg)
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"任务处理失败: {e}"

    def _handle_ping(self, msg: dict) -> None:
        """Control-plane echo, deliberately bypassing handlers and metering."""
        self.client._req("POST", f"/v1/nodes/{self.client.node_id}/tunnel/up",
                         {"req_id": msg["req_id"], "status": 200,
                          "body": {"pong": msg["req_id"]}})

    def _handle_forward(self, msg: dict) -> None:
        """中继转发：平台公网入口进来的调用 → 本地服务 → 回包上行。

        消息里若带了 task_id / dims（平台编排下发的"这一单 + 计费口径"），
        本机执行完之后**就地给这份计量连署**，把签名信封随回包上行 ——
        平台侧再把它交给验收/入账那一步。签名失败只记 last_error：
        连署是增强证据，不该让一次正常交付因为签名而出不去。
        """
        req_id = msg["req_id"]
        status, body = 502, {"error": "no_local_service"}
        started = time.time()
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
                body = _upstream_error(e)
            except Exception as e:  # noqa: BLE001
                status = 502
                body = {"error": f"{type(e).__name__}: {e}"[:160]}
            if self.on_execute:
                # 本机观测：inline 交付的执行也计入（calls/耗时/TTFT 窗口）。
                # 单发 HTTP 下"结果就绪"即首响；与任务通道的非流式口径一致。
                b = msg.get("body")
                skill = str(b.get("skill") or "") if isinstance(b, dict) else ""
                try:
                    self.on_execute(int(status) < 400, int((time.time() - started) * 1000), skill)
                except Exception:  # noqa: BLE001 - 观测绝不拖垮转发
                    pass
        up: dict = {"req_id": req_id, "status": status, "body": body}
        att = self._attest(msg.get("task_id"), msg.get("dims"))
        if att is not None:
            up["attest"] = att
        try:
            self.client._req("POST", f"/v1/nodes/{self.client.node_id}/tunnel/up", up)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"回包失败: {e}"

    def _attest(self, task_id: str | None, dims: dict | None) -> dict | None:
        """给这一单的计量连署。没有身份 / 没有任务号 / 签不动 —— 一律返回 None。"""
        if self.attest_fn is None or not task_id:
            return None
        try:
            return self.attest_fn(task_id, self.client.node_id, dict(dims or {}))
        except Exception as e:  # noqa: BLE001 - 连署失败不拖垮交付
            self.last_error = f"计量连署失败: {type(e).__name__}: {e}"[:160]
            return None


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
