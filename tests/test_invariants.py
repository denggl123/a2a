"""三条红线测试 —— 挂了就不许合并。

铁律三「廉洁刻进代码」在这里变成可执行的约束：
  1. 增发函数不存在（唯一入口被白名单锁死）
  2. 总量恒等于托管
  3. 账本 append-only
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from a2n_custodian import get_custodian
from a2n_store import conn
from a2n_ledger import Ledger
from a2n_task import tasks
from a2n_server.routers.custodian import DepositIn, deposit
from a2n_kernel.hashing import new_id
from a2n_settlement.reconcile import reconcile

ROOT = Path(__file__).resolve().parents[1]


def test_mint_burn_only_callable_from_custodian_router():
    """全网唯一能触发增发/销毁的调用点，必须是持牌方回调。"""
    allowed = {"ledger/service.py", "custodian.py"}
    offenders = []
    skip_dirs = {".venv", "__pycache__", "data", ".git", "sdk", "tests"}
    for p in ROOT.rglob("*.py"):
        if p.name in allowed or skip_dirs & set(p.parts):
            continue
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"\.(mint|burn)\s*\(", text):
            offenders.append(f"{p.relative_to(ROOT)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not offenders, f"发现越权的增发/销毁调用：{offenders}"


def test_ledger_mint_refuses_unauthorized_caller():
    """运行时兜底：即使绕过静态检查，mint 也会拒绝。"""
    with pytest.raises(PermissionError):
        Ledger().mint("acct:evil", 1000000, "hack")


def test_ledger_is_append_only():
    """账本表禁止 UPDATE / DELETE。"""
    # 成对记账保证不改变积分总量，避免污染其他用例的对账不变式
    Ledger().post("acct:appendonly-a", 1, "test", "t1")
    Ledger().post("acct:appendonly-b", -1, "test", "t1")
    for sql in ("UPDATE ledger_entries SET delta=999 WHERE ref_id='t1'",
                "DELETE FROM ledger_entries WHERE ref_id='t1'"):
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            conn().execute(sql)
        conn().rollback()


def test_no_balance_column_anywhere():
    """没有余额字段，就没有"改余额"这个动作。"""
    cols = {r["name"] for r in conn().execute("PRAGMA table_info(ledger_entries)")}
    assert "balance" not in cols and "balance_after" not in cols
    acct_cols = {r["name"] for r in conn().execute("PRAGMA table_info(accounts)")}
    assert "balance" not in acct_cols


def test_points_total_equals_escrow():
    """每日对账：积分总量 ≡ 托管余额，差一分即冻结提现。"""
    user = "acct:inv-" + new_id("")[2:]
    deposit(DepositIn(account_id=user, amount_fen=5000))
    rec = reconcile()
    assert rec["balanced"], rec
    assert rec["points_total"] == rec["escrow_balance_fen"]
    assert Ledger().balance(user) == 5000


def test_chain_verifies_and_detects_tamper():
    ok, _ = Ledger().verify_chain()
    assert ok
    # 直接改文件层（绕过触发器）应当被哈希链发现
    c = conn()
    row = c.execute("SELECT * FROM ledger_entries ORDER BY rowid DESC LIMIT 1").fetchone()
    c.execute("PRAGMA writable_schema=ON")
    try:
        c.execute("UPDATE sqlite_master SET sql = sql WHERE name='ledger_entries'")
        c.execute("DROP TRIGGER IF EXISTS ledger_no_update")
        c.execute("UPDATE ledger_entries SET delta=? WHERE id=?", (row["delta"] + 7, row["id"]))
        c.commit()
        ok2, msg = Ledger().verify_chain()
        assert not ok2, "账本被篡改却没被发现"
        assert "篡改" in msg
    finally:
        c.execute("UPDATE ledger_entries SET delta=? WHERE id=?", (row["delta"], row["id"]))
        c.commit()
        c.execute(SQL_RECREATE_TRIGGER)
        c.commit()


SQL_RECREATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE forbidden'); END;
"""
