"""Verify the full install entry without accessing a user's existing node."""
import base64
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from urllib.parse import urlsplit

from a2n_node.product_cli import health, start_background
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector


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
    assert not (tmp_path / "caller.db").exists()


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
