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
import time
import urllib.error
import urllib.request

import pytest

from a2n_node.public_entry import DEFAULT_TLS_FRONTED_PORT, public_entry_port, serve_public_entry
from a2n_node.public_entry import _public_entry_server


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


# 每一条已登记的公共协议都要**从反代这一侧**能打过来。
# 真踩过的坑：`/public/v1/assets/` 与 `/public/v1/asset-mailbox/` 一度漏在放行清单外，
# 而 `a2n_node.asset_service` 正是拿对方反代地址去取 `/public/v1/assets/read` ——
# 同机回环直连节点本体端口的测试照样全绿，只有真到公网 NAT 后面取文件才 404。
# 所以这里按"清单"逐条断言，新增公共协议忘了登记会当场红。
@pytest.mark.parametrize('path', [
    '/public/v1/agents',
    '/public/v1/samples',
    '/public/v1/routes',
    '/public/v1/witness',
    '/public/v1/coord/probe',
    '/public/v1/experience/query',
    '/public/v1/resolution/open',
    '/public/v1/assets/read',          # a2n-assets/1：公网 NAT 后面取文件的唯一入口
    '/public/v1/asset-mailbox/put',    # a2n-asset-mailbox/1
    '/public/v2/metadata-mailbox/send',
])
def test_registered_public_protocols_reach_upstream_through_the_entry(path):
    upstream = _Upstream()
    listen = _free_port()
    serve_public_entry(upstream.port, 'https://a2n.example.com', listen_port=listen, tag='test')
    try:
        with _get(listen, path) as resp:
            assert resp.status == 200, path
        assert upstream.seen and upstream.seen[-1][0] == path, upstream.seen
    finally:
        upstream.stop()


@pytest.mark.parametrize('path', [
    '/public/v1/assets',        # 少一个尾斜杠就不该命中前缀清单
    '/public/v1/internal/secrets',
    '/public/v1/assetsomething',
])
def test_paths_merely_sharing_a_public_prefix_are_still_refused(path):
    """前缀清单不许退化成 `startswith` —— 拼得近不等于就是公共协议。"""
    upstream = _Upstream()
    listen = _free_port()
    serve_public_entry(upstream.port, 'https://a2n.example.com', listen_port=listen, tag='test')
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(listen, path)
        assert err.value.code == 404, path
        assert upstream.seen == [], (path, upstream.seen)
    finally:
        upstream.stop()


@pytest.fixture
def bounded_entry():
    upstream = _Upstream()
    server = _public_entry_server(upstream.port, 'http://example.test', listen_port=0,
                                  read_timeout=.4, max_connections=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], upstream
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        upstream.stop()


def _raw_client(port, headers, body=b''):
    client = socket.create_connection(('127.0.0.1', port), timeout=2)
    client.settimeout(2)
    client.sendall(headers + b'\r\n\r\n' + body)
    return client


@pytest.mark.parametrize('path,limit', [
    ('/public/v1/coord/probe', 262144),
    ('/relay/v1/submit', 1_400_000),
    ('/a2n/ack', 65536),
    ('/a2a/svc', 16 * 1024 * 1024),
])
def test_oversized_public_body_is_rejected_before_reading(bounded_entry, path, limit):
    port, upstream = bounded_entry
    headers = f'POST {path} HTTP/1.1\r\nHost: example.test\r\nContent-Length: {limit + 1}'.encode()
    with _raw_client(port, headers, b'x') as client:
        assert client.recv(1024).startswith(b'HTTP/1.1 413')
    assert upstream.seen == []


@pytest.mark.parametrize('framing', [
    b'Content-Length: -1', b'Content-Length: invalid',
    b'Content-Length: 1\r\nContent-Length: 1',
    b'Transfer-Encoding: chunked',
])
def test_invalid_public_framing_is_rejected(bounded_entry, framing):
    port, upstream = bounded_entry
    with _raw_client(port, b'POST /public/v1/coord/probe HTTP/1.1\r\nHost: example.test\r\n' + framing) as client:
        assert client.recv(1024).startswith(b'HTTP/1.1 400')
    assert upstream.seen == []


def test_public_connection_limit_and_total_read_deadline(bounded_entry):
    port, upstream = bounded_entry
    headers = (b'POST /public/v1/coord/probe HTTP/1.1\r\nHost: example.test\r\n'
               b'Expect: 100-continue\r\nContent-Length: 100')
    with _raw_client(port, headers) as slow:
        # The interim response proves this connection occupies the only slot.
        assert slow.recv(1024).startswith(b'HTTP/1.1 100')
        started = time.monotonic()
        with _raw_client(port, b'GET /public/v1/agents HTTP/1.1\r\nHost: example.test') as second:
            response = second.recv(1024)
            assert response.startswith(b'HTTP/1.1 503') and b'Retry-After: 1' in response
        for _ in range(3):
            time.sleep(.1)
            slow.sendall(b'x')
        assert slow.recv(1024).startswith(b'HTTP/1.1 408')
        assert time.monotonic() - started < .6  # idle-only timeout would finish at ~.7
    with _get(port, '/public/v1/agents') as response:
        assert response.status == 200
    assert len(upstream.seen) == 1


def test_slow_public_headers_are_closed_at_absolute_deadline(bounded_entry):
    port, upstream = bounded_entry
    with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
        started = time.monotonic()
        client.sendall(b'GET /public/v1/agents HTTP/1.1\r\nHost: ')
        for _ in range(3):
            time.sleep(.1)
            client.sendall(b'x')
        assert client.recv(1024) == b''
        assert time.monotonic() - started < .6
    assert upstream.seen == []
