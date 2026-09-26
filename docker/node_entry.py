"""容器里的「行业案例」节点 —— 这次容器跑的是**那份产品本体 a2n-node**。

为什么另起一个入口（2026-09-25 用户拍板）
-----------------------------------------
用户的目标原话：「本机节点启动后，控制台就能自动发现（就像蓝牙一样），发现后
把 docker 里的 agent 展示出来」；并明确「**3 个容器，就是本项目代码镜像**」。

于是容器不再只跑一个"会注册平台的供给脚本"（`market_node.py`，SDK `Node`），
而是跑 `a2n_node`：

* 它有自己的**控制台**（同一份 runtime.html）、自己的**开关**、自己的 **P2P 面**；
* 它对外的供给仍可通过平台发布（沿用 `RuntimePlatformBridge` 的反向隧道，
  行为与旧的 SDK Node 一致），同时**额外**以 P2P 直邻的身份被别的节点发现；
* 也就是说：容器 = 公网上的另一台真机器，不是"平台的一部分"。

一个容器 = 一个身份，但身份**不变**
------------------------------------
旧入口把钥匙落在命名卷 `STATE_DIR/node.key.json`，`uid` 由 did 派生，
所以"重启复用同一条上架"依赖那把钥匙活过 `docker rm -f`。

a2n-node 的身份来自它自己的库 `HOME/runtime.db`（`identity/seed` = base64(私钥)）。
为了让**迁移后的 did 与迁移前完全一致**（否则平台会多出一整批新上架，
演示里的"重启不重复"当场作废），本入口在起节点前做一件事：

    读 STATE_DIR/node.key.json 的私钥（urlsafe 无填充 base64）
    → 换算成节点库口径（标准带填充 base64）写进 HOME/runtime.db 的 identity/seed

同一把私钥、两种编码 —— 换算一次，不是"抄一把新钥匙"，也不是把两种编码混用
（混用会得到 `binascii.Error: Incorrect padding`，2026-09-25 真踩过）。
没有 node.key.json（全新容器）就现生成一把并同时写进两处。

环境变量
--------
  A2N_CONTAINER       要起哪个容器（video-studio / finance-legal / play-ecom）
  A2N_HOME            节点目录（默认 STATE_DIR/node）—— runtime.db 落这里，挂卷
  A2N_STATE_DIR       钥匙/数据目录（默认 /app/state）
  A2N_PORT            节点 HTTP 端口（控制台 + A2A 入口，默认 8787）
  A2N_P2P_PORT        P2P 发现 UDP 端口（默认 9711）
  A2N_BOOTSTRAP       逗号分隔的 P2P 冷启动邻居 host:port（默认空）
  A2N_PUBLIC_BASE     本节点对外可访问的 HTTP 入口；**不配就不广播供给**
  A2N_ADVERTISE_HOST  诊断显示的发现地址（回程路由始终用实测源地址）
  A2N_PLATFORM        可选平台入口；配了就把供给发布上去（沿用反向隧道）
  A2N_DEAD_AGENT      上游 agent 地址（默认 http://127.0.0.1:9/invoke，本地没人听）
  A2N_STORAGE_KEY     凭据库口令；Linux 上 system_protector 不允许明文落凭据，
                      所以**必须注入**（见 docker/Dockerfile 注释）

最后一跳**真的不通**（用户 2026-09-20 原话）
----------------------------------------------
节点会**真的**去连一台不存在的上游 agent（`A2N_DEAD_AGENT`），连不上的真实错误
原样上报为失败原因。演示里最该看的就是这一段：发现通、对方节点活、请求也送到了；
断在它转发给自己那台 agent。这里不做任何"写死的失败文案"。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 同一份仓库代码，镜像按 packages/<pkg>/src/<mod>/ 的形状铺好（见 docker/Dockerfile）。
sys.path.insert(0, os.environ.get("A2N_SCRIPTS_DIR", "/app/scripts"))

import run_market_demo_agents as catalog  # noqa: E402

from a2n_p2p import Identity  # noqa: E402
# 钥匙文件的编码是 urlsafe 无填充（`Identity.save` 的 `_b64`），而节点库的
# `identity/seed` 是标准带填充 base64。两者**不是同一个字符串**，必须经这把
# 唯一的解码器换算 —— 直接抄 `sk` 进 seed 会在 Daemon 里炸 Incorrect padding（真踩过）。
from a2n_p2p.identity import _unb64  # noqa: E402
from a2n_node.home import use_home  # noqa: E402
from a2n_node.daemon import Daemon  # noqa: E402
from a2n_sdk.client import Client  # noqa: E402
from a2n_sdk.storage import LocalStore  # noqa: E402

import base64  # noqa: E402
import http.client  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402


CONTAINER = os.environ.get("A2N_CONTAINER", "")
STATE_DIR = Path(os.environ.get("A2N_STATE_DIR", "/app/state"))
HOME = Path(os.environ.get("A2N_HOME") or (STATE_DIR / "node"))
PORT = int(os.environ.get("A2N_PORT", "8787"))
P2P_PORT = int(os.environ.get("A2N_P2P_PORT", "9711"))
PUBLIC_BASE = os.environ.get("A2N_PUBLIC_BASE") or None
ADVERTISE_HOST = os.environ.get("A2N_ADVERTISE_HOST", "0.0.0.0")
PLATFORM = os.environ.get("A2N_PLATFORM") or None
DEAD_AGENT = os.environ.get("A2N_DEAD_AGENT", "http://127.0.0.1:9/invoke")
KEYFILE = STATE_DIR / "node.key.json"
# 公共入口在本容器内监听的端口（= A2N_PUBLIC_BASE 里的端口）。为空则不起反代。
PUBLIC_PORT = int(urlsplit(PUBLIC_BASE).port) if PUBLIC_BASE else 0

CONTAINERS = {c[0]: c for c in catalog.CONTAINERS}
BY_SLUG = {p[0]: p for p in catalog.MARKET}


def _bootstrap() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for raw in (os.environ.get("A2N_BOOTSTRAP") or "").split(","):
        text = raw.strip()
        if not text:
            continue
        host, sep, port = text.rpartition(":")
        host = host.strip("[] ")
        if not sep or not host:
            raise SystemExit(f"A2N_BOOTSTRAP 必须是 host:port，收到 {text!r}")
        out.append((host, int(port)))
    return out


def _seed_from_keyfile(path: Path) -> tuple[Identity, str]:
    """把钥匙文件里的私钥换算成"节点库口径"的 seed。

    钥匙文件存的是 urlsafe 无填充 base64（`Identity.save._b64`），节点库的
    `identity/seed` 是标准带填充 base64 —— 同一把私钥、两种写法。这里做**一次**
    换算，而不是把两种编码混着用（混着用会得到 Incorrect padding）。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = _unb64(data["sk"])
    return Identity.from_private_bytes(raw), base64.b64encode(raw).decode("ascii")


