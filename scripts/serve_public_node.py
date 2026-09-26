"""把一台**公网机器**跑成一个 A2N 节点（同一份软件，不用 docker）。

和 `docker/node_entry.py` 是同一件事的两种皮：起真 `Daemon`（控制台 + P2P + 公益开关）、
把案例供给挂上去、起一个同机反代当公开 HTTP 入口。差别只有三处：

* 身份直接用节点库自生成的 seed（容器那份要从旧钥匙文件换算，为了 did 不变）；
* 本机反代端口与公开入口端口的解耦在这里是**必须**的：公网机通常已经跑着 nginx，
  公开地址写 `https://host`（443），本机反代要是照抄 443 就会**因为端口被占而起不来**；
* 常见做法是前面挂一个 TLS 终结器（cloudflared 隧道 / nginx / 负载均衡），
  于是公开地址是 https，本机监听还是明文 HTTP。

为什么需要同机反代：`a2n_sdk.gateway` 只服务**回环来源**（`_local()`），节点本体只绑
`127.0.0.1`。反代只放行 `/a2a/*` 与 `/public/*`，`/v1/*`、`/console`、`/health` 一律
404（实现见 `a2n_node/public_entry.py`）。

环境变量
--------
  A2N_PUBLIC_BASE   对外入口（必填）。如 https://a2n.example.com 或 http://1.2.3.4:8891
  A2N_PUBLIC_PORT   本机反代监听端口；前置 TLS 终结器时**必须**另给（默认见 public_entry）
  A2N_PORT          节点 HTTP 端口（回环），默认 8890
  A2N_P2P_PORT      P2P 发现 UDP 端口，默认 9711
  A2N_HOME          节点目录，默认 /opt/a2n/node-home
  A2N_CONTAINER     挂哪个案例组（video-studio / finance-legal / play-ecom）
  A2N_PUBLIC_SERVICE 是否开公益开关（自愿公共目录），默认 1
  A2N_STORAGE_KEY   凭据库口令；Linux 上 system_protector 不允许明文落盘，**必须注入**

注意 `A2N_PUBLIC_BASE` 用的是 **https** 时，别的节点把它当目录源才收得下：
`PublicDirectoryClient.normalize_base` 只放行"不离开本机"的明文 HTTP
（回环 / localhost / host.docker.internal），公网地址一律要求 HTTPS —— 明文会让
"它到底是不是那个节点"变成不可判断。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 直接跑仓库脚本时把 packages/*/src 挂上（和 sim_start.sh / node_entry.py 同一种铺法）
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(p) for p in sorted(ROOT.glob("packages/*/src"))]
sys.path.insert(0, str(ROOT / "scripts"))

import run_market_demo_agents as catalog  # noqa: E402

from a2n_node.daemon import Daemon  # noqa: E402
from a2n_node.home import use_home  # noqa: E402
from a2n_node.public_entry import env_listen_port, serve_public_entry  # noqa: E402
from a2n_node.supply import mount_supply, open_public_service  # noqa: E402

PUBLIC_BASE = (os.environ.get("A2N_PUBLIC_BASE") or "").rstrip("/")
NODE_PORT = int(os.environ.get("A2N_PORT", "8890"))
P2P_PORT = int(os.environ.get("A2N_P2P_PORT", "9711"))
HOME = Path(os.environ.get("A2N_HOME", "/opt/a2n/node-home"))
CONTAINER = os.environ.get("A2N_CONTAINER", "video-studio")
DEAD_AGENT = os.environ.get("A2N_DEAD_AGENT", "http://127.0.0.1:9/invoke")
PUBLIC_SERVICE = os.environ.get("A2N_PUBLIC_SERVICE", "1") != "0"

CONTAINERS = {c[0]: c for c in catalog.CONTAINERS}
BY_SLUG = {p[0]: p for p in catalog.MARKET}


def main() -> None:
    if not PUBLIC_BASE:
        raise SystemExit("必须给 A2N_PUBLIC_BASE（本节点对外可访问的入口）")
    if CONTAINER not in CONTAINERS:
        raise SystemExit(f"A2N_CONTAINER 必须是 {sorted(CONTAINERS)} 之一，收到 {CONTAINER!r}")

    _slug, name, members = CONTAINERS[CONTAINER]
    listen_port = env_listen_port(PUBLIC_BASE)
    advertise_host = PUBLIC_BASE.split("//", 1)[-1].split("/")[0].split(":")[0]

    use_home(str(HOME))
    daemon = Daemon(str(HOME), port=NODE_PORT, p2p_port=P2P_PORT, beacon=False,
                    advertise_host=advertise_host,
                    discovery_public_base=PUBLIC_BASE).start()
    print(f"[public-node] 节点 {name} · did={daemon.identity.did} · HTTP {NODE_PORT} · "
          f"P2P UDP {P2P_PORT} · 公开入口 {PUBLIC_BASE}（本机反代 {listen_port}）",
          flush=True)

    serve_public_entry(NODE_PORT, PUBLIC_BASE, listen_port=listen_port, tag="public-node")

    # 挂供给必须**幂等**：节点目录是持久化的，重启时 `RuntimeManagement.restore()`
    # 已经把上次的挂载重新挂上了，盲目再挂会撞"service_id 已存在"而让节点起不来。
    mount_supply(daemon, daemon.identity, [BY_SLUG[s] for s in members],
                 build_card=catalog.build_card, endpoint=DEAD_AGENT, tag="public-node")

    open_public_service(daemon, PUBLIC_BASE, enabled=PUBLIC_SERVICE, tag="public-node")

    print("[public-node] READY", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()


if __name__ == "__main__":
    main()
