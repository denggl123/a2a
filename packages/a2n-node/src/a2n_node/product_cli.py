"""Whole desktop SDK product entry; the dependency-free Python SDK remains a library."""
from __future__ import annotations

import argparse
import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
import webbrowser

MODULES = ("a2n_kernel", "a2n_p2p", "a2n_acceptance", "a2n_sdk", "a2n_node")


def default_home():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "A2N" / "node"


def health(port, home=None):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=.5) as r:
            result = json.loads(r.read(4096))
        correct_home = home is None or result.get("instance_key") == hashlib.sha256(
            os.path.normcase(str(Path(home).expanduser().resolve())).encode()).hexdigest()
        return result.get("ok") is True and result.get("service") == "a2n-runtime" and correct_home
    except (OSError, ValueError):
        return False


def doctor():
    """Import every installed package, then smoke test an isolated full node."""
    # A subprocess owns this temporary home. No existing identity/database is read.
    with tempfile.TemporaryDirectory(prefix="a2n-doctor-") as tmp:
        from .home import use_home
        use_home(tmp)
        for module in MODULES:
            importlib.import_module(module)
        from .daemon import Daemon
        from .protection import system_protector
        # Real platform protection is part of install readiness (DPAPI on Windows).
        daemon = Daemon(tmp, port=0, protector=system_protector()).start()
        try:
            daemon.store.put("doctor", "roundtrip", {"ok": True})
            if daemon.store.get("doctor", "roundtrip") != {"ok": True}:
                raise RuntimeError("加密存储读写检查失败")
            port = urlsplit(daemon.runtime.local_base_url).port
            if not health(port):
                raise RuntimeError("本机 HTTP 入口未就绪")
            request = daemon.coord_network._envelope("PROBE", {"nonce": "doctor"}, daemon.identity.did)
            from a2n_sdk.coordination import CoordFailure
            try:
                reply = daemon.public_coordination.handle("PROBE", request)
            except CoordFailure as exc:
                # 认证后的失败现在抛 CoordFailure（带已签名 ERROR 封套），不再是裸异常。
                raise RuntimeError(f"本机公共协调自检失败：{exc}") from exc
            daemon.coord_network._check_response(reply, request, daemon.identity.did)
            if not all((daemon.calls, daemon.trials, daemon.feedback, daemon.coordination,
                        daemon.public_coordination, daemon.coord_mailbox, daemon.coord_neighbors)):
                raise RuntimeError("产品模块装配不完整")
            print(json.dumps({"ok": True, "packages": len(MODULES), "product": "A2N desktop SDK",
                              "features": ["console", "supply", "projection", "signed_calls", "trials",
                                           "samples", "bilateral_feedback", "coordination", "coord_mailbox",
                                           "coord_neighbors"],
                              "settlement": "not_configured"}, ensure_ascii=False))
        finally:
            daemon.stop()
    return 0


def start_background(home, port, extra, *, open_browser=False):
    if port <= 0:
        raise ValueError("后台启动必须指定固定端口")
    url = f"http://127.0.0.1:{port}/console"
    if health(port):
        if not health(port, home):
            raise RuntimeError(f"端口 {port} 已由另一份节点目录使用，请另选 --port")
        print(f"本机节点已在运行：{url}")
        if open_browser:
            webbrowser.open(url)
        return 0
    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    log = home / "node.log"
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with log.open("ab", buffering=0) as output:
        process = subprocess.Popen([sys.executable, "-m", "a2n_node", "serve", "--home", str(home),
                                    "--port", str(port), *extra], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, **kwargs)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(f"节点启动失败，查看日志：{log}")
        if health(port, home):
            print(f"完整 SDK 已启动：{url}\n节点数据和日志：{home}")
            if open_browser:
                webbrowser.open(url)
            return 0
        time.sleep(.1)
    # Keep a slow healthy startup observable; report failure instead of fake success.
    raise RuntimeError(f"节点尚未就绪，查看日志：{log}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="a2n-sdk", description="A2N 完整桌面节点：安装全套、启动、自检")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="启动完整节点与控制台")
    start.add_argument("--home", default=str(default_home()))
    start.add_argument("--port", type=int, default=8771)
    start.add_argument("--background", action="store_true", help="后台运行，日志写入节点目录")
    start.add_argument("--open", action="store_true", help="打开控制台")
    sub.add_parser("doctor", help="检查全部包、加密存储、完整装配与本机入口")
    for name in ("request", "stop"):
        command = sub.add_parser(name, help="调用受保护的本机 API" if name == "request" else "正常停止本机节点")
        command.add_argument("--home", default=os.environ.get("A2N_HOME") or str(default_home()))
        command.add_argument("--port", type=int, default=int(os.environ.get("A2N_PORT", "8771")))
    args, extra = parser.parse_known_args(argv if argv is not None else sys.argv[1:] or ["start", "--open"])
    try:
        if args.command in {"request", "stop"}:
            from a2n_sdk.client import NodeClient
            from a2n_sdk.storage import LocalStore
            from .protection import system_protector
            if not health(args.port, args.home):
                raise RuntimeError("指定节点尚未就绪，或端口与节点目录不匹配")
            store = LocalStore(Path(args.home) / "runtime.db", system_protector())
            try:
                access = store.get("runtime_control", "access") or {}
            finally:
                store.close()
            if not access.get("token"):
                raise RuntimeError("节点未保存本机控制凭据，请重启节点")
            client = NodeClient(f"http://127.0.0.1:{args.port}", token=access["token"])
            if args.command == "stop":
                print(json.dumps(client.request("POST", "/v1/node/stop", {})))
                return 0
            from a2n_sdk.__main__ import main as request
            return request(extra, client=client)
        if args.command == "doctor":
            if extra:
                parser.error("doctor 不接受额外参数")
            return doctor()
        if args.command == "start":
            if args.background:
                return start_background(args.home, args.port, extra, open_browser=args.open)
            from .__main__ import main as serve
            return serve(["serve", "--home", args.home, "--port", str(args.port),
                          *(["--open"] if args.open else []), *extra])
        parser.print_help()
        return 2
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"SDK 操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
