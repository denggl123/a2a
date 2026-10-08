"""同机反向代理 —— 节点的「公开 HTTP 入口」。

为什么必须有这个反代
--------------------
`a2n_sdk.local_api` 只服务**回环来源**（`_local()` 要求 `client_address` 是回环），
而节点本体只绑 `127.0.0.1`。文档口径是"公共入口由**同机反向代理**提供，代理保留
`Host` 或传入匹配的 `X-Forwarded-Host/Proto`"（见 `docs/SDK-RUNTIME.md`）。
没有它，从别的机器连上来只会看到连接被挂断。

放行面**只有公开展示面**：`/a2a/*`（投影卡与 A2A 调用）与 `/public/*`（自愿公共目录
与见证）。`/v1/*`（账户、挂载、任务列表）、`/console`、`/health` 一律 404 ——
经反代的请求在节点看来来源是回环，照单全收等于把管理面开给所有能连到该端口的人。
这是"最小放行"，不是"顺手全开"。

放行面是**显式清单**，不是 `startswith("/public/")`
----------------------------------------------
`/public/*` 下面每一条都是各自独立的公共协议（`a2n-coord/1`、`a2n-experience/1`、
`a2n-assets/1`、`a2n-asset-mailbox/1`、协调邮箱…）。用前缀一把放行，等于默认
"凡是挂到 /public 下的都算公开展示面"——将来有人在该前缀下加一个**本地专有**端点，
它会静默变成对外可达。所以这里列**具体路径 + 具体前缀**，新增公共协议必须显式登记。

⚠ 登记资产协议时踩过的坑：`/public/v1/assets/` 与 `/public/v1/asset-mailbox/`
一度漏在这份清单外，而 `a2n_node.asset_service` 正是拿对方的**反代地址**去取
`/public/v1/assets/read`。同机回环测试（直连节点本体端口）照样全绿，只有真到
公网 NAT 后面取文件才 404。**凡是进清单的路径，都必须有测试从反代这一侧打过来。**

本机监听端口 ≠ 公开端口
----------------------
`https://host` 这种公开地址（前置 TLS 终结器：云端隧道 / nginx / 负载均衡）端口是 443，
但本机不能去抢 443：那里通常已经被 nginx 占着，硬绑定会**直接起不来**（2026-09-26 真踩过）。
所以本机端口由 `public_entry_port()` 单独决定，可显式给 `A2N_PUBLIC_PORT` 覆盖。

沿用历史：`docker/node_entry.py` 最初私有一份同样的反代；两台机器的部署各写一份之后
就漂了（一处忘了登记 /public/* 的具体路径、一处把端口算错），所以抽到这里单一实现 ——
**并把放行面从前缀一把放行改成显式清单**，让"漏登记"变成一次 404 而不是一次静默的对外可达。
"""
from __future__ import annotations

import http.client
import http.server
import os
import socket
import threading
import time
from urllib.parse import urlsplit

DEFAULT_TLS_FRONTED_PORT = 8891
COORD_BODY_LIMIT = 262144
RELAY_BODY_LIMIT = 1_400_000
ACK_BODY_LIMIT = 65_536
PUBLIC_BODY_LIMIT = 16 * 1024 * 1024
DEFAULT_READ_TIMEOUT = 5.0
DEFAULT_MAX_CONNECTIONS = 32


class _BoundedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, max_connections):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                # Drain a bounded part of the already sent request before close.
                # Closing with unread small headers can discard the 503 via RST
                # on Windows. This admission path must not wait on a slow peer.
                request.settimeout(.05)
                try:
                    request.recv(4096)
                except OSError:
                    pass
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\n'
                                b'Content-Length: 0\r\nRetry-After: 1\r\n'
                                b'Connection: close\r\n\r\n')
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def public_entry_port(public_base: str, explicit: object = None) -> int:
    """反代在本机监听的端口。

    优先 `explicit`（CLI/环境显式指定），否则取公开地址的端口；落到 *前置 TLS 终结器*
    的标准端口（80/443）时退到 `DEFAULT_TLS_FRONTED_PORT` —— 那两个端口上通常站着
    nginx，抢它只会得到一个起不来的节点。
    """
    if explicit not in (None, ""):
        return int(explicit)
    parsed = urlsplit(str(public_base))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return DEFAULT_TLS_FRONTED_PORT if port in (80, 443) else int(port)


