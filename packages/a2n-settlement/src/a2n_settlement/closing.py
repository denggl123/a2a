"""结算收口（P1 / docs/OPTIMIZATION-PLAN.md §3.2）：**事件驱动的单一触发点**。

原先结算是在"提交那一次调用"里顺手做的：两条记账路各写各的
（积分在 `a2n-task.submit` 里内联、对等账户/直付/x402 在
`a2n-gateway.settle.dispatch` 里分派），**没有一个地方收口**，
于是"这次到底结没结、为什么没结"没有任何单一事实可查。

这一层补的就是这件事，五条对应计划里的五条：

  1. **单一触发点**：不管哪条路，结算结果都从 `record()` 进同一张表。
  2. **幂等**：`task_id` 是 PRIMARY KEY —— 一单只结一次。已结的单再报
     一次**不二次计数**，原样返回（重复事件不许二次结算，这是最容易出的事故）。
  3. **失败可见**：结算失败走 `mark_pending()` 落一条 PENDING 事实，
     **绝不静默**。落了库才谈得上"点开看是哪一单、为什么"。
  4. **定时对账**：`daily_cut()` = 「积分总量 ≡ 托管余额」对账 + 当日应结/已结/
     待处理汇总，结果按天落 `reconciliations`（同日重跑是覆盖，不追加）。
  5. **报警**：对账不平、或存在待处理，都发事件（运维页据此显红）。

保留既有优点：**钱、记账、终态仍在同一个事务里** ——
`record(commit=False)` 把提交权交给外层 `store.tx()`，与任务推进到 SETTLED 同生共死。

两条口径纪律：

  · 金额一律用**整数最小单位**（`amount_minor`），币种跟着钱走；
  · 汇总**绝不跨币种相加** —— 分与 10⁻⁶ 相加没有解释力。
    所以三个头条数字是**笔数**（无币种），金额按币种分行列出。

一个例外要说明白：**裸中继（relay）的成交不进这里**。它没有任务单
（`task_id=None`），也就没有幂等键 —— 强行编一个键只会造出无法追溯的账。
relay 的账走 deals 自己那条线（见 `_bill_relay_call`），这里不掺和。
"""
from __future__ import annotations

from typing import Any

from a2n_kernel.events import publish
from a2n_kernel.hashing import new_id, now_iso
from a2n_store import conn

from .reconcile import reconcile

STATE_SETTLED = "SETTLED"
STATE_PENDING = "PENDING"
STATE_FAILED = "FAILED"
OPEN_STATES = (STATE_PENDING, STATE_FAILED)

MODE_POINTS = "prepaid_points"
MODE_FREE = "free"


def _today(day: str | None = None) -> str:
    """取"今天"时的口径必须与写库的时间同源。

    库里所有 `updated_at` 都是 `now_iso()`（**UTC**），汇总又是按
    `substr(updated_at,1,10)` 比日期的 —— 所以这里若用 `date.today()`（本地时区），
    在 UTC+8 的 00:00–08:00 这段里"本地今天"已经翻页、而库里的 UTC 日期还没翻，
    今日结算/今日对账会读成 0 笔（控制台看着像"今天什么都没发生"）。

    同一口径的另两个先例：`trial.grant()` 按 UTC 解析 `last_grant_at`、
    `transport.hub` 用 `datetime.now(timezone.utc)`。
    """
    return day or now_iso()[:10]


def record(task_id: str, mode: str, amount_minor: int = 0, currency: str = "CNY",
           ref: str | None = None, commit: bool = True) -> dict[str, Any]:
    """记一笔**已结**的结算。幂等：同 task_id 已结 → 原样返回，不二次计数。

    commit=False：提交权交给外层事务（积分路径必须与"任务 → SETTLED"同生共死）。
    """
    if not task_id:
        raise ValueError("结算必须有 task_id —— 它是幂等键，没有键就没有幂等")
    cur = (currency or "CNY").upper()
    c = conn()
    row = c.execute("SELECT * FROM settlements WHERE task_id=?", (task_id,)).fetchone()
    if row and row["state"] == STATE_SETTLED:
        # 重复事件：不二次计数，也不改金额（改金额等于事后篡改一笔已入账的账）
        return {**dict(row), "already": True}
    ts = now_iso()
    amt = int(amount_minor or 0)
    if row:
        c.execute(
            "UPDATE settlements SET mode=?, amount_minor=?, currency=?, ref=?, state=?,"
            " reason=NULL, attempts=attempts+1, updated_at=? WHERE task_id=?",
            (mode, amt, cur, ref, STATE_SETTLED, ts, task_id))
    else:
        c.execute(
            "INSERT INTO settlements (task_id, mode, amount_minor, currency, ref, state,"
            " reason, attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,NULL,1,?,?)",
            (task_id, mode, amt, cur, ref, STATE_SETTLED, ts, ts))
    publish("settlement.recorded", {"task_id": task_id, "mode": mode, "state": STATE_SETTLED,
                                    "amount_minor": amt, "currency": cur, "ref": ref})
    if commit:
        c.commit()
    return {**dict(c.execute("SELECT * FROM settlements WHERE task_id=?", (task_id,)).fetchone()),
            "already": False}


