import base64
import json
import os

import pytest

from a2n_node.daemon import Daemon
from a2n_node.feedback_identity import signer_for
from a2n_node.protection import EnvironmentProtector
from a2n_node.upgrades import UpgradeService, file_digest
from a2n_p2p import Identity
from a2n_sdk.experience import signed
from a2n_sdk.storage import LocalStore


def manifest(publisher, path, sequence=1):
    return signed({"v": "a2n-release/1", "author_did": publisher.did, "release_sequence": sequence,
        "platform": "Windows", "artifact": "A2N.exe", "size": path.stat().st_size,
        "sha256": file_digest(path), "database_schema": 2}, signer_for(publisher))


def test_signed_upgrade_refuses_untrusted_publisher_tampering_and_sequence_rollback(tmp_path):
    store = LocalStore()
    publisher = Identity.generate()
    binary = tmp_path / "A2N.exe"
    binary.write_bytes(b"signed bytes")
    service = UpgradeService(tmp_path, store, app_root=tmp_path / "app")
    record = manifest(publisher, binary)
    with pytest.raises(ValueError, match="NOT_TRUSTED"):
        service.preview(record, binary)
    service.trust(publisher.did, True)
    assert service.preview(record, binary)["verified"]
    binary.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="FILE_CHANGED"):
        service.preview(record, binary)
    store.put("release_publishers", publisher.did, {"trusted": True, "installed_sequence": 10})
    with pytest.raises(ValueError, match="ROLLBACK_REFUSED"):
        service.preview(record, binary)
    service.trust(publisher.did, False)
    assert store.get("release_publishers", publisher.did)["installed_sequence"] == 10
    store.close()


def test_prepare_creates_verified_identity_backup_but_never_stops_node_or_executes_binary(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    daemon = Daemon(tmp_path / "node", port=0, protector=protector).start()
    try:
        binary = tmp_path / "next.exe"
        binary.write_bytes(b"not executable; preview never runs a file")
        previous = tmp_path / "previous.exe"
        previous.write_bytes(b"current")
        (daemon.home / "installation.json").write_text(json.dumps({"executable": str(previous), "port": 8771}), encoding="utf-8")
        service = UpgradeService(daemon.home, daemon.store, daemon.backups, app_root=tmp_path / "app")
        publisher = Identity.generate()
        service.trust(publisher.did, True)
        record = manifest(publisher, binary)
        prepared = service.prepare(record, binary, "independent recovery passphrase")
        assert prepared["state"] == "PREPARED" and not daemon.stop_requested.is_set()
        assert daemon.backups.validate(prepared["backup_path"], "independent recovery passphrase")["node_did"] == daemon.identity.did
        assert file_digest(prepared["destination"]) == record["sha256"]
        assert daemon.store.get("release_publishers", publisher.did).get("installed_sequence", 0) == 0
    finally:
        daemon.stop()


def test_source_process_can_register_and_launch_the_bundled_executable(tmp_path):
    from a2n_node.autostart import plan
    from a2n_node.product_cli import runtime_command
    binary = tmp_path / "A2N.exe"
    assert "internal-run" in plan(tmp_path / "home", 8771, binary)["arguments"]
    assert runtime_command("a2n_node.desktop", executable=str(binary)) == [str(binary), "internal-run", "a2n_node.desktop"]


def test_upgrade_cannot_claim_success_when_old_supervisor_restarts_source_or_another_release():
    from a2n_node.upgrades import verify_running_release
    expected = "a" * 64
    for product in ({}, {"mode": "SOURCE", "executable_sha256": expected},
                    {"mode": "BUNDLED", "executable_sha256": "b" * 64}):
        with pytest.raises(ValueError, match="PROGRAM_MISMATCH"):
            verify_running_release({"node_did": "unchanged", "product_runtime": product}, expected)
    verify_running_release({"product_runtime": {"mode": "BUNDLED", "executable_sha256": expected}}, expected)
