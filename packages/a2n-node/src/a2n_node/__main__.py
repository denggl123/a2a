"""python -m a2n_node serve --home data/my-node --port 8771"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import threading
import webbrowser


def main(argv=None):
    parser = argparse.ArgumentParser(description="A2N 本机节点")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动常驻节点与本机管理页")
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "A2N" / "node"
    serve.add_argument("--home", default=str(default))
    serve.add_argument("--port", type=int, default=8771)
    serve.add_argument("--platform", help="可选的平台入口；不配置也能导入和调用 A2A Agent")
    serve.add_argument("--principal", help="平台已有的账户身份")
    serve.add_argument("--origin", action="append", default=[], help="允许连接本机的远程控制台来源")
    serve.add_argument("--open", action="store_true", help="在浏览器打开本机管理页")
    args = parser.parse_args(argv)
    # Must happen before importing card/signing (which imports the registry/store).
    from .home import use_home
    use_home(args.home)
    from .daemon import Daemon
    daemon = Daemon(args.home, port=args.port, platform=args.platform,
                    principal=args.principal, origins=args.origin).start()
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    code = daemon.pairing.new_code()
    print(f"A2N 本机节点已启动：{daemon.runtime.local_base_url}/console", flush=True)
    print(f"配对码：{code}（5 分钟有效，只能使用一次）", flush=True)
    if args.open:
        webbrowser.open(f"{daemon.runtime.local_base_url}/console#code={code}")
    try:
        stopping.wait()
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