def mark_pending(task_id: str, mode: str, reason: str, amount_minor: int = 0,
                 currency: str = "CNY", failed: bool = False,
                 commit: bool = True) -> dict[str, Any]:
    """结算失败/待重试 → 落一条待处理事实。**绝不静默。**

    `failed=True` 表示已经判定"结不动"（如实标 FAILED，不假装它还会自己好）；
    默认 PENDING 表示"还没结上，可重试"。

    已结的单**不许被降级**：一笔已经入账的账被说成"待处理"，对账时会凭空
    多出一笔差额 —— 宁可如实返回"已结"，也不制造第二个真相。
    """
    if not task_id:
        raise ValueError("待处理必须有 task_id（否则无从追溯是哪一单）")
    cur = (currency or "CNY").upper()
    c = conn()
    row = c.execute("SELECT * FROM settlements WHERE task_id=?", (task_id,)).fetchone()
    if row and row["state"] == STATE_SETTLED:
        return {**dict(row), "already": True, "downgraded": False}
    state = STATE_FAILED if failed else STATE_PENDING
    ts = now_iso()
    amt = int(amount_minor or 0)
    if row:
        c.execute(
            "UPDATE settlements SET mode=?, amount_minor=?, currency=?, state=?, reason=?,"
            " attempts=attempts+1, updated_at=? WHERE task_id=?",
            (mode, amt, cur, state, reason[:300], ts, task_id))
    else:
        c.execute(
            "INSERT INTO settlements (task_id, mode, amount_minor, currency, ref, state,"
            " reason, attempts, created_at, updated_at) VALUES (?,?,?,?,NULL,?,?,1,?,?)",
            (task_id, mode, amt, cur, state, reason[:300], ts, ts))
    publish("settlement.pending", {"task_id": task_id, "mode": mode, "state": state,
                                   "reason": reason[:300], "currency": cur})
    if commit:
        c.commit()
    return {**dict(c.execute("SELECT * FROM settlements WHERE task_id=?", (task_id,)).fetchone()),
            "already": False, "downgraded": True}


def get(task_id: str) -> dict | None:
    r = conn().execute("SELECT * FROM settlements WHERE task_id=?", (task_id,)).fetchone()
    return dict(r) if r else None


def pending(limit: int = 50) -> list[dict]:
    """待处理清单：能点开看到是哪一单、走的哪条路、为什么没结上。"""
    rows = conn().execute(
        "SELECT s.*, t.skill_id, t.node_id, t.requester_id FROM settlements s"
        " LEFT JOIN tasks t ON t.id = s.task_id"
        " WHERE s.state IN (?,?) ORDER BY s.updated_at DESC LIMIT ?",
        (*OPEN_STATES, limit)).fetchall()
    return [dict(r) for r in rows]


def recent(limit: int = 50) -> list[dict]:
    rows = conn().execute(
        "SELECT s.*, t.skill_id, t.node_id FROM settlements s"
        " LEFT JOIN tasks t ON t.id = s.task_id"
        " WHERE s.state=? ORDER BY s.updated_at DESC LIMIT ?", (STATE_SETTLED, limit)).fetchall()
    return [dict(r) for r in rows]


def by_currency(day: str | None = None, state: str | None = STATE_SETTLED) -> list[dict]:
    """按币种分行汇总（**绝不跨币种相加**：分与 10⁻⁶ 不是一个量纲）。"""
    where, args = "", []
    if day:
        where += " AND substr(updated_at,1,10)=?"
        args.append(day)
    if state:
        where += " AND state=?"
        args.append(state)
    rows = conn().execute(
        "SELECT currency, COUNT(*) n, COALESCE(SUM(amount_minor),0) amount_minor"
        " FROM settlements WHERE 1=1" + where + " GROUP BY currency ORDER BY currency",
        args).fetchall()
    return [dict(r) for r in rows]