def seed_identity() -> Identity:
    """确保 HOME/runtime.db 里的身份 = STATE_DIR/node.key.json 那把钥匙。

    did 必须与迁移前（旧入口 market_node.py）完全一致：平台上的 uid = uuid5(did + "/market/" + slug)，
    换了 did 就会在平台上多出一整批新上架，"重启不重复"当场作废。
    没有 node.key.json（全新容器）就现生成一把，并同时写进钥匙文件与节点库。
    """
    HOME.mkdir(parents=True, exist_ok=True)
    if KEYFILE.exists():
        identity, seed_b64 = _seed_from_keyfile(KEYFILE)
    else:
        identity = Identity.generate()
        identity.save(KEYFILE)
        identity, seed_b64 = _seed_from_keyfile(KEYFILE)
        print(f"[market-node] 新身份已生成 {KEYFILE}（{identity.did}）", flush=True)

    # system_protector() 在 Linux 上必须有 A2N_STORAGE_KEY，否则直接抛错 ——
    # 宁可起不来，也不许把凭据明文落盘。
    from a2n_node.protection import system_protector
    store = LocalStore(HOME / "runtime.db", system_protector())
    saved = store.get("identity", "seed")
    if saved:
        existing = Identity.from_private_bytes(base64.b64decode(saved))
        if existing.did != identity.did:
            raise SystemExit(
                f"节点库里的身份（{existing.did}）与钥匙文件（{identity.did}）不一致；"
                f"拒绝用两个身份起一个节点")
        return existing
    store.put("identity", "seed", seed_b64)
    return identity


def _existing_agent_id(client: Client, card: dict) -> str | None:
    """迁移到 a2n-node 后第一次发布时，平台里可能已经有同 uid 的上架。

    旧入口（SDK Node）注册的条目就在平台上，uid 由 did 派生 —— 迁移后 did 不变，
    所以 uid 不变。若直接 register 会 409，因此先按 uid 在自己名下找那条，
    用它的 agent_id 走 update 分支，而不是放弃或另插一条。
    """
    uid = (card.get("x-a2n") or {}).get("uid")
    if not uid:
        return None
    try:
        for row in client.my_agents():
            try:
                old = json.loads(row.get("card_json") or "{}")
            except (TypeError, ValueError):
                continue
            if (old.get("x-a2n") or {}).get("uid") == uid:
                return str(row["agent_id"])
    except Exception:  # noqa: BLE001 - 平台不可用时不该拖垮节点本体
        return None
    return None


