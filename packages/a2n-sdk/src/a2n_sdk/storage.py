"""Local persistence adapter. No runtime, network, or payment dependencies.

Values (including credentials and delivered artifacts) are sealed by an injected
protector. SQLite only sees opaque blobs and task identifiers.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Protocol


class Protector(Protocol):
    def seal(self, raw: bytes) -> bytes: ...
    def open(self, sealed: bytes) -> bytes: ...


class LocalStore:
    def __init__(self, path: str | Path = ":memory:", protector: Protector | None = None):
        if protector is None and str(path) != ":memory:":
            raise ValueError("持久化存储必须配置加密器")
        self.protector = protector
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                namespace TEXT NOT NULL, key TEXT NOT NULL, value BLOB NOT NULL,
                PRIMARY KEY(namespace, key));
            CREATE TABLE IF NOT EXISTS calls (
                scope TEXT NOT NULL, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, outcome BLOB, created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(scope, task_id));
        """)

    def _encode(self, value) -> bytes:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return self.protector.seal(raw) if self.protector else raw

    def _decode(self, value):
        return json.loads((self.protector.open(value) if self.protector else value).decode("utf-8"))

    def put(self, namespace: str, key: str, value) -> None:
        sealed = self._encode(value)
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO settings VALUES (?,?,?)",
                             (namespace, key, sealed))

    def get(self, namespace: str, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE namespace=? AND key=?",
                                   (namespace, key)).fetchone()
        return self._decode(row[0]) if row else default

    def items(self, namespace: str) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key,value FROM settings WHERE namespace=?",
                                    (namespace,)).fetchall()
        return {r["key"]: self._decode(r["value"]) for r in rows}

    def delete(self, namespace: str, key: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM settings WHERE namespace=? AND key=?", (namespace, key))

    def claim(self, scope: str, task_id: str, fingerprint: str) -> bool:
        with self._lock, self._db:
            old = self._db.execute("SELECT fingerprint FROM calls WHERE scope=? AND task_id=?",
                                    (scope, task_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError("同一任务标识已用于不同的请求；请使用新的任务标识")
                return False
            at = time.time()
            self._db.execute("INSERT INTO calls VALUES (?,?,?,'RUNNING',NULL,?,?)",
                             (scope, task_id, fingerprint, at, at))
            return True

    def finish(self, scope: str, task_id: str, outcome: dict) -> None:
        sealed = self._encode(outcome)
        with self._lock, self._db:
            self._db.execute("UPDATE calls SET state=?,outcome=?,updated=? WHERE scope=? AND task_id=?",
                             (outcome["state"], sealed, time.time(), scope, task_id))

    def task(self, scope: str, task_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM calls WHERE scope=? AND task_id=?",
                                   (scope, task_id)).fetchone()
        if not row:
            return None
        return {**dict(row), "outcome": self._decode(row["outcome"]) if row["outcome"] else None}

    def interrupt_unfinished(self) -> None:
        # Crash recovery never repeats an execution whose remote effects are unknown.
        with self._lock, self._db:
            self._db.execute("UPDATE calls SET state='INTERRUPTED',updated=? WHERE state='RUNNING'",
                             (time.time(),))

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT scope,task_id,state,created,updated FROM calls ORDER BY updated DESC LIMIT ?",
                (max(1, min(limit, 100)),)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
