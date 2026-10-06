"""Local persistence adapter. No runtime, network, or payment dependencies.

Values (including credentials and delivered artifacts) are sealed by an injected
protector. SQLite only sees opaque blobs and task identifiers.
"""
from __future__ import annotations

from contextlib import contextmanager
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
        # 显式事务深度：0 = 不在 tx() 里，写入各自提交；>0 = 由外层 tx() 统一提交。
        self._tx_depth = 0
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

    def export_snapshot(self, path, protector):
        """Consistent online SQLite backup with all payloads rewrapped in memory."""
        destination = Path(path)
        if protector is None or destination.exists():
            raise ValueError("快照须使用新的目标文件与加密器")
        destination.parent.mkdir(parents=True, exist_ok=True)
        copied = sqlite3.connect(destination)
        try:
            with self._lock:
                if self._tx_depth:
                    raise RuntimeError("不能在业务写事务中导出快照")
                self._db.backup(copied)
            with copied:
                for namespace, key, sealed in copied.execute("SELECT namespace,key,value FROM settings"):
                    raw = json.dumps(self._decode(sealed), ensure_ascii=False, allow_nan=False).encode()
                    copied.execute("UPDATE settings SET value=? WHERE namespace=? AND key=?",
                                   (protector.seal(raw), namespace, key))
                for row in copied.execute("SELECT scope,task_id,request,outcome FROM calls"):
                    encrypted = []
                    for sealed in row[2:]:
                        raw = json.dumps(self._decode(sealed), ensure_ascii=False, allow_nan=False).encode() if sealed else None
                        encrypted.append(protector.seal(raw) if raw is not None else None)
                    copied.execute("UPDATE calls SET request=?,outcome=? WHERE scope=? AND task_id=?",
                                   (*encrypted, row[0], row[1]))
            if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("快照数据库校验失败")
            copied.execute("PRAGMA journal_mode=DELETE")
        except BaseException:
            copied.close()
            destination.unlink(missing_ok=True)
            raise
        finally:
            copied.close()

    def _execute(self, sql: str, params=()) -> None:
        """执行一条写语句。已在 `tx()` 里就把提交权交给外层（不提前提交）。"""
        try:
            self._db.execute(sql, params)
        except BaseException:
            if self._tx_depth == 0:
                self._db.rollback()
            raise
        if self._tx_depth == 0:
            self._db.commit()

    @contextmanager
    def tx(self):
        """显式写事务（BEGIN IMMEDIATE）：把"读-判-写"串成一个原子动作。

        与 `a2n_store.db.tx()`（§资金/额度）同一口径：WAL 下 IMMEDIATE 立刻拿写锁，
        并发的第二个写被挡在外面排队，而不是各自读到同一份旧值再一起写穿。凡
        "计数 + 样品"、"幂等检查 + 状态推进"这类地方必须用它，否则两次并发完成可以
        同时读到旧计数再一起写回，静默丢一次。

        参与事务的代码把提交权交出来（本对象的写方法靠 `_tx_depth` 自动识别），
        嵌套调用只最外层提交；中途抛错整段回滚，不留半截账。
        """
        with self._lock:
            depth = self._tx_depth
            if depth == 0:
                self._db.execute("BEGIN IMMEDIATE")
            self._tx_depth = depth + 1
            try:
                yield self
            except BaseException:
                self._tx_depth = depth
                if depth == 0:
                    self._db.rollback()
                raise
            else:
                self._tx_depth = depth
                if depth == 0:
                    self._db.commit()

    def put(self, namespace: str, key: str, value) -> None:
        sealed = self._encode(value)
        with self._lock:
            self._execute("INSERT OR REPLACE INTO settings VALUES (?,?,?)",
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
        with self._lock:
            self._execute("DELETE FROM settings WHERE namespace=? AND key=?", (namespace, key))

    def claim(self, scope: str, task_id: str, fingerprint: str,
              request: dict | None = None, *, state: str = "RUNNING") -> bool:
        sealed_request = self._encode(request) if request is not None else None
        with self._lock:
            old = self._db.execute("SELECT fingerprint FROM calls WHERE scope=? AND task_id=?",
                                    (scope, task_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError("同一任务标识已用于不同的请求；请使用新的任务标识")
                return False
            at = time.time()
            self._execute(
                "INSERT INTO calls(scope,task_id,fingerprint,state,request,outcome,created,updated) "
                "VALUES (?,?,?,?,?,NULL,?,?)",
                (scope, task_id, fingerprint, state, sealed_request, at, at))
            return True

    def mark_running(self, scope: str, task_id: str) -> None:
        with self._lock:
            self._execute("UPDATE calls SET state='RUNNING',updated=? "
                          "WHERE scope=? AND task_id=? AND state='READY'",
                          (time.time(), scope, task_id))

    def ready_tasks(self, limit: int = 64) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT scope,task_id FROM calls WHERE state='READY' "
                                    "ORDER BY created LIMIT ?", (limit,)).fetchall()
        return [self.task(row['scope'], row['task_id']) for row in rows]

    def requests(self, limit: int = 1000, *, after: tuple[str, str] | None = None) -> list[dict]:
        """Bounded migration/reconciliation scan; never publishes decrypted records."""
        with self._lock:
            rows = self._db.execute("SELECT scope,task_id FROM calls "
                                    "WHERE (scope,task_id) > (?,?) ORDER BY scope,task_id LIMIT ?",
                                    (*(after or ("", "")), max(1, min(int(limit), 10000)))).fetchall()
        return [self.task(row['scope'], row['task_id']) for row in rows]

    def iter_requests(self, batch_size=256):
        after = None
        while rows := self.requests(batch_size, after=after):
            yield from rows
            after = (rows[-1]["scope"], rows[-1]["task_id"])

    def finish(self, scope: str, task_id: str, outcome: dict) -> None:
        sealed = self._encode(outcome)
        with self._lock:
            self._execute("UPDATE calls SET state=?,outcome=?,evidence=?,updated=? "
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
        with self._lock:
            self._execute("UPDATE calls SET state='INTERRUPTED',updated=? WHERE state='RUNNING'",
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
                self._execute(
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

    def outcomes(self, *, scope: str = "", limit: int = 100) -> list[dict]:
        """Bounded read for local factual quality reports, using the existing call journal."""
        clause = " WHERE scope=?" if scope else ""
        params = (scope,) if scope else ()
        with self._lock:
            rows = self._db.execute("SELECT scope,task_id,state,outcome FROM calls" + clause +
                " ORDER BY updated DESC LIMIT ?", (*params, max(1, min(int(limit), 100)))).fetchall()
            return [{**dict(r), "outcome": self._decode(r["outcome"]) if r["outcome"] else {}} for r in rows]

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
