"""Portable password protected backups, including identity and rewrapped payloads."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import tempfile
import time
import zipfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from a2n_p2p import Identity
from a2n_sdk.storage import LocalStore
from a2n_sdk.projection import canonical_json

from .protection import EnvironmentProtector

MAGIC = b"A2NBAK1\x00"
ITERATIONS = 600000
MAX_ARCHIVE = 1024 * 1024 * 1024
CHUNK = 262144


def _keys(password, salt):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("恢复口令须为 12–1024 个字符")
    root = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())
    container = hmac.new(root, b"a2n-backup-container/1", hashlib.sha256).digest()
    database = hmac.new(root, b"a2n-backup-database/1", hashlib.sha256).digest()
    return container, EnvironmentProtector(base64.b64encode(database).decode())


def _encrypt(source, output, password):
    if Path(source).stat().st_size > MAX_ARCHIVE - 4096:
        raise ValueError("备份体积超过当前导出上限")
    salt, nonce = os.urandom(16), os.urandom(12)
    key, _ = _keys(password, salt)
    header = canonical_json({"v": 1, "kdf": "PBKDF2-SHA256", "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode()}).encode()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC + header)
    with Path(source).open("rb") as reader, Path(output).open("xb") as writer:
        writer.write(MAGIC + struct.pack(">I", len(header)) + header)
        while chunk := reader.read(CHUNK):
            writer.write(encryptor.update(chunk))
        writer.write(encryptor.finalize() + encryptor.tag)


@contextmanager
def opened_backup(path, password):
    path = Path(path)
    if not path.is_file() or not 32 <= path.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("备份文件不存在或体积不合范围")
    with tempfile.TemporaryDirectory(prefix="a2n-backup-read-") as tmp, path.open("rb") as reader:
        if reader.read(len(MAGIC)) != MAGIC:
            raise ValueError("不支持的备份格式")
        raw_size = reader.read(4)
        if len(raw_size) != 4:
            raise ValueError("备份文件损坏")
        size = struct.unpack(">I", raw_size)[0]
        if not 1 <= size <= 4096:
            raise ValueError("备份头部损坏")
        header = reader.read(size)
        spec = json.loads(header)
        if set(spec) != {"v", "kdf", "iterations", "salt", "nonce"} or spec["v"] != 1 or spec["kdf"] != "PBKDF2-SHA256" or spec["iterations"] != ITERATIONS:
            raise ValueError("不支持的备份加密参数")
        salt, nonce = base64.b64decode(spec["salt"], validate=True), base64.b64decode(spec["nonce"], validate=True)
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError("备份加密参数损坏")
        key, protector = _keys(password, salt)
        start = reader.tell()
        reader.seek(-16, 2)
        tag = reader.read(16)
        remaining = reader.tell() - 16 - start
        if remaining <= 0:
            raise ValueError("备份文件损坏")
        reader.seek(start)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC + header)
        decrypted = Path(tmp) / "payload.zip"
        try:
            with decrypted.open("xb") as writer:
                while remaining:
                    chunk = reader.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError("备份文件不完整")
                    remaining -= len(chunk)
                    writer.write(decryptor.update(chunk))
                writer.write(decryptor.finalize())
        except Exception as exc:
            raise ValueError("恢复口令不正确或备份已损坏") from exc
        with zipfile.ZipFile(decrypted) as archive:
            names = archive.namelist()
            allowed = {"runtime.db", "metadata.json", "network.json"}
            if set(names) - allowed or len(names) != len(set(names)) or not {"runtime.db", "metadata.json"} <= set(names):
                raise ValueError("备份内容不符合节点格式")
            if any(i.file_size > MAX_ARCHIVE // 2 or i.compress_type != zipfile.ZIP_STORED for i in archive.infolist()):
                raise ValueError("备份内容体积或编码异常")
            if archive.getinfo("metadata.json").file_size > 4096:
                raise ValueError("备份说明体积异常")
            metadata = json.loads(archive.read("metadata.json"))
            database_salt = base64.b64decode(metadata.get("database_salt", ""), validate=True)
            if len(database_salt) != 16:
                raise ValueError("备份数据库密钥参数损坏")
            _, protector = _keys(password, database_salt)
            db = Path(tmp) / "runtime.db"
            with archive.open("runtime.db") as source, db.open("xb") as target:
                while chunk := source.read(CHUNK):
                    target.write(chunk)
            network = archive.read("network.json") if "network.json" in names else None
            if network is not None and len(network) > 16384:
                raise ValueError("网络配置体积异常")
        store = LocalStore(db, protector)
        try:
            seed = store.get("identity", "seed")
            did = Identity.from_private_bytes(base64.b64decode(seed)).did
            if (metadata.get("v") != 1 or metadata.get("node_did") != did
                    or store._db.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
                raise ValueError("备份身份或数据库校验失败")
            # Check every encrypted payload; a key that unlocks one row is insufficient.
            for row in store._db.execute("SELECT value FROM settings"):
                store._decode(row[0])
            for row in store._db.execute("SELECT request,outcome FROM calls"):
                for sealed in row:
                    if sealed:
                        store._decode(sealed)
            yield store, metadata, network
        finally:
            store.close()


class BackupService:
    def __init__(self, home, store, identity):
        self.home, self.store, self.identity = Path(home), store, identity

    def create(self, password):
        # Derive first: invalid passwords must not create even temporary snapshots.
        _keys(password, os.urandom(16))
        backup_id = "bk_" + secrets.token_hex(16)
        directory = self.home / "backups"
        directory.mkdir(exist_ok=True)
        output = directory / (backup_id + ".a2nbak")
        # The container salt is independent; export key is derived from a private
        # salt stored inside the authenticated archive metadata.
        database_salt = os.urandom(16)
        _, export_protector = _keys(password, database_salt)
        metadata = {"v": 1, "node_did": self.identity.did, "created_at": time.time(),
            "scope": ["database", "identity", "node_configuration"],
            "database_salt": base64.b64encode(database_salt).decode(), "portable": True}
        try:
            with tempfile.TemporaryDirectory(prefix="a2n-backup-create-") as tmp:
                db = Path(tmp) / "runtime.db"
                self.store.export_snapshot(db, export_protector)
                container = Path(tmp) / "payload.zip"
                with zipfile.ZipFile(container, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.write(db, "runtime.db")
                    archive.writestr("metadata.json", canonical_json(metadata))
                    network = self.home / "network.json"
                    if network.is_file():
                        archive.writestr("network.json", network.read_bytes())
                _encrypt(container, output, password)
            # Validate password, whole-file authentication, DID and every stored payload.
            result = self.validate(output, password)
            fingerprint = hashlib.sha256()
            with output.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    fingerprint.update(chunk)
            row = {"backup_id": backup_id, "path": str(output), "bytes": output.stat().st_size, "created_at": metadata['created_at'],
                   "sha256": fingerprint.hexdigest(), **result}
            self.store.put("backups", backup_id, row)
            return row
        except BaseException:
            output.unlink(missing_ok=True)
            raise

    def validate(self, path, password):
        with opened_backup(path, password) as (store, metadata, _):
            return {"valid": True, "portable": True, "node_did": metadata["node_did"],
                "same_identity": metadata["node_did"] == self.identity.did,
                "calls": store._db.execute("SELECT count(*) FROM calls").fetchone()[0],
                "requires_stopped_node": True, "scope": metadata["scope"],
                "notice": "含节点身份；恢复前须停止同一 DID 的原节点，避免并行分叉。"}


def restore_offline(path, password, home, protector, *, replace_identity=False, restore_network=False):
    """Explicit offline action; an active home lock prevents replacing a live node."""
    from .home import acquire_home_lock
    home = Path(home).expanduser().resolve()
    lock = acquire_home_lock(home)
    try:
        with opened_backup(path, password) as (exported, metadata, network):
            destination = home / "runtime.db"
            rollback = None
            if destination.exists():
                current = LocalStore(destination, protector)
                try:
                    seed = current.get("identity", "seed")
                    current_did = Identity.from_private_bytes(base64.b64decode(seed)).did
                    if current_did != metadata["node_did"] and not replace_identity:
                        raise ValueError("恢复会替换本机身份；需明确指定 --replace-identity")
                    directory = home / "backups"
                    directory.mkdir(exist_ok=True)
                    rollback = directory / ("before-restore-" + secrets.token_hex(8) + ".db")
                    current.export_snapshot(rollback, protector)
                    current._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    current.close()
            with tempfile.TemporaryDirectory(prefix="a2n-restore-", dir=home) as tmp:
                replacement = Path(tmp) / "runtime.db"
                exported.export_snapshot(replacement, protector)
                new_network = Path(tmp) / "network.json"
                if restore_network and network is not None:
                    json.loads(network)
                    new_network.write_bytes(network)
                for suffix in ("-wal", "-shm"):
                    stale = home / ("runtime.db" + suffix)
                    if stale.exists():
                        raise RuntimeError("停机后仍有数据库日志文件；保留原目录，请先检查其他实例")
                previous_network = (home / "network.json").read_bytes() if (home / "network.json").exists() else None
                try:
                    os.replace(replacement, destination)
                    if new_network.exists():
                        os.replace(new_network, home / "network.json")
                except BaseException:
                    if rollback:
                        import shutil
                        shutil.copy2(rollback, destination)
                    if previous_network is not None:
                        (home / "network.json").write_bytes(previous_network)
                    raise
            return {"restored": True, "node_did": metadata["node_did"], "home": str(home),
                    "rollback_path": str(rollback) if rollback else None, "started": False,
                    "network_restored": bool(restore_network and network is not None)}
    finally:
        lock.close()
