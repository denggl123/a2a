import base64
import os
from pathlib import Path

import pytest

from a2n_node.backups import BackupService, opened_backup
from a2n_node.backups import restore_offline
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.storage import LocalStore

PASSWORD = "test-only-recovery-password"


def test_portable_snapshot_rewraps_identity_credentials_and_every_call(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(tmp_path / "node", port=0, protector=protector).start()
    try:
        node.store.put("accounts", "one", {"secret": "PRIVATE-BACKUP-VALUE"})
        node.store.claim("svc", "task", "business", {"payload": "PRIVATE-INPUT"})
        node.store.finish("svc", "task", {"ok": True, "state": "COMPLETED", "result": "PRIVATE-RESULT"})
        original_seed = node.store.get("identity", "seed")
        row = node.backups.create(PASSWORD)
        assert row["valid"] and row["portable"] and row["same_identity"] and row["calls"] == 1
        data = Path(row["path"]).read_bytes()
        assert all(v not in data for v in (b"PRIVATE-BACKUP-VALUE", b"PRIVATE-INPUT", b"PRIVATE-RESULT"))
        with opened_backup(row["path"], PASSWORD) as (exported, meta, network):
            assert exported.get("identity", "seed") == original_seed
            assert exported.get("accounts", "one")["secret"] == "PRIVATE-BACKUP-VALUE"
            assert exported.task("svc", "task")["outcome"]["result"] == "PRIVATE-RESULT"
            destination = tmp_path / "another-account.db"
            replacement = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
            exported.export_snapshot(destination, replacement)
        restored = LocalStore(destination, replacement)
        try:
            assert restored.get("identity", "seed") == original_seed
            assert restored.task("svc", "task")["request"]["payload"] == "PRIVATE-INPUT"
        finally:
            restored.close()
        assert node.store.task("svc", "task")["outcome"]["result"] == "PRIVATE-RESULT"
        assert original_seed == node.store.get("identity", "seed")
    finally:
        node.stop()


def test_wrong_password_and_whole_container_tampering_fail_closed(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(tmp_path / "node", port=0, protector=protector).start()
    try:
        row = node.backups.create(PASSWORD)
        with pytest.raises(ValueError):
            node.backups.validate(row["path"], "a-different-password")
        corrupted = bytearray(Path(row["path"]).read_bytes())
        corrupted[-50] ^= 1
        path = tmp_path / "corrupted.a2nbak"
        path.write_bytes(corrupted)
        with pytest.raises(ValueError):
            node.backups.validate(path, PASSWORD)
        with pytest.raises(ValueError, match="口令"):
            node.backups.create("short")
        assert len(list((node.home / "backups").glob("*.a2nbak"))) == 1
    finally:
        node.stop()


def test_restore_refuses_live_home_and_preserves_current_identity_until_explicitly_replaced(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    first = Daemon(tmp_path / "first", port=0, protector=protector).start()
    second = Daemon(tmp_path / "second", port=0, protector=protector).start()
    original = second.identity.did
    try:
        backup = first.backups.create(PASSWORD)
        with pytest.raises(RuntimeError, match="运行"):
            restore_offline(backup["path"], PASSWORD, second.home, protector, replace_identity=True)
        assert second.identity.did == original
    finally:
        second.stop()
        first.stop()
    with pytest.raises(ValueError, match="替换"):
        restore_offline(backup["path"], PASSWORD, second.home, protector)
    result = restore_offline(backup["path"], PASSWORD, second.home, protector, replace_identity=True)
    assert result["restored"] and not result["started"] and Path(result["rollback_path"]).is_file()
    restored = LocalStore(second.home / "runtime.db", protector)
    try:
        from a2n_p2p import Identity
        assert Identity.from_private_bytes(base64.b64decode(restored.get("identity", "seed"))).did == first.identity.did
    finally:
        restored.close()
