"""Frozen complete SDK entry and first-run Windows installer."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading

ALLOWED_INTERNAL = {"a2n_node", "a2n_node.desktop", "a2n_node.wasm_worker", "a2n_node.upgrades"}


def _log(home):
    Path(home).mkdir(parents=True, exist_ok=True)
    stream = (Path(home) / "node.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def install(home, port, autostart, notify):
    from a2n_node.product_cli import doctor, start_background
    from a2n_node.autostart import enable
    executable = Path(sys.executable).resolve()
    fingerprint = hashlib.sha256(executable.read_bytes()).hexdigest()
    root = Path(os.environ["LOCALAPPDATA"]) / "A2N" / "app" / fingerprint[:16]
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "A2N.exe"
    notify("安装完整运行时与五个产品包…")
    if destination != executable:
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != fingerprint:
                raise RuntimeError("安装目录已有不同内容的同名程序")
        else:
            shutil.copy2(executable, destination)
    notify("验证加密存储、公共发现和产品装配…")
    doctor()
    notify("保存唯一节点目录与启动设置…")
    destination = destination.resolve()
    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    (home / "installation.json").write_text(json.dumps({"executable": str(destination), "sha256": fingerprint,
        "home": str(home), "port": port, "runtime_bundled": True, "autostart": bool(autostart)}, indent=2), encoding="utf-8")
    if autostart:
        enable(home, port, executable=destination)
    notify("启动完整节点并打开控制台…")
    start_background(home, port, [], open_browser=True, executable=str(destination))
    return destination


def setup_window():
    """Self-contained loopback installer; no optional desktop widget runtime."""
    import secrets
    import time
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from a2n_node.product_cli import default_home
    token = secrets.token_urlsafe(32)
    state = {"running": False, "done": False, "error": "", "status": "请选择数据目录和启动方式", "finished_at": None}
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>A2N 完整节点安装</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;margin:8vh auto;max-width:640px;padding:24px;color:#19283c}input[type=text]{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 20px}button{padding:12px 22px;background:#254dbe;color:white;border:0;border-radius:6px}p{line-height:1.7}#status{white-space:pre-wrap}</style>
    <h1>安装完整 Agent 节点</h1><p>已包含运行时、五个产品包和控制台，无需安装开发环境。安装后这台电脑就是一个平台，并参与公共发现。</p>
    <label>节点数据目录（沿用已有身份和记录）<input id="home" type="text"></label>
    <label><input id="autostart" type="checkbox" checked>登录 Windows 后启动节点</label>
    <p><button id="install">安装并打开控制台</button></p><p id="status"></p>
    <hr><h2>从便携备份恢复</h2><p>先停止原节点。验证备份后才导入，不同时运行两份相同身份。</p>
    <label>备份文件绝对路径<input id="backup" type="text"></label><label>恢复口令<input id="password" type="password" autocomplete="off"></label>
    <p><label><input id="replace" type="checkbox">明确允许替换目标目录内不同的节点身份</label></p>
    <p><label><input id="network" type="checkbox">同时恢复原网络配置（新电脑地址可能需调整）</label></p><button id="restore">验证并恢复到上面的数据目录</button>
    <script>const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const headers={'Content-Type':'application/json','X-A2N-Setup':token};const s=document.getElementById('status');const b=document.getElementById('install');
    async function poll(){try{const r=await fetch('/status',{headers});const d=await r.json();if(!document.getElementById('home').value)document.getElementById('home').value=d.home;s.textContent=d.error||d.status;b.disabled=d.running||d.done;document.getElementById('restore').disabled=d.running||d.done;if(d.done){return;}setTimeout(poll,500);}catch(e){s.textContent='安装器已退出；可重新打开 A2N.exe。';}}
    b.onclick=async()=>{b.disabled=true;try{const r=await fetch('/install',{method:'POST',headers,body:JSON.stringify({home:document.getElementById('home').value,autostart:document.getElementById('autostart').checked})});const d=await r.json();if(!r.ok)throw Error(d.error); }catch(e){s.textContent=e.message;b.disabled=false;}};
    document.getElementById('restore').onclick=async()=>{const p=document.getElementById('password');try{const r=await fetch('/restore',{method:'POST',headers,body:JSON.stringify({home:document.getElementById('home').value,path:document.getElementById('backup').value,password:p.value,replace_identity:document.getElementById('replace').checked,restore_network:document.getElementById('network').checked})});const d=await r.json();if(!r.ok)throw Error(d.error);}catch(e){s.textContent=e.message;}finally{p.value='';}};poll();</script></html>"""
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def log_message(self, *_):
            pass
        def send(self, status, body, kind="application/json"):
            raw = (json.dumps(body, ensure_ascii=False) if kind == "application/json" else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(raw)
        def allowed(self):
            origin = "http://127.0.0.1:" + str(server.server_port)
            return self.headers.get("X-A2N-Setup") == token and self.headers.get("Origin", origin) == origin and self.headers.get("Host") == origin.split("//")[1]
        def do_GET(self):
            if self.path == "/":
                return self.send(200, page, "text/html")
            if self.path == "/status" and self.allowed():
                return self.send(200, {**state, "home": str(default_home())})
            self.send(403, {"error": "未授权"})
        def do_POST(self):
            if self.path not in {"/install", "/restore"} or not self.allowed():
                return self.send(403, {"error": "未授权"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("安装参数长度不正确")
                body = json.loads(self.rfile.read(length))
                home = str(Path(body["home"]).expanduser().resolve())
                autostart = body.get("autostart")
                restoring = self.path == "/restore"
                if (not restoring and not isinstance(autostart, bool)) or state["running"] or state["done"]:
                    raise ValueError("安装已进行或参数不正确")
                if restoring and (type(body.get("replace_identity")) is not bool or type(body.get("restore_network")) is not bool):
                    raise ValueError("恢复参数不正确")
                state.update(running=True, error="")
                def worker():
                    try:
                        if restoring:
                            from a2n_node.backups import restore_offline
                            from a2n_node.protection import system_protector
                            state.update(status="正在验证备份并恢复加密身份和账本…")
                            result = restore_offline(body["path"], body["password"], home, system_protector(),
                                replace_identity=body["replace_identity"], restore_network=body["restore_network"])
                            body.pop("password", None)
                            state.update(done=True, status="恢复完成：" + result["node_did"] + "。重新打开安装器以启动节点。", finished_at=time.monotonic())
                        else:
                            install(home, 8771, autostart, lambda value: state.update(status=value))
                            state.update(done=True, status="完整节点已启动，控制台已打开。可以关闭本页。", finished_at=time.monotonic())
                    except Exception as exc:
                        body.pop("password", None)
                        state.update(error=str(exc), running=False)
                threading.Thread(target=worker, daemon=True).start()
                self.send(202, {"accepted": True})
            except (ValueError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = .5
        webbrowser.open("http://127.0.0.1:" + str(server.server_port) + "/#" + token)
        until = time.monotonic() + 900
        while time.monotonic() < until:
            server.handle_request()
            if state["finished_at"] and time.monotonic() - state["finished_at"] > 20:
                break
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "internal-run":
        if len(args) < 2 or args[1] not in ALLOWED_INTERNAL:
            raise ValueError("不支持的内部运行入口")
        argv = args[2:]
        if args[1] == "a2n_node.wasm_worker":
            return importlib.import_module(args[1]).main(argv)
        if args[1] == "a2n_node.upgrades":
            return importlib.import_module(args[1]).main(argv)
        from a2n_node.product_cli import default_home
        home = argv[argv.index("--home") + 1] if "--home" in argv else default_home()
        _log(home)
        return importlib.import_module(args[1] + ".__main__" if args[1] == "a2n_node" else args[1]).main(argv)
    if args:
        from a2n_node.product_cli import main as cli
        return cli(args)
    return setup_window()


if __name__ == "__main__":
    raise SystemExit(main())
