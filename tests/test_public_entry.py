"""同机反代（`a2n_node.public_entry`）的两条硬口径。

2026-09-26 抽这个模块出来，是因为同一段反代在两个地方各写了一份，然后就漂了：
一处只放行 `/a2a/*`（`/public/*` 被自己 404，公共目录整条链路断掉），
另一处把"公开端口"当成本机监听端口 —— 公开地址是 `https://host` 时本机去抢 443，
nginx 已经占着，节点**直接起不来**（真踩过）。

所以这里钉两件事：
1. 本机监听端口 ≠ 公开端口（前置 TLS 终结器时不能抢 80/443）；
2. 只放行公开展示面；`/v1/*`、`/console`、`/health` 必须 404 —— 而且**不能悄悄
   转给上游**（转过去在节点看来是回环来源，等于把管理面开出去）。
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from a2n_node.public_entry import DEFAULT_TLS_FRONTED_PORT, public_entry_port, serve_public_entry


@pytest.mark.parametrize("base,explicit,want", [
    # 前置 TLS 终结器：公开是 443，本机不能去抢
    ("https://a2n.example.com", None, DEFAULT_TLS_FRONTED_PORT),
    ("https://a2n.example.com", 8891, 8891),
    ("http://a2n.example.com", None, DEFAULT_TLS_FRONTED_PORT),   # 80 同样要退开
    # 直接暴露的裸端口（没有前置终结器）：两边就是同一个端口
    ("http://1.2.3.4:8891", None, 8891),
    ("https://1.2.3.4:9443", None, 9443),
    # 显式值优先，也接受字符串（环境变量来的都是字符串）
    ("https://a2n.example.com", "9999", 9999),
    ("http://1.2.3.4:8891", "", 8891),
])
def test_listen_port_is_not_the_public_port(base, explicit, want):
    assert public_entry_port(base, explicit) == want


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Upstream:
    """假的上游节点：只记录收到的请求，不判权限（判权限是被测代理的事）。"""

    def __init__(self):
        self.port = _free_port()
        self.seen: list[tuple[str, str | None]] = []
        upstream = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                upstream.seen.append((self.path, self.headers.get("Host")))
                raw = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _get(port: int, path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(f"http://127.0.0.1:{port}{path}", timeout=8)


def test_public_entry_allows_exactly_the_public_surface():
    upstream = _Upstream()
    listen = _free_port()
    public_base = "https://a2n.example.com"       # 前置 TLS 终结器
    serve_public_entry(upstream.port, public_base, listen_port=listen, tag="test")
    try:
        for path in ("/a2a/svc_1/.well-known/agent.json", "/public/v1/agents?skill=x"):
            with _get(listen, path) as resp:
                assert resp.status == 200, path
        # 上游拿到的 Host 必须是**声明过的公开入口**，否则网关的 _public_card_base
        # 匹配不上（经反代的请求在节点看来是回环来源）。
        assert upstream.seen[-1][1] == "a2n.example.com", upstream.seen
        assert upstream.seen[-1][0].startswith("/public/v1/agents"), upstream.seen

        for path in ("/v1/runtime", "/console", "/health", "/a2a", "/"):
            with pytest.raises(urllib.error.HTTPError) as err:
                _get(listen, path)
            assert err.value.code == 404, path
        # 关键：被拦下的请求**没有转给上游**（转了就等于从回环把管理面读出来）
        reached = [p for p, _ in upstream.seen]
        assert not any(p.startswith(("/v1/", "/console", "/health")) for p in reached), reached
    finally:
        upstream.stop()
