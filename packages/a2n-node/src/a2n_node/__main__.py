"""python -m a2n_node serve --home data/my-node --port 8771"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading
import webbrowser


def _bootstrap_addresses(values):
    addresses = []
    for value in values or []:
        host, sep, port = value.rpartition(":")
        host = host.strip("[] ")
        if not sep or not host:
            raise ValueError(f"bootstrap 必须是 host:port：{value!r}")
        number = int(port)
        if not 0 < number <= 65535:
            raise ValueError(f"bootstrap 端口不合法：{value!r}")
        addresses.append((host, number))
    return addresses


def _lan_host():
    from a2n_sdk.connection import local_ips
    return next((ip for ip in local_ips()
                 if ":" not in ip and not ip.startswith("127.")), "127.0.0.1")


def main(argv=None):
    # Node logs are consumed by launchers and dashboards.  Make their encoding
    # deterministic instead of inheriting a Windows console code page.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="A2N 本机节点")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动常驻节点与本机管理页")
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "A2N" / "node"
    serve.add_argument("--home", default=str(default))
    serve.add_argument("--port", type=int, default=8771,
                       help="本机管理页与 A2A 入口端口（默认 8771）")
    serve.add_argument("--public-node", action="append", default=[], metavar="URL",
                       help="可选的自愿公共节点基础 URL，可重复指定；远端需 HTTPS")
    serve.add_argument("--relay-node", metavar="URL",
                       help="可选的自愿密封中继节点；本机主动领活，无需公网入站，远端需 HTTPS")
    serve.add_argument("--coord-mailbox-node", action="append", default=[], metavar="URL",
                       help="指定出站协调邮箱；有局域网公开入口的节点也可借它接受公网发现")
    serve.add_argument("--origin", action="append", default=[], help="允许连接本机的远程控制台来源")
    serve.add_argument("--no-p2p", action="store_true", help="关闭局域网/种子节点发现")
    serve.add_argument("--p2p-port", type=int, default=9701, help="P2P 发现 UDP 端口")
    serve.add_argument("--bootstrap", action="append", default=[], metavar="HOST:PORT",
                       help="P2P 冷启动邻居，可重复指定")
    serve.add_argument("--no-lan-beacon", action="store_true", help="关闭局域网零配置广播")
    serve.add_argument("--p2p-advertise-host",
                       help="诊断显示的发现地址；回程路由始终使用实测源地址")
    serve.add_argument("--p2p-public-base",
                       help="明确可被其他节点访问的 HTTP 入口；未配置时只发现、不广播供给")
    serve.add_argument("--open", action="store_true", help="在浏览器打开本机管理页")
    serve.add_argument("--coord-allow-network", action="append", default=[], metavar="CIDR",
                       help="明确允许协调引荐访问的可信网络，可重复；局域网试点例如 192.168.1.0/24")
    args = parser.parse_args(argv)
    # The node owns its directory explicitly; no process-global database.
    from .home import use_home
    use_home(args.home)
    from .daemon import Daemon
    try:
        bootstrap = _bootstrap_addresses(args.bootstrap)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    try:
        daemon = Daemon(args.home, port=args.port, origins=args.origin,
                        p2p_port=None if args.no_p2p else args.p2p_port,
                        bootstrap=bootstrap, beacon=not args.no_lan_beacon,
                        advertise_host=args.p2p_advertise_host or _lan_host(),
                        discovery_public_base=args.p2p_public_base,
                        public_nodes=args.public_node,
                        relay_node=args.relay_node,
                        coord_mailbox_nodes=args.coord_mailbox_node,
                        coord_allow_networks=args.coord_allow_network).start()
    except OSError as exc:
        print(f"A2N 节点启动失败：{exc}；检查端口 {args.port} 或另选 --port", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"A2N 节点配置无效：{exc}", file=sys.stderr)
        return 2
    stopping = daemon.stop_requested
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    print(f"A2N 本机节点已启动：{daemon.runtime.local_base_url}/console", flush=True)
    if daemon.discovery:
        mode = "发现 + 广播供给" if args.p2p_public_base else "仅发现（未声明对外 HTTP 入口）"
        print(f"P2P：UDP {args.p2p_port} · {mode}", flush=True)
    if args.open:
        webbrowser.open(f"{daemon.runtime.local_base_url}/console")
    try:
        stopping.wait()
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
