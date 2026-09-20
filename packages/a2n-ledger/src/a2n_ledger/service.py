"""M2 账本：append-only 复式记账 + hash 链。

三条铁律在这里落地：
  1. 账本不知道业务 —— 只认 ref_type / ref_id
  2. 没有余额字段 —— 余额由 SUM(delta) 得出
  3. 增发只有一个入口 —— mint 检查调用者身份，burn 同理
"""
from __future__ import annotations

import inspect
import sqlite3
from typing import Any

from a2n_store import conn
from a2n_kernel.hashing import GENESIS, chain_hash, new_id, now_iso

# 唯一被允许触发增发/销毁的调用点（持牌方回调）
_ALLOWED_MINT_CALLERS = {"a2n_server.routers.custodian"}


def _caller_module() -> str:
    stack = inspect.stack()
    return stack[2].frame.f_globals.get("__name__", "?") if len(stack) > 2 else "?"


class Ledger:
    def _last_hash(self) -> str:
        row = conn().execute("SELECT hash FROM ledger_entries ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS

    def post(self, account_id: str, delta: int, ref_type: str, ref_id: str,
             commit: bool = True) -> dict[str, Any]:
        """写入一条账目。delta 可正可负，但网内流转的净额必须为 0。

        commit=False 用于"多条账目必须同生共死"的场景（如提现冻结）：
        提交权交给外层的 store.tx()，中途失败整段回滚，不留半截账。
        """
        if not isinstance(delta, int):
            raise TypeError("积分必须为整数（1 积分 = 0.01 元）")
        c = conn()
        entry_id = new_id("le")
        prev_hash = self._last_hash()
        ts = now_iso()
        h = chain_hash(prev_hash, {
            "id": entry_id, "account": account_id, "delta": delta,
            "ref_type": ref_type, "ref_id": ref_id, "ts": ts,
        })
        c.execute(
            "INSERT INTO ledger_entries (id, account_id, delta, ref_type, ref_id, prev_hash, hash, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (entry_id, account_id, delta, ref_type, ref_id, prev_hash, h, ts),
        )
        if commit:
            c.commit()
        return {"id": entry_id, "account_id": account_id, "delta": delta, "hash": h, "created_at": ts}

    def mint(self, account_id: str, amount: int, ref_id: str) -> dict:
        """增发：仅随充值。调用者必须是持牌方回调模块。"""
        if _caller_module() not in _ALLOWED_MINT_CALLERS:
            raise PermissionError(f"mint 只允许由持牌方充值回调触发，当前调用者：{_caller_module()}")
        return self.post(account_id, amount, "deposit", ref_id)

    def burn(self, account_id: str, amount: int, ref_id: str) -> dict:
        """销毁：仅随提现打款成功。"""
        if _caller_module() not in _ALLOWED_MINT_CALLERS:
            raise PermissionError(f"burn 只允许由持牌方打款回调触发，当前调用者：{_caller_module()}")
        return self.post(account_id, -amount, "withdraw", ref_id)

    def total_points(self) -> int:
        row = conn().execute("SELECT COALESCE(SUM(delta),0) AS t FROM ledger_entries").fetchone()
        return int(row["t"])

    def balance(self, account_id: str) -> int:
        row = conn().execute(
            "SELECT COALESCE(SUM(delta),0) AS b FROM ledger_entries WHERE account_id=?", (account_id,)
        ).fetchone()
        return int(row["b"])

    def entries(self, account_id: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if account_id:
            return conn().execute(
                "SELECT * FROM ledger_entries WHERE account_id=? ORDER BY rowid DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        return conn().execute("SELECT * FROM ledger_entries ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()

    def head_rowid(self) -> int:
        """当前账本高水位（最后一条的 rowid）。

        批次共识需要"从哪里到哪里"，而 id 是无序随机串 —— 只有 rowid 能表达区间。
        没有账目时返回 0，表示"创世之前"。
        """
        row = conn().execute("SELECT COALESCE(MAX(rowid),0) AS r FROM ledger_entries").fetchone()
        return int(row["r"])

    def batch_range(self, from_rowid: int, to_rowid: int | None = None) -> list[dict[str, Any]]:
        """取 [from_rowid, to_rowid] 闭区间的账目，按 rowid 升序。

        这是共识批次的输入。升序不可协商：Merkle 根对顺序敏感，
        顺序不一致就算内容一样也会算出不同的根。
        """
        to = self.head_rowid() if to_rowid is None else int(to_rowid)
        rows = conn().execute(
            "SELECT rowid, * FROM ledger_entries WHERE rowid>? AND rowid<=? ORDER BY rowid ASC",
            (int(from_rowid), to),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def leaf(entry: dict[str, Any]) -> str:
        """批次里的一个叶子。

        直接用账目自身的 hash —— 它是 chain_hash(prev_hash, 字段)，
        既含内容又含位置，等于把"顺序"和"内容"一起提交给了 Merkle 根。
        """
        return entry["hash"]

    def verify_chain(self) -> tuple[bool, str]:
        rows = conn().execute("SELECT * FROM ledger_entries ORDER BY rowid ASC").fetchall()
        prev = GENESIS
        for r in rows:
            expect = chain_hash(prev, {
                "id": r["id"], "account": r["account_id"], "delta": r["delta"],
                "ref_type": r["ref_type"], "ref_id": r["ref_id"], "ts": r["created_at"],
            })
            if r["prev_hash"] != prev:
                return False, f"断裂于 {r['id']}: prev_hash 不匹配"
            if r["hash"] != expect:
                return False, f"篡改于 {r['id']}: hash 不匹配"
            prev = r["hash"]
        return True, f"完整，共 {len(rows)} 条"


def ensure_account(account_id: str, kind: str, name: str, kyc_status: str = "pending") -> None:
    """幂等建户。**判据与写入必须是同一条语句**。

    这里原来写的是"先 SELECT 再 INSERT"，在并发下是错的：两条线程可以同时查到
    "还没有这个户"，然后一起插，第二条撞 `UNIQUE(accounts.id)` 把整次调用打成 500。
    以前撞不上，因为四个演示节点是四个进程、四个主体、四个账号；**一个节点一个身份**
    （2026-09-20）之后，同一主体的四条注册线程必然同时来建同一个户 —— 真踩：
    本机节点只上架成功一张卡（另外三张 500 IntegrityError），后续全线级联红。
    `ON CONFLICT(id) DO NOTHING` 把"查"与"插"合成一步，没有可以插进去的间隙。

    仍然**自带提交**（调用方约定：不能放进别人的写事务里，见 a2n-task 的预算托管户）。
    """
    conn().execute(
        "INSERT INTO accounts (id, kind, name, kyc_status, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(id) DO NOTHING",
        (account_id, kind, name, kyc_status, now_iso()),
    )
    conn().commit()


def list_accounts() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM accounts ORDER BY kind, id").fetchall()
