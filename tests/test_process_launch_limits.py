"""Exercise isolated native limits and preserve launcher/upgrade semantics."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from a2n_node import process_limits

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_other_platforms_never_load_windows_api(monkeypatch):
    monkeypatch.setattr(process_limits, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(process_limits.ctypes, 'WinDLL', lambda *a, **k: pytest.fail('Windows API loaded'), raising=False)
    assert process_limits.ensure_limit()['applied'] is False


def test_launcher_resolves_explicit_home_before_reading_installation(tmp_path, monkeypatch):
    launcher = load_script('serve_desktop_node')
    original = tmp_path / 'different-default'
    original.mkdir()
    (original/'installation.json').write_text('{invalid')
    requested = tmp_path / 'selected'
    monkeypatch.setattr(launcher, 'default_home', lambda: original)
    seen = []
    monkeypatch.setattr(launcher, 'source_main', lambda args: seen.extend(args) or 7)
    assert launcher.main(['--home=' + str(requested), '--port', '9991']) == 7
    assert seen == ['--home=' + str(requested), '--port', '9991']


def test_legacy_task_reuses_its_existing_home_and_preserves_explicit_override(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    wrapper = load_script('a2n_job_wrapper')
    (tmp_path/'runtime.db').touch()
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(wrapper, 'serve', lambda args: seen.append(args) or 0)
    assert wrapper.main([]) == 0
    assert seen[-1] == ['--home', str(tmp_path), '--watch-existing']
    wrapper.main(['--home=another-home'])
    assert seen[-1] == ['--home=another-home', '--watch-existing']


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows process trees')
def test_native_limit_is_enforced_without_large_host_allocation():
    result = subprocess.run([sys.executable, str(ROOT/'scripts/a2n_job_wrapper_selftest.py')],
        capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout)['passed']


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows process trees')
def test_native_group_limit_applies_to_aggregate_children():
    child = "import ctypes;\ntry: b=ctypes.create_string_buffer(64*1024**2)\nexcept MemoryError: print('bounded')\nelse: print('unbounded')"
    parent = "from a2n_node.process_limits import ensure_limit; import ctypes,subprocess,sys; ensure_limit(96*1024**2); held=ctypes.create_string_buffer(32*1024**2); print(subprocess.check_output([sys.executable,'-c'," + repr(child) + "],text=True).strip())"
    result = subprocess.run([sys.executable, '-c', parent], capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == 'bounded'


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows process trees')
def test_replacement_survives_launcher_exit(tmp_path):
    marker = tmp_path / 'replacement-live'
    child = "import time,pathlib;time.sleep(.5);pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
    parent = "from a2n_node.process_limits import ensure_limit;import subprocess,sys;ensure_limit(384*1024**2);subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    # The Windows venv redirector owns another kill-on-close job. Exercise this
    # product job with the real interpreter, not that unrelated launcher job.
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'packages/a2n-node/src')}
    subprocess.run([sys._base_executable, '-c', parent], check=True, timeout=10, env=env)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert marker.read_text() == 'alive'
