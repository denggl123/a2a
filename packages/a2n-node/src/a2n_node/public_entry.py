"""同机反向代理 —— 节点的「公开 HTTP 入口」。

为什么必须有这个反代
--------------------
`a2n_sdk.gateway` 只服务**回环来源**（`_local()` 要求 `client_address` 是回环），
而节点本体只绑 `127.0.0.1`。文档口径是"公共入口由**同机反向代理**提供，代理保留
`Host` 或传入匹配的 `X-Forwarded-Host/Proto`"（见 `docs/SDK-RUNTIME.md`）。
没有它，从别的机器连上来只会看到连接被挂断。

放行面**只有公开展示面**：`/a2a/*`（投影卡与 A2A 调用）与 `/public/*`（自愿公共目录
与见证）。`/v1/*`（账户、挂载、任务列表）、`/console`、`/health` 一律 404 ——
经反代的请求在节点看来来源是回环，照单全收等于把管理面开给所有能连到该端口的人。
这是"最小放行"，不是"顺手全开"。

本机监听端口 ≠ 公开端口
----------------------
`https://host` 这种公开地址（前置 TLS 终结器：云端隧道 / nginx / 负载均衡）端口是 443，
但本机不能去抢 443：那里通常已经被 nginx 占着，硬绑定会**直接起不来**（2026-09-26 真踩过）。
所以本机端口由 `public_entry_port()` 单独决定，可显式给 `A2N_PUBLIC_PORT` 覆盖。

沿用历史：`docker/node_entry.py` 最初私有一份同样的反代；两台机器的部署各写一份之后
就漂了（一处忘了 /public/*、一处把端口算错），所以抽到这里单一实现。
"""
from __future__ import annotations

import http.client
import http.server
import os
import threading
from urllib.parse import urlsplit

DEFAULT_TLS_FRONTED_PORT = 8891


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


def serve_public_entry(target_port: int, public_base: str, *,
                       listen_port: int, tag: str = "public-entry") -> int:
    """起一个只放行公开展示面的同机反代，返回实际监听端口。"""
    parsed = urlsplit(public_base)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "a2n-public-entry/1"

        def log_message(self, *_args):  # 演示/生产日志都不该被访问日志刷屏
            pass

        def _deny(self) -> None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _forward(self) -> None:
            path = urlsplit(self.path).path
            if not (path.startswith("/a2a/") or path.startswith("/public/")):
                return self._deny()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
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
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
            finally:
                conn.close()

        do_GET = _forward
        do_POST = _forward

    server = http.server.ThreadingHTTPServer(("0.0.0.0", listen_port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True,
                     name=f"a2n-{tag}").start()
    print(f"[{tag}] 公开入口反代 0.0.0.0:{listen_port} -> 127.0.0.1:{target_port}"
          f"（只放行 /a2a/* 与 /public/*）", flush=True)
    return listen_port


def env_listen_port(public_base: str) -> int:
    """按环境变量决定本机反代端口：`A2N_PUBLIC_PORT` 优先。"""
    return public_entry_port(public_base, os.environ.get("A2N_PUBLIC_PORT"))