def _public_entry_server(target_port: int, public_base: str, *, listen_port: int,
                         read_timeout=DEFAULT_READ_TIMEOUT,
                         max_connections=DEFAULT_MAX_CONNECTIONS):
    if read_timeout <= 0 or max_connections < 1:
        raise ValueError('Public read timeout and connection limit must be positive')
    parsed = urlsplit(public_base)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "a2n-public-entry/1"

        def log_message(self, *_args):  # 演示/生产日志都不该被访问日志刷屏
            pass

        def handle(self):
            self.connection.settimeout(read_timeout)
            self._read_expired = False
            self._read_deadline = time.monotonic() + read_timeout
            # Header parsing uses the standard HTTP parser. Close slow header
            # connections at an absolute deadline, including on Windows.
            self._read_timer = threading.Timer(read_timeout, self._expire_read)
            self._read_timer.daemon = True
            self._read_timer.start()
            try:
                super().handle()
            finally:
                self._read_timer.cancel()

        def _expire_read(self):
            self._read_expired = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def _reject(self, status) -> None:
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _forward(self) -> None:
            self._read_timer.cancel()
            if self._read_expired:
                return
            path = urlsplit(self.path).path
            public_paths = {"/public/v1/agents", "/public/v1/samples", "/public/v1/routes", "/public/v1/witness"}
            public_paths.update({"/public/v1/trades/quote", "/public/v1/trades/points-recover"})
            public_paths.update("/public/v1/points/" + action for action in
                ("catalog", "balance", "consent", "prepare", "apply", "status", "decision"))
            public_prefixes = ("/public/v1/coord/", "/public/v1/experience/", "/public/v1/resolution/",
                               "/public/v1/settlement/", "/public/v1/settlement-mailbox/",
                               "/public/v1/payments/",
                               "/public/v1/assets/", "/public/v1/asset-mailbox/", "/public/v2/metadata-mailbox/")
            if not (path.startswith("/a2a/") or path in public_paths or path.startswith(public_prefixes)
                    or path == "/a2n/ack" or path.startswith("/relay/v1/")):
                return self._reject(404)
            lengths = self.headers.get_all('Content-Length', [])
            raw_length = lengths[0].strip() if lengths else '0'
            if (self.headers.get('Transfer-Encoding') is not None or len(lengths) > 1
                    or not raw_length.isascii() or not raw_length.isdecimal()
                    or len(raw_length) > 20):
                return self._reject(400)
            length = int(raw_length)
            limit = (196608 if path.startswith(('/public/v2/metadata-mailbox/', '/public/v1/settlement/', '/public/v1/settlement-mailbox/')) else
                     16384 if path.startswith(('/public/v1/experience/', '/public/v1/resolution/')) else
                     COORD_BODY_LIMIT if path.startswith(('/public/v1/coord/', '/public/v1/points/', '/public/v1/trades/', '/public/v1/payments/')) else
                     RELAY_BODY_LIMIT if path.startswith('/relay/v1/') else
                     ACK_BODY_LIMIT if path == '/a2n/ack' else PUBLIC_BODY_LIMIT)
            if length > limit:
                return self._reject(413)
            try:
                body = bytearray() if length else None
                while body is not None and len(body) < length:
                    remaining = self._read_deadline - time.monotonic()
                    if remaining <= 0:
                        return self._reject(408)
                    self.connection.settimeout(remaining)
                    # read1 returns after one underlying read. read(length) can
                    # reset an idle timeout for every trickled byte on Windows.
                    chunk = self.rfile.read1(min(65536, length - len(body)))
                    if not chunk:
                        return self._reject(400)
                    body.extend(chunk)
            except TimeoutError:
                return self._reject(408)
            self.connection.settimeout(60)
            self.close_connection = True
            # 经反代的请求在节点看来是**回环来源**，所以 Host 必须改写成声明过的入口，
            # 否则 `_public_card_base()` 匹配不上，网关照样 403。
            headers = {"Host": parsed.netloc,
                       "X-Forwarded-Host": parsed.netloc,
                       "X-Forwarded-Proto": parsed.scheme}
            for key, value in self.headers.items():
                if key.lower() in {"host", "content-length", "connection",
                                   "proxy-connection", "x-forwarded-host",
                                   "x-forwarded-proto"}:
                    continue
                headers[key] = value
            conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=60)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                payload = resp.read()
                self.send_response(resp.status)
                for key, value in resp.getheaders():
                    if key.lower() in {"transfer-encoding", "connection",
                                       "content-length", "server", "date"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                self._reject(502)
            finally:
                conn.close()

        do_GET = _forward
        do_POST = _forward

    return _BoundedHTTPServer(("0.0.0.0", listen_port), Handler, max_connections)


def serve_public_entry(target_port: int, public_base: str, *,
                       listen_port: int, tag: str = "public-entry",
                       read_timeout=DEFAULT_READ_TIMEOUT,
                       max_connections=DEFAULT_MAX_CONNECTIONS) -> int:
    """Start the bounded public proxy and return its actual listening port."""
    server = _public_entry_server(target_port, public_base, listen_port=listen_port,
                                  read_timeout=read_timeout, max_connections=max_connections)
    actual_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True,
                     name=f"a2n-{tag}").start()
    print(f"[{tag}] 公开入口反代 0.0.0.0:{actual_port} -> 127.0.0.1:{target_port}"
          f"（只放行 /a2a/* 与显式登记的 /public/* 公共协议）", flush=True)
    return actual_port


def env_listen_port(public_base: str) -> int:
    """按环境变量决定本机反代端口：`A2N_PUBLIC_PORT` 优先。"""
    return public_entry_port(public_base, os.environ.get("A2N_PUBLIC_PORT"))