def today_summary(day: str | None = None) -> dict[str, Any]:
    """今日三数：应结 / 已结 / 待处理。

    口径明写（数字没有口径就是噪声）：
      · 应结 = 当日**通过验收**的任务数（ACCEPTED ∪ SETTLED）——"该产生结算的事实"；
      · 已结 = 当日写入 SETTLED 的结算笔数（积分/对等/直付/x402 一起算）；
      · 待处理 = **当前**未结清的笔数（含跨日积压，不按日切）——积压不该被日期藏掉。
    金额不在这里给单一数字：跨币种相加是假的，用 `by_currency()` 分行看。
    """
    d = _today(day)
    c = conn()
    due = c.execute(
        "SELECT COUNT(*) n FROM tasks WHERE state IN ('ACCEPTED','SETTLED')"
        " AND substr(updated_at,1,10)=?", (d,)).fetchone()["n"]
    settled = c.execute(
        "SELECT COUNT(*) n FROM settlements WHERE state=? AND substr(updated_at,1,10)=?",
        (STATE_SETTLED, d)).fetchone()["n"]
    pending_n = c.execute(
        "SELECT COUNT(*) n FROM settlements WHERE state IN (?,?)",
        OPEN_STATES).fetchone()["n"]
    return {"day": d, "due_count": int(due), "settled_count": int(settled),
            "pending_count": int(pending_n), "settled_by_currency": by_currency(d)}


def history(limit: int = 30) -> list[dict]:
    rows = conn().execute(
        "SELECT * FROM reconciliations ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def latest() -> dict | None:
    r = conn().execute("SELECT * FROM reconciliations ORDER BY day DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def daily_cut(day: str | None = None) -> dict[str, Any]:
    """日切：对账 + 当日汇总 + 落库 + 报警。

    可重复跑（同日覆盖，不追加）：追加会把"今天跑了几次"变成一条假历史。
    """
    d = _today(day)
    rec = reconcile()                 # 积分总量 ≡ 托管余额（不平则冻结提现并发事件）
    s = today_summary(d)
    notes = []
    if not rec["balanced"]:
        notes.append(f"对账不平：积分 {rec['points_total']} − 托管 {rec['escrow_balance_fen']}"
                     f" = {rec['diff']}（已冻结提现）")
    if s["pending_count"]:
        notes.append(f"待处理 {s['pending_count']} 笔未结清")
    if not notes:
        notes.append("对账平、无待处理")
    note = "；".join(notes)
    ts = now_iso()
    c = conn()
    c.execute(
        "INSERT INTO reconciliations (id, day, points_total, escrow_balance_fen, diff,"
        " balanced, due_count, settled_count, pending_count, note, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(day) DO UPDATE SET"
        " points_total=excluded.points_total, escrow_balance_fen=excluded.escrow_balance_fen,"
        " diff=excluded.diff, balanced=excluded.balanced, due_count=excluded.due_count,"
        " settled_count=excluded.settled_count, pending_count=excluded.pending_count,"
        " note=excluded.note, created_at=excluded.created_at",
        (new_id("rc"), d, rec["points_total"], rec["escrow_balance_fen"], rec["diff"],
         1 if rec["balanced"] else 0, s["due_count"], s["settled_count"], s["pending_count"],
         note, ts))
    publish("closing.done", {"day": d, "balanced": rec["balanced"], "diff": rec["diff"],
                             "due_count": s["due_count"], "settled_count": s["settled_count"],
                             "pending_count": s["pending_count"]})
    if s["pending_count"]:
        publish("settlement.alert", {"kind": "pending", "count": s["pending_count"],
                                     "day": d})
    if not rec["balanced"]:
        publish("settlement.alert", {"kind": "imbalance", "diff": rec["diff"], "day": d})
    c.commit()
    return {"day": d, "reconcile": rec, "summary": s, "note": note,
            "row": latest()}


def alert_state() -> dict[str, Any]:
    """报警位（运维页红点）：不平 / 有积压 / 提现被冻结，任一成立即告警。"""
    last = latest()
    s = today_summary()
    reasons = []
    if last and not last["balanced"]:
        reasons.append(f"最近一次对账不平（{last['day']}，差 {last['diff']}）")
    if s["pending_count"]:
        reasons.append(f"待处理 {s['pending_count']} 笔")
    from .reconcile import is_frozen
    if is_frozen():
        reasons.append("提现已冻结")
    return {"alert": bool(reasons), "reasons": reasons, "last_cut": last}
