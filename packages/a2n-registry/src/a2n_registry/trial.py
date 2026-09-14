"""试用额度与毕业：新 agent 的前 N 次**完成**的调用免费。

用户定调：额度**不按人计**，按次数计 —— 提供者做满 10 次活儿却一分钱没有，
还要被要求"来自不同人"，体感就是被白嫖。所以谁调的都算，任何**完成**的调用都计数。

代价是"自己调自己"也能凑满 10 次。这个代价**不靠加限制消除**，而靠把它
**移出公开证据**来消除（见 `a2n_reputation.ratings` 的 `self_source`）：
自源调用照常吃额度，但不进公开案例、不进评分统计。两边都不吃亏 ——
靠的是"计数"与"证据"分开算，而不是靠为难某一方。

有界性（否则"重启即免费"就是无限免费）：
  · 首装给 10 次；重连**额度重置**为 5 次（不是又给 10）；
  · 每自然日最多补 1 次；
  · 生命周期**累计授予**有总上限（`TOTAL_GRANT_CAP`）。

表归属：`trial_offers` 归本包（agent 生命周期）。策略开关只在**注册那一刻**读
`A2N_TRIAL_DEFAULT` —— 之后每次调用只读表，不再看环境变量（否则一次重启就能
把所有人变回免费）。
"""
from __future__ import annotations

import os
import time

from a2n_kernel.hashing import now_iso
from a2n_store import conn, tx

TRIAL = "TRIAL"
GRADUATED = "GRADUATED"

INITIAL_CAP = 10          # 首装免费次数
RECONNECT_CAP = 5         # 重连后额度重置为 5
TOTAL_GRANT_CAP = 30      # 生命周期累计授予上限（含首装）
REGRANT_MIN_HOURS = 24    # 每自然日最多补 1 次


def default_enabled() -> bool:
    """部署级默认：新 agent 是否自动获得试用额度。

    读环境变量（**在注册那一刻**读，不在导入时读）——这样测试与演示环境
    可以把它关掉（`A2N_TRIAL_DEFAULT=0`），而不影响线上默认。
    """
    return os.environ.get("A2N_TRIAL_DEFAULT", "1") != "0"


def policy_for(card: dict) -> bool:
    """这张卡的 agent 是否适用试用策略：卡上显式 `x-a2n.trial=false` 即退出。"""
    t = ((card or {}).get("x-a2n") or {}).get("trial")
    if t is False:
        return False
    return default_enabled()


def open_for(agent_id: str, card: dict) -> dict:
    """注册时定下额度（幂等）。不适用策略的 agent 直接记为已毕业（永无试用）。"""
    enabled = policy_for(card)
    ts = now_iso()
    with tx():
        row = conn().execute("SELECT * FROM trial_offers WHERE agent_id=?", (agent_id,)).fetchone()
        if row:
            return dict(row)
        cap = INITIAL_CAP if enabled else 0
        conn().execute(
            "INSERT INTO trial_offers (agent_id, state, used, cap, total_used, granted_total,"
            " last_grant_at, graduated_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_id, TRIAL if enabled else GRADUATED, 0, cap, 0, cap,
             None, None if enabled else ts, ts, ts),
        )
    return state(agent_id) or {}


def state(agent_id: str) -> dict | None:
    r = conn().execute("SELECT * FROM trial_offers WHERE agent_id=?", (agent_id,)).fetchone()
    return dict(r) if r else None


def in_trial(agent_id: str) -> bool:
    """此刻是否处于试用期（= 该 agent 表现为免费）。

    没有行 = 这个 agent 早于试用策略、或策略关掉了 → 不试用（老年份的账不重算）。
    """
    s = state(agent_id)
    return bool(s and s["state"] == TRIAL and s["used"] < s["cap"])


def progress(agent_id: str) -> dict:
    """给界面用：试用进度与是否已毕业。

    `trial` 与 `in_trial()` 必须是同一个判据（额度用尽即不再"试用中"），
    否则界面会挂着一个"试用中 10/10"的徽标，而门禁早已按收费处理 —— 两处说两套话。
    """
    s = state(agent_id)
    if not s:
        return {"state": GRADUATED, "trial": False, "used": 0, "cap": 0, "remaining": 0}
    used, cap = int(s["used"]), int(s["cap"])
    active = s["state"] == TRIAL and used < cap
    return {"state": s["state"], "trial": active, "used": used, "cap": cap,
            "remaining": max(0, cap - used) if active else 0,
            "graduated_at": s["graduated_at"]}


def consume(agent_id: str, commit: bool = True) -> dict | None:
    """一次**完成**的调用吃掉一个额度。

    只由"验收通过"的路径调用（失败、超时、被退回都不计）—— 不能让失败吃掉额度。
    """
    with tx():
        row = conn().execute("SELECT * FROM trial_offers WHERE agent_id=?", (agent_id,)).fetchone()
        if not row or row["state"] != TRIAL:
            return dict(row) if row else None
        ts = now_iso()
        conn().execute(
            "UPDATE trial_offers SET used = used + 1, total_used = total_used + 1, updated_at=?"
            " WHERE agent_id=?", (ts, agent_id))
    return state(agent_id)


def grant(agent_id: str) -> tuple[dict | None, str]:
    """重连补额（节点断线重连后叫一次）。返回 (新状态, 说明)。

    三条有界：只在额度已用尽时补；同一 agent 每自然日最多一次；
    生命周期累计授予不超 `TOTAL_GRANT_CAP`。
    """
    row = state(agent_id)
    if not row:
        return None, "该 agent 没有试用记录（试用策略未启用）"
    if row["state"] != TRIAL:
        return row, "已毕业，不需要补额"
    if row["used"] < row["cap"]:
        return row, f"还有 {row['cap'] - row['used']} 次额度，不需要补"
    last = row["last_grant_at"]
    if last:
        try:
            from datetime import datetime, timezone
            prev = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if time.time() - prev.timestamp() < REGRANT_MIN_HOURS * 3600:
                return row, f"同一身份每自然日最多补 1 次（上次 {last}）"
        except ValueError:
            pass
    if int(row["granted_total"]) + RECONNECT_CAP > TOTAL_GRANT_CAP:
        return row, f"生命周期累计免费次数已达上限 {TOTAL_GRANT_CAP}"
    ts = now_iso()
    with tx():
        conn().execute(
            "UPDATE trial_offers SET cap=?, used=0, granted_total=granted_total+?,"
            " last_grant_at=?, updated_at=? WHERE agent_id=?",
            (RECONNECT_CAP, RECONNECT_CAP, ts, ts, agent_id))
    return state(agent_id), f"额度重置为 {RECONNECT_CAP} 次"


def graduate(agent_id: str) -> dict | None:
    """毕业：此后才允许收费。"""
    ts = now_iso()
    with tx():
        conn().execute("UPDATE trial_offers SET state=?, graduated_at=?, updated_at=?"
                       " WHERE agent_id=?", (GRADUATED, ts, ts, agent_id))
    return state(agent_id)
