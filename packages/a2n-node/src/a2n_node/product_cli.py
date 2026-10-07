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


def runtime_info():
    bundled = bool(getattr(sys, "frozen", False))
    digest = None
    if bundled:
        hasher = hashlib.sha256()
        with Path(sys.executable).open("rb") as stream:
            while chunk := stream.read(262144):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return {"mode": "BUNDLED" if bundled else "SOURCE", "pid": os.getpid(), "executable_sha256": digest}


def runtime_command(module, arguments=(), *, executable=None):
    binary = executable or sys.executable
    bundled = getattr(sys, "frozen", False) or Path(binary).name.lower() == "a2n.exe"
    return ([binary, "internal-run", module] if bundled else [binary, "-m", module]) + list(arguments)


def default_home():
    if os.environ.get('A2N_HOME'):
        return Path(os.environ['A2N_HOME']).expanduser().resolve()
    # A portable installation can keep one shared home beside its launchers.
    # This also avoids different user-profile file views in scheduled sessions.
    config = Path.cwd() / '.a2n-local.json'
    if config.exists():
        home = Path(json.loads(config.read_text(encoding='utf-8-sig'))['home'])
        if not home.is_absolute():
            raise ValueError('Portable SDK home must be an absolute path')
        return home
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "A2N" / "node"


def health_snapshot(port):
    """Read one validated response so a stop cannot split readiness/home checks."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=.5) as r:
            result = json.loads(r.read(4096))
        return result if result.get("ok") is True and result.get("service") == "a2n-runtime" else None
    except (OSError, ValueError):
        return None


def health(port, home=None):
    result = health_snapshot(port)
    correct_home = home is None or (result or {}).get("instance_key") == hashlib.sha256(
        os.path.normcase(str(Path(home).expanduser().resolve())).encode()).hexdigest()
    return result is not None and correct_home


def doctor(report_path=None):
    """Import every installed package, then smoke test an isolated full node."""
    # A subprocess owns this temporary home. No existing identity/database is read.
    with tempfile.TemporaryDirectory(prefix="a2n-doctor-") as tmp:
        from .home import use_home
        use_home(tmp)
        for module in MODULES:
            importlib.import_module(module)
        # These native backends load lazily during ordinary node startup. Verify
        # them before advertising a complete installation or accepting an upgrade.
        from wasmtime import Engine, Module
        Module(Engine(), '(module)')
        from PIL import Image
        import io
        image_bytes = io.BytesIO()
        Image.new('RGB', (1, 1)).save(image_bytes, format='PNG')
        if not image_bytes.getvalue().startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError('图像预览执行后端未就绪')
        from .x402.evm import self_test as x402_self_test
        x402_self_test()
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
            report = {"ok": True, "packages": len(MODULES), "product": "A2N desktop SDK",
                              "backends": {"wasm_compile": True, "png_encode": True, "x402_signatures": True},
                              "features": ["console", "supply", "projection", "signed_calls", "trials",
                                           "samples", "bilateral_feedback", "coordination", "coord_mailbox",
                                           "coord_neighbors", "public_experience", "reputation_shadow", "local_policy",
                                           "metadata_mailbox_v2", "portable_backup", "bilateral_negotiation",
                                           "fixed_free_contracts", "reconnect_sampling", "wasm_agent_packages", "private_assets", "encrypted_asset_mailbox", "x402_v2_exact_evm"],
                              "settlement": "not_configured"}
            if report_path:
                Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            daemon.stop()
    return 0


def start_background(home, port, extra, *, open_browser=False, executable=None):
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
    module = "a2n_node.desktop" if not extra else "a2n_node"
    command = runtime_command(module, [*(["serve"] if module == "a2n_node" else []),
        "--home", str(home), "--port", str(port), *extra], executable=executable)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    if getattr(sys, "frozen", False):
        # The daemon outlives this launcher and needs its own extracted runtime.
        kwargs["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    with log.open("ab", buffering=0) as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, **kwargs)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if health(port, home):
            print(f"完整 SDK 已启动：{url}\n节点数据和日志：{home}")
            if open_browser:
                webbrowser.open(url)
            return 0
        if process.poll() is not None:
            # A duplicate launcher may have exited normally after reusing the
            # serving process. Check that process before interpreting the exit.
            raise RuntimeError(f"节点启动失败，查看日志：{log}")
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
    doctor_parser = sub.add_parser("doctor", help="检查全部包、加密存储、完整装配与本机入口")
    doctor_parser.add_argument("--report")
    autostart = sub.add_parser("autostart", help="注册当前用户登录后常驻的完整 SDK")
    autostart.add_argument("--home", default=str(default_home()))
    autostart.add_argument("--port", type=int, default=8771)
    autostart.add_argument("--dry-run", action="store_true")
    restore = sub.add_parser("restore", help="停机后用恢复口令导入便携备份")
    restore.add_argument("path")
    restore.add_argument("--home", default=str(default_home()))
    restore.add_argument("--replace-identity", action="store_true")
    restore.add_argument("--restore-network", action="store_true")
    restore.add_argument("--password-file", help="从受保护的本机文件读取口令，适用于无终端的单文件程序")
    for name in ("request", "stop"):
        command = sub.add_parser(name, help="调用受保护的本机 API" if name == "request" else "正常停止本机节点",
            description=("管理参数：--path PATH、--method GET|POST|DELETE、--body JSON（- 从标准输入读取）、--header NAME:VALUE；由统一 SDK API 命令解析。"
                         if name == "request" else None))
        command.add_argument("--home", default=os.environ.get("A2N_HOME") or str(default_home()))
        command.add_argument("--port", type=int, default=int(os.environ.get("A2N_PORT", "8771")))
    args, extra = parser.parse_known_args(argv if argv is not None else sys.argv[1:] or ["start", "--open"])
    try:
        if args.command == "autostart":
            from .autostart import enable
            print(json.dumps(enable(args.home, args.port, dry_run=args.dry_run), ensure_ascii=False))
            return 0
        if args.command == "restore":
            import getpass
            from .backups import restore_offline
            from .protection import system_protector
            if args.password_file:
                path = Path(args.password_file)
                if not 12 <= path.stat().st_size <= 8192:
                    raise ValueError("恢复口令文件长度不正确")
                password = path.read_text(encoding="utf-8-sig").rstrip("\r\n")
            elif getattr(sys, "frozen", False) and not sys.stdin:
                raise ValueError("请双击 A2N.exe 使用恢复页面，或指定 --password-file")
            else:
                password = getpass.getpass("恢复口令（不回显）：")
            print(json.dumps(restore_offline(args.path, password, args.home, system_protector(),
                replace_identity=args.replace_identity, restore_network=args.restore_network), ensure_ascii=False))
            return 0
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
            return doctor(args.report)
        if args.command == "start":
            if args.background:
                return start_background(args.home, args.port, extra, open_browser=args.open)
            if (Path(args.home) / "network.json").exists() and not extra:
                from .desktop import main as desktop
                if args.open:
                    webbrowser.open(f"http://127.0.0.1:{args.port}/console")
                return desktop(["--home", args.home, "--port", str(args.port)])
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
