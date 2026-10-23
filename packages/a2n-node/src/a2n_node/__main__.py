"""python -m a2n_node serve --home data/my-node --port 8771"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading
import webbrowser

from a2n_sdk import cli_i18n


def _bootstrap_addresses(values):
    addresses = []
    for value in values or []:
        host, sep, port = value.rpartition(":")
        host = host.strip("[] ")
        if not sep or not host:
            raise ValueError(f"BOOTSTRAP_NOT_HOST_PORT: bootstrap 必须是 host:port：{value!r}")
        number = int(port)
        if not 0 < number <= 65535:
            raise ValueError(f"BOOTSTRAP_PORT_INVALID: bootstrap 端口不合法：{value!r}")
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
    try:
        # 从 product_cli 进来时语言已经定过了，这里只是摘掉 --lang 并沿用同一个选择。
        argv = cli_i18n.configure(argv)
    except ValueError as exc:
        print(cli_i18n.error_text(exc), file=sys.stderr)
        return 2
    t = cli_i18n.t
    parser = argparse.ArgumentParser(prog="a2n-node", description=t("cli.node.description"),
                                     epilog=t("cli.common.language_hint"))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help=t("cli.node.serve.help"))
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "A2N" / "node"
    serve.add_argument("--home", default=str(default))
    serve.add_argument("--port", type=int, default=8771, help=t("cli.node.port.help"))
    serve.add_argument("--public-node", action="append", default=[], metavar="URL",
                       help=t("cli.node.public_node.help"))
    serve.add_argument("--relay-node", metavar="URL", help=t("cli.node.relay_node.help"))
    serve.add_argument("--coord-mailbox-node", action="append", default=[], metavar="URL",
                       help=t("cli.node.coord_mailbox.help"))
    serve.add_argument("--origin", action="append", default=[], help=t("cli.node.origin.help"))
    serve.add_argument("--no-p2p", action="store_true", help=t("cli.node.no_p2p.help"))
    serve.add_argument("--p2p-port", type=int, default=9701, help=t("cli.node.p2p_port.help"))
    serve.add_argument("--bootstrap", action="append", default=[], metavar="HOST:PORT",
                       help=t("cli.node.bootstrap.help"))
    serve.add_argument("--no-lan-beacon", action="store_true", help=t("cli.node.no_lan_beacon.help"))
    serve.add_argument("--p2p-advertise-host", help=t("cli.node.advertise_host.help"))
    serve.add_argument("--p2p-public-base", help=t("cli.node.public_base.help"))
    serve.add_argument("--open", action="store_true", help=t("cli.node.open.help"))
    serve.add_argument("--coord-allow-network", action="append", default=[], metavar="CIDR",
                       help=t("cli.node.coord_allow.help"))
    args = parser.parse_args(argv)
    from .process_limits import ensure_limit
    ensure_limit()
    # The node owns its directory explicitly; no process-global database.
    from .home import use_home
    use_home(args.home)
    from .daemon import Daemon
    try:
        bootstrap = _bootstrap_addresses(args.bootstrap)
    except (ValueError, TypeError) as exc:
        parser.error(cli_i18n.error_text(exc))
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
        print(t("cli.node.start_failed", error=cli_i18n.error_text(exc), port=args.port),
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print(t("cli.node.invalid_config", error=cli_i18n.error_text(exc)), file=sys.stderr)
        return 2
    stopping = daemon.stop_requested
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    print(t("cli.node.started", url=f"{daemon.runtime.local_base_url}/console"), flush=True)
    if daemon.discovery:
        mode = t("cli.node.p2p.advertise") if args.p2p_public_base else t("cli.node.p2p.discover")
        print(t("cli.node.p2p", port=args.p2p_port, mode=mode), flush=True)
    if args.open:
        webbrowser.open(f"{daemon.runtime.local_base_url}/console")
    try:
        stopping.wait()
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
