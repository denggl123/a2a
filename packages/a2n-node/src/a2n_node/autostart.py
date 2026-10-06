"""One Windows task, fixed executable and canonical home, under the current user."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TASK = "A2N-Node"


def plan(home, port, executable=None):
    home = Path(home).expanduser().resolve()
    executable = Path(executable or sys.executable).resolve()
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid desktop port")
    bundled = getattr(sys, "frozen", False) or executable.name.lower() == "a2n.exe"
    args = (["internal-run", "a2n_node.desktop"] if bundled else ["-m", "a2n_node.desktop"])
    args += ["--home", str(home), "--port", str(port), "--watch-existing"]
    return {"task_name": TASK, "executable": str(executable), "arguments": subprocess.list2cmdline(args),
            "home": str(home), "working_directory": str(home), "port": port}


def enable(home, port=8771, *, dry_run=False, executable=None):
    spec = plan(home, port, executable)
    if dry_run:
        return {**spec, "applied": False}
    if os.name != "nt":
        raise RuntimeError("桌面自启动当前支持 Windows；Linux 服务器由完整 SDK 的 systemd 安装器管理")
    Path(spec["home"]).mkdir(parents=True, exist_ok=True)
    sid = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"], capture_output=True, text=True, check=True).stdout.strip()
    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(sid)}</UserId></LogonTrigger></Triggers>
<Principals><Principal id="Owner"><UserId>{escape(sid)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
<Actions Context="Owner"><Exec><Command>{escape(spec['executable'])}</Command><Arguments>{escape(spec['arguments'])}</Arguments><WorkingDirectory>{escape(spec['working_directory'])}</WorkingDirectory></Exec></Actions></Task>'''
    with tempfile.TemporaryDirectory(prefix="a2n-autostart-") as tmp:
        source = Path(tmp) / "task.xml"
        source.write_text(xml, encoding="utf-16")
        process = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(source), "/F"],
                                 capture_output=True, text=True)
        if process.returncode:
            # Existing elevated tasks can have an ACL the current desktop
            # session cannot change. HKCU is the user's own logon mechanism.
            import winreg
            command = subprocess.list2cmdline([spec["executable"]]) + " " + spec["arguments"]
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "A2N Node", 0, winreg.REG_SZ, command)
            return {**spec, "applied": True, "scope": "CURRENT_USER_RUN",
                    "notice": "旧计划任务权限不足；已使用当前用户登录入口。"}
    # A task registration supersedes our previous fallback, when permissions
    # permit it. Do not alter any other startup entry.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "A2N Node")
    except FileNotFoundError:
        pass
    return {**spec, "applied": True, "scope": "CURRENT_USER_LOGON"}
