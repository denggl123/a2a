#!/usr/bin/env python
"""一键装：把这台电脑变成 A2N 节点。

设计目标（`docs/PRODUCT.md` 时刻 1）：**双击，然后什么都不做，目标 < 1 分钟。**
所以这里只有一条路，没有需要用户回答的问题：

    python scripts/bootstrap.py

它做的事（**幂等**，重复跑只是重新对一遍，不会把已有环境弄乱）：
  1. 确认 Python ≥ 3.11；
  2. 没有 `.venv` 就建一个（`venv` + pip）；
  3. 装 `requirements.txt` 里的第三方依赖；
  4. 把 `packages/*` 全部**可编辑安装**（`--no-deps`：a2n-* 还没发到 PyPI，
     第三方依赖上一步已装齐）；
  5. **自检**：真的 import 一遍 `a2n_node` / `a2n_sdk`，
     失败就大声退出 —— "装完了但起不来"是最伤人的一种假成功；
  6. 打印唯一一句下一步。

除 `--dry-run` / `--check` 外，任何一步失败都立刻非零退出并说明卡在哪里。
旁路：Windows 双击 `scripts\\bootstrap.cmd`；POSIX `bash scripts/bootstrap.sh`。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 11)
# 自检要 import 的核心包：少一个，README 的第一条命令就会失败。
CHECK_IMPORTS = ("a2n_node", "a2n_sdk", "a2n_p2p", "a2n_acceptance", "a2n_kernel")
RETIRED_PACKAGES = tuple("a2n-" + name for name in (
    "store", "ledger", "custodian", "registry", "transport", "reputation", "account",
    "dispatch", "settlement", "wallet", "task", "deal", "notary", "market", "consensus",
    "ap2", "gateway", "server"))


def venv_python(venv_dir: Path) -> Path:
    """venv 里的解释器路径（Windows 与 POSIX 不同）。"""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_script(venv_dir: Path, name: str) -> Path:
    """venv 里的可执行入口（console script）路径。"""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def package_dirs(root: Path = ROOT) -> list[Path]:
    return sorted(p for p in (root / "packages").glob("*") if (p / "pyproject.toml").is_file())


def render_next_step(venv_dir: Path) -> str:
    script = venv_script(venv_dir, "a2n-sdk")
    try:
        rel = script.relative_to(venv_dir.parent)
    except ValueError:
        rel = script
    return f"{rel} start --background --open → http://127.0.0.1:8771/console（兼容：a2n-node serve）"


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("  $ " + " ".join(str(c) for c in cmd))
    if dry_run:
        return
    proc = subprocess.run([str(c) for c in cmd], stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        detail = f"\n    {lines[0]}" if lines else ""
        raise SystemExit(f"✗ 这一步失败了（exit {proc.returncode}）："
                         f"{' '.join(map(str, cmd))}{detail}")


def plan(root: Path, venv_dir: Path, py: str) -> list[tuple[str, list[str]]]:
    """把"要做的事"列出来 —— 干跑与真跑共用同一份，杜绝两条路漂移。"""
    vpy = venv_python(venv_dir)
    steps: list[tuple[str, list[str]]] = []
    if not vpy.exists():
        steps.append((f"建虚拟环境 {venv_dir}", [py, "-m", "venv", str(venv_dir)]))
    steps.append(("升级 pip", [str(vpy), "-m", "pip", "install", "--upgrade", "pip", "-q"]))
    steps.append(("移除退役包的安装登记（保留节点数据）",
                  [str(vpy), "-m", "pip", "uninstall", "-y", *RETIRED_PACKAGES]))
    steps.append((f"装第三方依赖 {REQUIREMENTS.name}",
                  [str(vpy), "-m", "pip", "install", "-r", str(REQUIREMENTS), "-q"]))
    for d in package_dirs(root):
        steps.append((f"可编辑安装 {d.name}",
                      [str(vpy), "-m", "pip", "install", "-e", str(d), "--no-deps", "-q"]))
    return steps


def verify(venv_dir: Path, *, dry_run: bool) -> None:
    vpy = venv_python(venv_dir)
    code = "import " + ", ".join(CHECK_IMPORTS) + "; print('import-ok')"
    print("  · 自检：import " + " / ".join(CHECK_IMPORTS))
    if dry_run:
        return
    if not vpy.exists():
        raise SystemExit(f"✗ 没有虚拟环境 {venv_dir}；先跑一次 python scripts/bootstrap.py")
    proc = subprocess.run([str(vpy), "-c", code], capture_output=True)
    if proc.returncode != 0 or b"import-ok" not in proc.stdout:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise SystemExit("✗ 装完了却 import 不进来，环境是坏的：\n    "
                         + "\n    ".join(tail))
    print("  · 完整产品自检：5 个包、存储保护、节点服务与协调身份")
    _run([str(vpy), "-m", "a2n_node.product_cli", "doctor"], dry_run=False)


def preflight(py: str, *, dry_run: bool) -> None:
    if sys.version_info[:2] < MIN_PYTHON:
        raise SystemExit(f"✗ 需要 Python ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}，"
                         f"当前是 {sys.version.split()[0]}")
    if not REQUIREMENTS.is_file():
        raise SystemExit(f"✗ 找不到 {REQUIREMENTS}；请在仓库根目录运行本脚本")
    print(f"· Python {sys.version.split()[0]}  ({py})")
    print(f"· 仓库根：{ROOT}")
    if not dry_run:
        print(f"· 虚拟环境：{DEFAULT_VENV}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="bootstrap", description="一键装：建 .venv、装依赖、装 5 个本地包、自检。")
    parser.add_argument("--venv", default=str(DEFAULT_VENV), help="虚拟环境目录（默认 .venv）")
    parser.add_argument("--dry-run", action="store_true", help="只打印要做的事，不执行")
    parser.add_argument("--check", action="store_true", help="只跑 import 自检，不安装")
    parser.add_argument("--launch", action="store_true", help="装好后后台启动完整 SDK 并打开控制台")
    args = parser.parse_args(argv)

    venv_dir = Path(args.venv).resolve()
    py = sys.executable
    dry_run = bool(args.dry_run)
    print("A2N 一键装" + ("（干跑）" if dry_run else ""))
    preflight(py, dry_run=dry_run)

    if args.check:
        verify(venv_dir, dry_run=False)
        print("✓ 环境自检通过。下一步：" + render_next_step(venv_dir))
        return 0

    steps = plan(ROOT, venv_dir, py)
    for title, cmd in steps:
        print(f"· {title}")
        _run(cmd, dry_run=dry_run)
    verify(venv_dir, dry_run=dry_run)

    if dry_run:
        print("\n（干跑结束，未改动任何东西）")
        return 0
    print("\n✓ 安装完成，这台电脑已是一个 A2N 节点。")
    print("  下一步：" + render_next_step(venv_dir))
    if args.launch:
        _run([str(venv_python(venv_dir)), "-m", "a2n_node.product_cli", "start", "--background", "--open"], dry_run=False)
        print("  控制台：http://127.0.0.1:8771/console")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