def mount_and_publish(daemon: Daemon, identity: Identity, profiles: list) -> None:
    for profile in profiles:
        slug, name, skill = profile[0], profile[1], profile[2]
        card = catalog.build_card(profile, identity)
        status, out = daemon.management.command(
            "/v1/bindings/http",
            {"card": card, "endpoint": DEAD_AGENT, "protocol": "a2a"})
        if status not in (200, 201):
            raise SystemExit(f"[market-node] 挂载 {name} 失败：{out}")
        service_id = out["service_id"]
        print(f"[market-node] 已挂载 {name} · {skill} · 上游 {DEAD_AGENT}（按设计连不上）",
              flush=True)
        if not PLATFORM:
            continue
        agent_id = _existing_agent_id(
            Client(PLATFORM, principal=identity.did), card)
        status, published = daemon.management.command(
            "/v1/publish", {"service_id": service_id, "agent_id": agent_id})
        if status not in (200, 201):
            raise SystemExit(f"[market-node] 发布 {name} 到平台失败：{published}")
        print(f"[market-node] 已发布到平台 {name} -> agent_id={published.get('agent_id')}",
              flush=True)


def start_public_entry(target_port: int, public_base: str) -> int:
    """起一个**显式**的本机反代，充当本节点声明的公共 HTTP 入口。

    为什么必须有它（2026-09-25 实测结论）
    -------------------------------------
    `a2n_sdk.gateway` 只服务**回环来源**：`/a2a/<sid>` 的取卡与调用都要求
    `_local()` 为真，或在声明了 `--p2p-public-base` 时精确匹配该入口
    （`_public_card_base`）。文档口径是"公共入口由**同机反向代理**提供，代理保留
    `Host` 或传入匹配的 `X-Forwarded-Host/Proto`"（见 docs/SDK-RUNTIME.md）。

    容器里没有这个代理时会发生什么：节点绑的是 `127.0.0.1:PORT`，docker 端口发布
    投递到容器 eth0，容器内没人监听 → 从主机看是连接被挂断（`curl` 得到
    `Empty reply from server`）。所以容器要"被别的节点当成一台真机器访问"，
    就必须自带这个回环反代。

    **只放行公开展示面**：路径必须以 `/a2a/` 开头（投影卡与 A2A 调用）。
    `/v1/*`（账户、配置、任务列表）、`/console`、`/health` 一律 404 —— 因为经本机
    反代的请求在节点看来来源是回环，若照单全收，等于把管理面开给了所有能连到
    这个端口的人。这是"最小放行"，不是"顺手全开"。
    """
    parsed = urlsplit(public_base)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "a2n-public-entry/1"

        def log_message(self, *_args):  # 不往 stdout 刷访问日志（演示日志要干净）
            pass

        def _deny(self) -> None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _forward(self) -> None:
            path = urlsplit(self.path).path
            if not path.startswith("/a2a/"):
                return self._deny()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
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

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="a2n-public-entry").start()
    print(f"[market-node] 公共入口反代已起：0.0.0.0:{PUBLIC_PORT} -> "
          f"127.0.0.1:{target_port}（只放行 /a2a/*）", flush=True)
    return PUBLIC_PORT


def main() -> None:
    if CONTAINER not in CONTAINERS:
        raise SystemExit(
            f"A2N_CONTAINER 必须是 {sorted(CONTAINERS)} 之一，收到 {CONTAINER!r}")
    _slug, name, member_slugs = CONTAINERS[CONTAINER]
    profiles = [BY_SLUG[s] for s in member_slugs]

    use_home(str(HOME))
    identity = seed_identity()
    bootstrap = _bootstrap()
    print(f"[market-node] 容器 {name} · 节点身份 {identity.did} · "
          f"HTTP {PORT} · P2P UDP {P2P_PORT} · 供给 {len(profiles)} 份 · "
          f"公共入口 {PUBLIC_BASE or '未声明（只发现、不广播供给）'}", flush=True)

    daemon = Daemon(
        str(HOME), port=PORT, platform=PLATFORM, p2p_port=P2P_PORT,
        bootstrap=bootstrap, beacon=False, advertise_host=ADVERTISE_HOST,
        discovery_public_base=PUBLIC_BASE).start()
    try:
        if PUBLIC_BASE and PUBLIC_PORT:
            start_public_entry(PORT, PUBLIC_BASE)
        mount_and_publish(daemon, identity, profiles)
        print(f"[market-node] 节点就绪：{daemon.runtime.local_base_url}/console", flush=True)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()


if __name__ == "__main__":
    main()
