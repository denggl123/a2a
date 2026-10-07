"""Verify the full install entry without accessing a user's existing node."""
import base64
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from urllib.parse import urlsplit
import pytest

from a2n_node.product_cli import health, start_background
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector


def test_portable_home_is_shared_by_cli_and_desktop_launcher(tmp_path, monkeypatch):
    from a2n_node.product_cli import default_home
    from a2n_node.desktop import network_environment
    monkeypatch.delenv('A2N_HOME', raising=False)
    monkeypatch.chdir(tmp_path)
    home = tmp_path / 'shared-node'
    (tmp_path / '.a2n-local.json').write_text(json.dumps({'home': str(home)}))
    assert default_home() == home
    home.mkdir()
    (home / 'network.json').write_text(json.dumps({'host_address': '192.168.31.21', 'server_base': ''}))
    env = network_environment(default_home())
    assert env['A2N_HOME'] == str(home) and env['A2N_PUBLIC_BASE'] == 'http://192.168.31.21:18885'
    monkeypatch.setenv('A2N_HOME', str(tmp_path / 'explicit'))
    assert default_home() == tmp_path / 'explicit'


def test_doctor_runs_in_a_fresh_process_and_checks_the_full_product(tmp_path):
    env = {**os.environ, "A2N_STORAGE_KEY": base64.b64encode(os.urandom(32)).decode(),
           "A2N_DB": str(tmp_path / "caller.db")}
    run = subprocess.run([sys.executable, "-m", "a2n_node.product_cli", "doctor"],
                         capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["ok"] and result["packages"] == 5
    assert {"coordination", "coord_mailbox", "samples", "bilateral_feedback"} <= set(result["features"])
    assert result["settlement"] == "not_configured"
    assert result["backends"] == {"wasm_compile": True, "png_encode": True, "x402_signatures": True}
    assert not (tmp_path / "caller.db").exists()


@pytest.mark.parametrize("missing", ["wasmtime", "PIL", "eth_account"])
def test_doctor_rejects_an_install_without_a_required_execution_backend(missing):
    code = '''import importlib.abc,sys
class MissingBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == MISSING:
            raise ModuleNotFoundError('missing backend: '+MISSING)
sys.meta_path.insert(0, MissingBackend())
from a2n_node.product_cli import doctor
doctor()
'''.replace("MISSING", repr(missing))
    run = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", timeout=15)
    assert run.returncode != 0 and "missing backend: " + missing in run.stderr
    assert '"ok": true' not in run.stdout


def test_server_wrapper_command_accepts_post_and_json_stdin(tmp_path):
    key = base64.b64encode(os.urandom(32)).decode()
    node = Daemon(tmp_path / "node", port=0, protector=EnvironmentProtector(key)).start()
    try:
        run = subprocess.run([sys.executable, "-m", "a2n_node.product_cli", "request",
            "--home", str(node.home), "--port", str(urlsplit(node.runtime.local_base_url).port),
            "--path", "/v1/public-services", "--method", "POST", "--body", "-"],
            input=json.dumps({"services": {"witness": True}}), capture_output=True,
            text=True, encoding="utf-8", env={**os.environ, "A2N_STORAGE_KEY": key}, timeout=15)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout)["services"]["witness"] is True
        assert node.management.public_services["witness"] is True
        assert node.management.public_services["samples"] is True
    finally:
        node.stop()


def test_start_reuses_the_correct_home_and_refuses_another(tmp_path):
    import pytest
    with ExitStack() as stack:
        protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
        a = Daemon(tmp_path / "a", port=0, protector=protector).start()
        stack.callback(a.stop)
        port = urlsplit(a.runtime.local_base_url).port
        assert health(port, tmp_path / "a")
        assert not health(port, tmp_path / "other")
        assert start_background(tmp_path / "a", port, []) == 0
        with pytest.raises(RuntimeError, match="另一份节点目录"):
            start_background(tmp_path / "other", port, [])


def test_waiting_supervisor_hands_over_latest_installed_binary_and_rejects_changed_file(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from a2n_node.desktop import handover_installed_runtime
    binary = tmp_path / "next" / "A2N.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"new pinned release")
    (tmp_path / "installation.json").write_text(json.dumps({"runtime_bundled": True,
        "executable": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}))
    launched = []
    monkeypatch.setattr("a2n_node.desktop.subprocess.call", lambda args, **kwargs: launched.append(args) or 0)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "old" / "A2N.exe"))
    assert handover_installed_runtime(tmp_path, 8771) == 0
    assert launched[0][:3] == [str(binary.resolve()), "internal-run", "a2n_node.desktop"]
    monkeypatch.setattr(sys, "executable", str(binary))
    assert handover_installed_runtime(tmp_path, 8771) is None
    binary.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="INSTALLED_RUNTIME_CHANGED"):
        handover_installed_runtime(tmp_path, 8771)
