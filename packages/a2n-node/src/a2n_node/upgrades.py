"""Owner-pinned signed Windows upgrades, immutable binaries and binary rollback."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from a2n_sdk.experience import unsigned

from .feedback_identity import verifier_for
from .product_cli import health, runtime_command, start_background
from .home import acquire_upgrade_lock, HomeInUseError

FIELDS = {"v", "author_did", "release_sequence", "platform", "artifact", "size", "sha256", "database_schema", "proof"}

def release_sequence(value):
    # Decimal strings retain signatures through browser JSON parsers above 2^53.
    if type(value) is str:
        if not re.fullmatch(r'0|[1-9][0-9]{0,38}',value):return -1
        value=int(value)
    return value if type(value) is int and 0<=value<2**128 else -1


def verify_running_release(runtime, expected_sha256):
    product = runtime.get("product_runtime") or {}
    if product.get("mode") != "BUNDLED" or product.get("executable_sha256") != expected_sha256:
        raise ValueError("UPGRADE_PROGRAM_MISMATCH")


def file_digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(262144):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_installation(home, installation):
    """A killed updater must leave either complete installation record."""
    path = Path(home) / "installation.json"
    temporary = path.with_suffix(".json.pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(installation, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class UpgradeService:
    def __init__(self, home, store, backups=None, *, app_root=None):
        self.home, self.store, self.backups = Path(home).resolve(), store, backups
        self.app_root = Path(app_root or Path(os.environ.get("LOCALAPPDATA") or self.home) / "A2N" / "app").resolve()

    def recover_interrupted(self, product):
        """Reconcile a completed launch or expose a safe, explicit retry.

        An active updater owns a different lock from the node. Startup never
        races its doctor, replacement or final identity checks.
        """
        try:
            lock = acquire_upgrade_lock(self.home)
        except HomeInUseError:
            return []
        recovered = []
        with lock:
            for key, row in self.store.items("release_pending").items():
                if row["state"] not in {"QUEUED", "APPLYING"}:
                    continue
                manifest = row["manifest"]
                state, reason = "RECOVERY_REQUIRED", "升级中断；当前程序版本无法核验，请使用原备份恢复或重新检查安装程序"
                try:
                    if not verifier_for()(manifest.get("proof"), unsigned(manifest)):
                        raise ValueError("UNVERIFIED_RELEASE")
                    destination = Path(row["destination"])
                    if not destination.is_file() or file_digest(destination) != manifest["sha256"]:
                        raise ValueError("RELEASE_FILE_CHANGED")
                    if row["state"] == "QUEUED":
                        state, reason = "PREPARED", "升级在切换程序前中断，原节点继续运行；可以明确重试这份已准备的升级"
                    elif product.get("mode") == "BUNDLED" and product.get("executable_sha256") == manifest["sha256"]:
                        installation = json.loads((self.home / "installation.json").read_text("utf-8-sig"))
                        if Path(installation["executable"]).resolve() != destination.resolve() or installation.get("sha256") != manifest["sha256"]:
                            raise ValueError("UPGRADE_INSTALLATION_MISMATCH")
                        state, reason = "INSTALLED", "重启后已核验原节点及新程序，补齐原升级的完成记录"
                        publisher = self.store.get("release_publishers", manifest["author_did"]) or {}
                        self.store.put("release_publishers", manifest["author_did"], {**publisher,
                            "installed_sequence": str(max(release_sequence(publisher.get("installed_sequence",0)),release_sequence(manifest['release_sequence']))) if type(manifest['release_sequence']) is str else max(release_sequence(publisher.get("installed_sequence",0)),release_sequence(manifest['release_sequence']))})
                    else:
                        previous = row["previous_installation"]
                        if (product.get("mode") == "BUNDLED" and previous.get("sha256")
                                and product.get("executable_sha256") == previous["sha256"]
                                and Path(previous["executable"]).is_file()
                                and file_digest(previous["executable"]) == previous["sha256"]):
                            write_installation(self.home, previous)
                            if previous.get("autostart") and os.name == "nt":
                                from .autostart import enable
                                enable(self.home, previous["port"], executable=previous["executable"])
                            state, reason = "ROLLED_BACK_BINARY", "升级中断后原程序已恢复运行，安装入口已回到原程序；业务数据保留"
                except (ValueError, OSError, KeyError, TypeError):
                    reason = "升级中断或程序文件变化，未启动无法核验的文件；请检查程序或使用原备份恢复"
                with self.store.tx():
                    current = self.store.get("release_pending", key)
                    if current and current["state"] == row["state"]:
                        self.store.put("release_pending", key, {**current, "state": state, "reason": reason,
                            "recovered_at": time.time(), **({"finished_at": time.time()} if state == "INSTALLED" else {})})
                        recovered.append(key)
        return recovered

    def trust(self, author_did, trusted):
        if not isinstance(author_did, str) or not re.fullmatch(r"did:a2n:ag_[0-9a-f]{24}", author_did) or type(trusted) is not bool:
            raise ValueError("INVALID_RELEASE_PUBLISHER")
        with self.store.tx():
            previous = self.store.get("release_publishers", author_did) or {}
            row = {**previous, "author_did": author_did, "trusted": trusted, "updated_at": time.time()}
            self.store.put("release_publishers", author_did, row)
            return row

    def preview(self, manifest, path):
        if (not isinstance(manifest, dict) or set(manifest) != FIELDS or manifest["v"] != "a2n-release/1"
            or manifest["platform"] != "Windows" or manifest["artifact"] != "A2N.exe"
            or release_sequence(manifest["release_sequence"]) < 1
            or type(manifest["size"]) is not int or not 1 <= manifest["size"] <= 128 * 1024 * 1024
            or not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
            or manifest["database_schema"] != 2 or not verifier_for()(manifest.get("proof"), unsigned(manifest))):
            raise ValueError("UNVERIFIED_RELEASE")
        publisher = self.store.get("release_publishers", manifest["author_did"]) or {}
        if not publisher.get("trusted"):
            raise ValueError("RELEASE_PUBLISHER_NOT_TRUSTED")
        if release_sequence(manifest["release_sequence"]) <= release_sequence(publisher.get("installed_sequence", 0)):
            raise ValueError("RELEASE_ROLLBACK_REFUSED")
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size != manifest["size"] or file_digest(path) != manifest["sha256"]:
            raise ValueError("RELEASE_FILE_CHANGED")
        return {"manifest": manifest, "path": str(path), "home": str(self.home), "verified": True}

    def prepare(self, manifest, path, password):
        preview = self.preview(manifest, path)
        if not self.backups:
            raise ValueError("BACKUP_SERVICE_REQUIRED")
        install_file = self.home / "installation.json"
        installation = json.loads(install_file.read_text(encoding="utf-8")) if install_file.exists() else None
        if not installation or not Path(installation.get("executable", "")).is_file():
            raise ValueError("BUNDLED_INSTALLATION_REQUIRED")
        backup = self.backups.create(password)
        root = (self.app_root / manifest["sha256"][:16]).resolve()
        if self.app_root not in root.parents:
            raise ValueError("INVALID_INSTALLATION_TARGET")
        root.mkdir(parents=True, exist_ok=True)
        destination = root / "A2N.exe"
        if destination.exists():
            if file_digest(destination) != manifest["sha256"]:
                raise ValueError("RELEASE_DESTINATION_CONFLICT")
        else:
            shutil.copy2(preview["path"], destination)
        if file_digest(destination) != manifest["sha256"]:
            raise ValueError("RELEASE_COPY_CHANGED")
        key = "up_" + manifest["sha256"][:32]
        with self.store.tx():
            pending = self.store.get("release_pending", key)
            if pending and pending["state"] in {"APPLYING", "INSTALLED"}:
                return pending
            row = {"id": key, "state": "PREPARED", "manifest": manifest, "destination": str(destination),
                "previous_installation": installation, "backup_path": backup["path"], "created_at": time.time()}
            self.store.put("release_pending", key, row)
            return row

    def launch(self, pending_id):
        if os.name != "nt":
            raise ValueError("WINDOWS_UPGRADE_ONLY")
        with self.store.tx():
            row = self.store.get("release_pending", pending_id)
            if not row or row["state"] not in {"PREPARED", "FAILED", "ROLLED_BACK_BINARY"}:
                raise ValueError("PREPARED_UPGRADE_REQUIRED")
            self.preview(row["manifest"], row["destination"])
            self.store.put("release_pending", pending_id, {**row, "state": "QUEUED"})
        try:
            subprocess.Popen(runtime_command("a2n_node.upgrades", ["--home", str(self.home), "--pending", pending_id]),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}, creationflags=subprocess.CREATE_NO_WINDOW)
        except BaseException:
            self.store.put("release_pending", pending_id, {**row, "state": "FAILED"})
            raise
        return {"queued": True, "id": pending_id, "home": str(self.home)}


def apply_offline(home, pending_id):
    with acquire_upgrade_lock(home):
        return _apply_offline(home, pending_id)


def _apply_offline(home, pending_id):
    from a2n_sdk.client import NodeClient
    from a2n_sdk.storage import LocalStore
    from a2n_p2p import Identity
    import base64
    from .autostart import enable
    from .protection import system_protector
    from .home import wait_for_home_lock
    home = Path(home).resolve()
    store = LocalStore(home / "runtime.db", system_protector())
    stopped, installation = False, None
    port = 8771
    try:
        service = UpgradeService(home, store)
        row = store.get("release_pending", pending_id)
        if not row or row["state"] != "QUEUED":
            raise ValueError("QUEUED_UPGRADE_REQUIRED")
        service.preview(row["manifest"], row["destination"])
        identity = Identity.from_private_bytes(base64.b64decode(store.get("identity", "seed")))
        installation = row["previous_installation"]
        port = installation["port"]
        # A signed file still has to start its bundled runtime before touching a
        # working installation. Doctor operates on a separate temporary home.
        with tempfile.TemporaryDirectory(prefix="a2n-upgrade-check-") as tmp:
            report = Path(tmp) / "doctor.json"
            subprocess.run([row["destination"], "doctor", "--report", str(report)], timeout=60, check=True,
                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}, creationflags=subprocess.CREATE_NO_WINDOW)
            if not json.loads(report.read_text(encoding="utf-8")).get("ok"):
                raise ValueError("NEW_RELEASE_DOCTOR_FAILED")
        store.put("release_pending", pending_id, {**row, "state": "APPLYING"})
        if health(port, home):
            access = store.get("runtime_control", "access")
            NodeClient(f"http://127.0.0.1:{port}", token=access["token"]).request("POST", "/v1/node/stop", {})
            until = time.monotonic() + 15
            while health(port, home) and time.monotonic() < until:
                time.sleep(.2)
            if health(port, home):
                raise ValueError("NODE_DID_NOT_STOP")
        with wait_for_home_lock(home):
            stopped = True
            new_installation = {**installation, "executable": row["destination"], "sha256": row["manifest"]["sha256"]}
            write_installation(home, new_installation)
            if installation.get("autostart"):
                enable(home, port, executable=row["destination"])
        start_background(home, port, [], executable=row["destination"])
        access = store.get("runtime_control", "access")
        runtime = NodeClient(f"http://127.0.0.1:{port}", token=access["token"]).request("GET", "/v1/runtime")
        if runtime.get("node_did") != identity.did:
            raise ValueError("UPGRADE_IDENTITY_CHANGED")
        verify_running_release(runtime, row["manifest"]["sha256"])
        with store.tx():
            publisher = store.get("release_publishers", row["manifest"]["author_did"])
            store.put("release_publishers", row["manifest"]["author_did"], {**publisher, "installed_sequence": row["manifest"]["release_sequence"]})
            store.put("release_pending", pending_id, {**row, "state": "INSTALLED", "finished_at": time.time()})
    except Exception as exc:
        state = "FAILED"
        if stopped and installation:
            try:
                if health(port, home):
                    access = store.get("runtime_control", "access")
                    NodeClient(f"http://127.0.0.1:{port}", token=access["token"]).request("POST", "/v1/node/stop", {})
                    until = time.monotonic() + 15
                    while health(port, home) and time.monotonic() < until:
                        time.sleep(.2)
                with wait_for_home_lock(home):
                    write_installation(home, installation)
                    if installation.get("autostart"):
                        enable(home, port, executable=installation["executable"])
                start_background(home, port, [], executable=installation["executable"])
                state = "ROLLED_BACK_BINARY"
            except Exception:
                state = "RECOVERY_REQUIRED"
        record = store.get("release_pending", pending_id)
        if record:
            store.put("release_pending", pending_id, {**record, "state": state, "reason": str(exc)[:300]})
        raise
    finally:
        store.close()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--pending", required=True)
    args = parser.parse_args(argv)
    apply_offline(args.home, args.pending)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
