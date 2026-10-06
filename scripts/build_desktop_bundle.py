"""Build a Windows executable containing the runtime, all five packages and UI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("a2n_kernel", "a2n_p2p", "a2n_acceptance", "a2n_sdk", "a2n_node")


def main():
    if os.name != "nt":
        raise SystemExit("Windows SDK bundle must be built on Windows")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], check=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
        "--name", "A2N", "--distpath", str(ROOT / "artifacts"),
        "--workpath", str(ROOT / ".tmp" / "desktop-build"), "--specpath", str(ROOT / ".tmp"),
        "--collect-data", "a2n_sdk"]
    args += ["--collect-all", "wasmtime"]
    args += ["--collect-submodules", "PIL"]
    for module in MODULES:
        args += ["--collect-submodules", module]
    args.append(str(ROOT / "scripts" / "desktop_launcher.py"))
    subprocess.run(args, check=True, cwd=ROOT)
    output = ROOT / "artifacts" / "A2N.exe"
    report = {"artifact": output.name, "bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "runtime_bundled": True,
        "platform": "Windows", "packages": list(MODULES), "publisher_signature": "NOT_CONFIGURED",
        "notice": "完整单文件安装试点；支持显式可信发布者的签名升级与图形恢复，本产物尚无发布者签名。"}
    (ROOT / "artifacts" / "A2N-desktop.manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
