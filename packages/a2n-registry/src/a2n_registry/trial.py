"""试用额度与毕业：新 agent 的前 N 次**完成**的调用免费。

## 为什么是强制的（卡上不许退出）

**任何想被发现的 agent，前 10 次完成调用系统一律不计费。** 这条的目的不在补贴，
在使用方：**任何人都能先拿真实案例看看这东西是不是自己要的**，再决定付不付钱。

所以卡上**不提供任何退出通道** —— `x-a2n.trial=false` 这种声明会在上架时被直接
拒绝（`validate_card`），而不是被静默忽略：静默忽略等于让供给方以为自己退出了。
`A2N_TRIAL_DEFAULT` 是**部署级**开关（测试/演示用），不是供给方的出口。

## 额度不按人计

用户定调：额度**不按人计**，按次数计 —— 提供者做满 10 次活儿却一分钱没有，
还要被要求"来自不同人"，体感就是被白嫖。所以谁调的都算，任何**完成**的调用都计数。

代价是"自己调自己"也能凑满 10 次。这个代价**不靠加限制消除**，而靠把它
**移出公开证据**来消除（见 `a2n_reputation.ratings` 的 `self_source`）：
自源调用照常吃额度，但不进公开案例、不进评分统计。两边都不吃亏 ——
靠的是"计数"与"证据"分开算，而不是靠为难某一方。自调用也**不是白费**：
使用者能看到"自源案例 vs 独立使用者评分"的差值（`ratings.self_vs_independent`），
拿它预估质量 —— 给差异，不给结论。

## 两种额度，用途不同（`grant_kind`）

| 来源 | 额度 | 用途 | 这些案例 |
|---|---|---|---|
| 首装 `INITIAL` | 10 次 | 用真实案例换"可被信任" | **进**质量模板（就是毕业证据） |
| 重连 `RECONNECT` | 重置为 5 次 | 节点重新上线后**采网络稳定参数** | **不进**质量模板（不是质量信号） |

重连额度是**弱约束**：不检测"你是不是真的断过网"，只靠每自然日 1 次、
生命周期累计 ≤ 30 次这两道边界防滥用 —— 因为"随意断网刷额度"真正的代价是
**影响使用**，而过重的闸门同样影响使用。宁可留这一点缝，也不假装能查清。

## 有界性（否则"重启即免费"就是无限免费）

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

INITIAL = "INITIAL"        # 首装额度：用来换毕业证据（进质量模板）
RECONNECT = "RECONNECT"    # 重连额度：用来采网络稳定参数（不进质量模板）

INITIAL_CAP = 10          # 首装免费次数
RECONNECT_CAP = 5         # 重连后额度重置为 5
TOTAL_GRANT_CAP = 30      # 生命周期累计授予上限（含首装）
REGRANT_MIN_HOURS = 24    # 每自然日最多补 1 次


def default_enabled() -> bool:
    """部署级默认：新 agent 是否自动获得试用额度。

    读环境变量（**在注册那一刻**读，不在导入时读）——这样测试与演示环境
    可以把它关掉（`A2N_TRIAL_DEFAULT=0`），而不影响线上默认。

    **这是部署开关，不是供给方的出口**：卡上没有与之对应的字段。一个 agent
    想被网络发现，就必须先免费服务前 10 次（见模块 docstring）。
    """
    return os.environ.get("A2N_TRIAL_DEFAULT", "1") != "0"


def policy_for(card: dict) -> bool:
    """这张卡的 agent 是否适用试用策略。

    卡上**没有任何退出方式**：`x-a2n.trial=false` 会在 `validate_card` 被直接拒
    （不是在这里静默忽略 —— 静默忽略会让供给方以为自己退出了）。
    卡上写 `trial: true` 是允许的：它只是复述了本来就强制的规则。
    """
    return default_enabled()


def open_for(agent_id: str, card: dict) -> dict:
    """注册时定下额度（幂等）。策略被部署关掉时记为已毕业（永无试用）。"""
    enabled = policy_for(card)
    ts = now_iso()
    with tx():
        row = conn().execute("SELECT * FROM trial_offers WHERE agent_id=?", (agent_id,)).fetchone()
        if row:
            return dict(row)
        cap = INITIAL_CAP if enabled else 0
        conn().execute(
            "INSERT INTO trial_offers (agent_id, state, used, cap, total_used, granted_total,"
            " grant_kind, last_grant_at, graduated_at, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (agent_id, TRIAL if enabled else GRADUATED, 0, cap, 0, cap,
             INITIAL if enabled else None,
             None, None if enabled else ts, ts, ts),
        )
    return state(agent_id) or {}


def _kind_of(s: dict) -> str | None:
    """额度来源。老库补列（`grant_kind` 为 NULL）时兜一手：补列前**只存在首装额度**
    这一种，所以处于 TRIAL 的老记录如实算作 INITIAL —— 既不凭空升级成"重连采样"，
    也不因为补了一个列就把老 agent 的案例踢出质量模板。
    """
    kind = s.get("grant_kind")
    if kind is None and s["state"] == TRIAL:
        return INITIAL
    return kind


def current_kind(agent_id: str) -> str | None:
    """当前这段额度是哪来的：`INITIAL` / `RECONNECT` / None（没有额度）。

    任务侧在建单那一刻问它一次，把答案冻进 `tasks.trial_kind` ——
    之后补额、用完、毕业都不再改写那一单的性质（快照，不是实时查询）。
    """
    s = state(agent_id)
    return _kind_of(s) if s else None


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

    `stability` = 当前这段额度是重连额度（采网络稳定参数用）——
    界面必须把这句话说出来，否则用户会以为这些免费案例也是质量证据。
    """
    s = state(agent_id)
    if not s:
        return {"state": GRADUATED, "trial": False, "used": 0, "cap": 0, "remaining": 0,
                "grant_kind": None, "stability": False}
    used, cap = int(s["used"]), int(s["cap"])
    active = s["state"] == TRIAL and used < cap
    kind = _kind_of(s)
    return {"state": s["state"], "trial": active, "used": used, "cap": cap,
            "remaining": max(0, cap - used) if active else 0,
            "grant_kind": kind, "stability": active and kind == RECONNECT,
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

    这段额度的用途是**节点重新上线后采网络稳定参数**，不是继续攒质量证据 ——
    所以它的 `grant_kind` 记为 `RECONNECT`，落进它里面的案例**不进质量模板**
    （见 `a2n_reputation` 与 `routers/quality` 的 stability 过滤）。

    三条有界（**弱约束**：不检测"你究竟断没断过网"，也不许为此随意断网 ——
    真正的代价是影响使用，而过重的闸门同样影响使用）：
    只在额度已用尽时补；同一 agent 每自然日最多一次；
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
            " grant_kind=?, last_grant_at=?, updated_at=? WHERE agent_id=?",
            (RECONNECT_CAP, RECONNECT_CAP, RECONNECT, ts, ts, agent_id))
    return state(agent_id), (f"额度重置为 {RECONNECT_CAP} 次"
                             f"（重连采样：这段时间的案例不进质量模板）")


def graduate(agent_id: str) -> dict | None:
    """毕业：此后才允许收费。"""
    ts = now_iso()
    with tx():
        conn().execute("UPDATE trial_offers SET state=?, graduated_at=?, updated_at=?"
                       " WHERE agent_id=?", (GRADUATED, ts, ts, agent_id))
    return state(agent_id)
