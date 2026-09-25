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
                state TEXT NOT NULL, request BLOB, outcome BLOB,
                evidence INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(scope, task_id));
        """)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(calls)")}
        if "request" not in columns:
            self._db.execute("ALTER TABLE calls ADD COLUMN request BLOB")
            self._db.commit()
        if "evidence" not in columns:
            self._db.execute("ALTER TABLE calls ADD COLUMN evidence INTEGER NOT NULL DEFAULT 0")
            # One-time migration of encrypted historical outcomes.  A corrupt
            # row is not evidence and must not prevent the node from starting.
            for row in self._db.execute("SELECT scope,task_id,outcome FROM calls"):
                try:
                    outcome = self._decode(row["outcome"]) if row["outcome"] else None
                except Exception:
                    outcome = None
                if self._has_evidence(outcome):
                    self._db.execute("UPDATE calls SET evidence=1 WHERE scope=? AND task_id=?",
                                     (row["scope"], row["task_id"]))
            self._db.commit()
        self._db.execute("CREATE INDEX IF NOT EXISTS calls_evidence_recent "
                         "ON calls(evidence, updated DESC)")
        self._db.commit()

    @staticmethod
    def _has_evidence(outcome) -> bool:
        if not isinstance(outcome, dict):
            return False
        if outcome.get("receipt"):
            return True
        settlement = outcome.get("settlement") or {}
        if not isinstance(settlement, dict):
            return False
        return (bool(settlement)
                and str(settlement.get("state") or "").upper() != "NOT_CONFIGURED"
                and str(settlement.get("mode") or "").lower() != "none")

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

    def count(self, namespace: str) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM settings WHERE namespace=?", (namespace,)
            ).fetchone()[0])

    def delete(self, namespace: str, key: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM settings WHERE namespace=? AND key=?", (namespace, key))

    def claim(self, scope: str, task_id: str, fingerprint: str,
              request: dict | None = None) -> bool:
        sealed_request = self._encode(request) if request is not None else None
        with self._lock, self._db:
            old = self._db.execute("SELECT fingerprint FROM calls WHERE scope=? AND task_id=?",
                                    (scope, task_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError("同一任务标识已用于不同的请求；请使用新的任务标识")
                return False
            at = time.time()
            self._db.execute(
                "INSERT INTO calls(scope,task_id,fingerprint,state,request,outcome,created,updated) "
                "VALUES (?,?,?,'RUNNING',?,NULL,?,?)",
                (scope, task_id, fingerprint, sealed_request, at, at))
            return True

    def finish(self, scope: str, task_id: str, outcome: dict) -> None:
        sealed = self._encode(outcome)
        with self._lock, self._db:
            self._db.execute("UPDATE calls SET state=?,outcome=?,evidence=?,updated=? "
                             "WHERE scope=? AND task_id=?",
                             (outcome["state"], sealed, int(self._has_evidence(outcome)),
                              time.time(), scope, task_id))

    def task(self, scope: str, task_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM calls WHERE scope=? AND task_id=?",
                                   (scope, task_id)).fetchone()
        if not row:
            return None
        return {**dict(row),
                "request": self._decode(row["request"]) if row["request"] else None,
                "outcome": self._decode(row["outcome"]) if row["outcome"] else None}

    def interrupt_unfinished(self) -> None:
        # Crash recovery never repeats an execution whose remote effects are unknown.
        with self._lock, self._db:
            self._db.execute("UPDATE calls SET state='INTERRUPTED',updated=? WHERE state='RUNNING'",
                             (time.time(),))
            rows = self._db.execute(
                "SELECT scope,task_id,outcome FROM calls WHERE state='CANCEL_REQUESTED'"
            ).fetchall()
            for row in rows:
                # CANCEL_REQUESTED was only an in-process interim view.  After a
                # restart there is no worker left that can replace it with the
                # eventual truth, so persist the uncertainty as terminal local
                # history and never replay the same task id.
                outcome = self._decode(row["outcome"]) if row["outcome"] else {}
                metadata = dict(outcome.get("metadata") or {})
                metadata.update({
                    "cancel_requested": True,
                    "cancel_acknowledged": False,
                    "remote_effect_unknown": True,
                    "remote_terminal": False,
                    "replay_safe": False,
                    "same_task_replay": "returns_recorded_outcome",
                })
                outcome.update({
                    "ok": False,
                    "task_id": row["task_id"],
                    "state": "INTERRUPTED",
                    "error": "节点在取消请求尚未确认时退出；远端结果未知，未自动重放",
                    "metadata": metadata,
                })
                self._db.execute(
                    "UPDATE calls SET state='INTERRUPTED',outcome=?,updated=? "
                    "WHERE scope=? AND task_id=?",
                    (self._encode(outcome), time.time(), row["scope"], row["task_id"]),
                )

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT scope,task_id,state,created,updated FROM calls ORDER BY updated DESC LIMIT ?",
                (max(1, min(limit, 100)),)).fetchall()
        return [dict(r) for r in rows]

    def recent_settlements(self, limit: int = 30) -> list[dict]:
        """Return evidence-bearing calls, never treating acceptance as a payment.

        A delivery-only verdict and NoSettlement's NOT_CONFIGURED marker are
        ordinary call history, not a receipt or a financial settlement.
        """
        wanted = max(1, min(int(limit), 100))
        out = []
        with self._lock:
            cursor = self._db.execute(
                "SELECT scope,task_id,state,created,updated,outcome FROM calls "
                "WHERE evidence=1 ORDER BY updated DESC")
            while len(out) < wanted:
                rows = cursor.fetchmany(100)
                if not rows:
                    break
                for row in rows:
                    try:
                        outcome = self._decode(row["outcome"]) if row["outcome"] else None
                    except Exception:  # noqa: BLE001 - one corrupt row must not hide all evidence
                        continue
                    if not isinstance(outcome, dict):
                        continue
                    receipt = outcome.get("receipt")
                    settlement = outcome.get("settlement") or {}
                    verdict = outcome.get("verdict") or {}
                    has_settlement = self._has_evidence({"settlement": settlement})
                    if not receipt and not has_settlement:
                        continue
                    out.append({
                        "scope": row["scope"], "task_id": row["task_id"],
                        "state": row["state"], "created": row["created"],
                        "updated": row["updated"], "has_receipt": bool(receipt),
                        "has_settlement": has_settlement,
                        "evidence_type": ("receipt_unverified" if receipt else
                                          "settlement_record"),
                        "settlement": settlement if has_settlement else {},
                        "verdict": verdict,
                    })
                    if len(out) >= wanted:
                        break
        return out

    def close(self) -> None:
        with self._lock:
            self._db.close()
