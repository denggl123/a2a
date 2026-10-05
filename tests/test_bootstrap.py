"""一键装（`scripts/bootstrap.py`）的契约。

它不是"跑一遍能装上"就算数 —— 装机脚本最贵的错是**假成功**：
装完了却起不来，用户以为坏了就卸载。所以这里守四件事：

1. **一条路走到自检** —— 计划里必须包含 requirements 与全部本地包，
   并且最后真的 verify 一次 import。
2. **干跑与真跑同一份计划** —— 不许两条路漂移（干跑看到的 ≠ 真跑做的）。
3. **Windows 双击入口必须是纯 ASCII** —— cmd.exe 按系统代码页解析，
   中文注释会被当成命令执行（踩过一次）。
4. **坏环境要响亮失败** —— 没有 venv 时 `--check` 不许假装通过。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.py"


def _load():
    spec = importlib.util.spec_from_file_location("a2n_bootstrap", BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_plan_covers_requirements_and_every_package(tmp_path):
    mod = _load()
    steps = mod.plan(ROOT, tmp_path / "venv", sys.executable)
    titles = [t for t, _ in steps]
    cmds = [c for _, c in steps]
    assert any("requirements.txt" in " ".join(c) for c in cmds)
    pkgs = mod.package_dirs(ROOT)
    assert len(pkgs) == 5, "本地包数量异常"
    for d in pkgs:
        assert any(str(d) in " ".join(c) and "--no-deps" in c for c in cmds), d.name
    assert any("虚拟环境" in t for t in titles), "缺 venv 时应先建 venv"


def test_plan_skips_venv_creation_when_it_exists(tmp_path):
    mod = _load()
    venv = tmp_path / "venv"
    venv.mkdir()
    mod.venv_python(venv).parent.mkdir(parents=True, exist_ok=True)
    mod.venv_python(venv).write_bytes(b"")
    titles = [t for t, _ in mod.plan(ROOT, venv, sys.executable)]
    assert not any("虚拟环境" in t for t in titles)


def test_dry_run_changes_nothing_and_returns_zero(capsys):
    mod = _load()
    assert mod.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "干跑" in out and "未改动任何东西" in out
    assert "--no-deps" in out


def test_check_fails_loudly_without_a_venv(tmp_path, capsys):
    mod = _load()
    with pytest.raises(SystemExit) as exc:
        mod.main(["--check", "--venv", str(tmp_path / "nope")])
    assert "虚拟环境" in str(exc.value)


def test_venv_paths_follow_the_platform(tmp_path):
    mod = _load()
    if sys.platform == "win32":
        assert mod.venv_python(tmp_path).name == "python.exe"
        assert mod.venv_python(tmp_path).parent.name == "Scripts"
        assert mod.venv_script(tmp_path, "a2n-node").suffix == ".exe"
    else:
        assert mod.venv_python(tmp_path).parent.name == "bin"
        assert mod.venv_script(tmp_path, "a2n-node").name == "a2n-node"


def test_next_step_names_the_console():
    mod = _load()
    text = mod.render_next_step(ROOT / ".venv")
    assert "a2n-node" in text and "8771" in text


def test_windows_launcher_is_ascii_only():
    cmd = (ROOT / "scripts" / "bootstrap.cmd").read_bytes()
    assert all(b < 128 for b in cmd), "bootstrap.cmd 必须纯 ASCII（cmd.exe 按 GBK 解析）"


def test_wrappers_point_at_the_python_entrypoint():
    sh = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    cmd = (ROOT / "scripts" / "bootstrap.cmd").read_bytes().decode("ascii")
    assert "scripts/bootstrap.py" in sh
    assert "scripts\\bootstrap.py" in cmd


def test_bootstrap_module_compiles():
    subprocess.run([sys.executable, "-m", "py_compile", str(BOOTSTRAP)], check=True)
