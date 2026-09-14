"""使用评价：绑定交付的评分 + **归一化** + 最小样本门。

## 为什么归一化

使用者打分有"手松 / 手紧"的差异。归一化要消掉的是**刻度位置**差异：
把一个评分者的平均分锚到 50，于是"50 分 = 他眼里的中性"，不同人打的分数才第一次可比。

公式（两段折线，锚点 μ 映到 50，同时保留 0→0、100→100 两个不动点）：

    f(x) = x · 50/μ                        (x ≤ μ)
    f(x) = 50 + (x − μ) · 50/(100 − μ)      (x > μ)

μ=80 时：f(80)=50、f(90)=75、f(40)=25 —— 与需求里给的三个数字完全一致。

## 必须定死的几件事（每一条都对应一个会反噬的坑）

1. **锚点 μ 是"这个评分者给所有人的平均"**，不是"他给这一家的平均"。
   若是后者，这家永远被拉回 50，做多好都涨不上去 = 判无期徒刑。
2. **收缩**：μ_用 = (n·μ_原始 + k·50)/(n + k)。样本少时把 μ 往 50 拉，
   否则第一次评分时 μ 就等于那一次的分，输出恒为 50（零信息）。
3. **边界保护**：μ=0 会爆炸、μ=100 会除零，所以夹在 [1, 99]。
4. **评分必须绑定一次具体交付**（task_id 唯一）：每个评分都能点开看那次交付，
   也天然防刷（凭空打分没有入口）。
5. **归一化分必须可复算**：μ_用 与样本数 n 随评分一起存档并对外公布 ——
   否则归一化就是平台说了算的黑盒，直接违反最核心的承诺。

## 两个最小样本门（"案例先行，分数后到"）

- **评分者的门**：他自己满 `PUBLISH_MIN_RATER` 次评分之前，他的评分只进本地校准池，
  `credited=0`（**不对外发布**）。
- **供应商的门**：它满 `PUBLISH_MIN_RATERS` 个**去重评分者**之前，详情页只给案例、不给分数。

对外分数永远带上"样本数"与"去重评分者数" —— "3 个人评的 88 分"和"300 个人评的
88 分"可信度完全不同，长得一样就是骗人。

## 最强的防线：同源不计入

自己调自己的那些次，**照常吃试用额度**（尊重提供者），但 `self_source=1`：
**不进公开案例、不进评分统计**。供应商之间互刷的防线只有三条 ——
绑定具体交付、去重且公开样本数、同源不计入；这三条不做，后面怎么调权重都是假的。
"""
from __future__ import annotations

from typing import Any

from a2n_kernel.errors import ConflictError, NotFoundError, ValidationError
from a2n_kernel.hashing import new_id, now_iso
from a2n_registry import registry
from a2n_store import conn, tx

K_SHRINK = 10             # μ 的收缩系数（样本少时往 50 拉）
PUBLISH_MIN_RATER = 20    # 评分者自己的样本门（满 20 次评分才对外发布）
PUBLISH_MIN_RATERS = 20   # 供应商的样本门（满 20 位去重评分者才对外给分）
MU_FLOOR, MU_CEIL = 1.0, 99.0
MIN_NON_SELF_CASES = 3    # 毕业所需的最少非自源案例

# 完成态（可以评价的两种终态）：SETTLED=积分结算完，ACCEPTED=非积分完成
DONE_STATES = ("SETTLED", "ACCEPTED")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_score(raw: float, mu_raw: float = 50.0, n: int = 0) -> tuple[float, float]:
    """原始分 → (归一化分, 本次实际用的 μ)。

    纯函数 + 无副作用：只要给出 raw / μ_原始 / n，谁都能算出同一个数。
    """
    mu = _clamp(float(mu_raw), MU_FLOOR, MU_CEIL)
    mu_used = _clamp((n * mu + K_SHRINK * 50.0) / (n + K_SHRINK), MU_FLOOR, MU_CEIL)
    x = float(raw)
    if x <= mu_used:
        out = x * 50.0 / mu_used
    else:
        out = 50.0 + (x - mu_used) * 50.0 / (100.0 - mu_used)
    return round(_clamp(out, 0.0, 100.0), 4), round(mu_used, 4)


def rater_stats(rater_id: str) -> dict:
    """评分者的校准统计（从 ratings 派生，不另存一份会漂移的副本）。

    只统计他的**非自源**评分 —— 自评不是信号。
    """
    row = conn().execute(
        "SELECT COUNT(*) AS n, AVG(raw_score) AS mu FROM ratings"
        " WHERE rater_id=? AND self_source=0", (rater_id,)).fetchone()
    n = int(row["n"] or 0)
    return {"n": n, "mu_raw": float(row["mu"]) if row["mu"] is not None else 50.0}


def rate(task_id: str, rater_id: str, raw_score: int, note: str | None = None) -> dict:
    """给一次**具体交付**打分。一条交付只允许一条评价（唯一索引即幂等闸）。

    归属校验：只有发起那次调用的人能评价（防事后被人代打）。
    """
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        raise ValidationError(f"评分必须是 0..100 的整数：{raw_score!r}")
    if not 0 <= score <= 100:
        raise ValidationError(f"评分必须在 0..100 之间：{score}")

    t = conn().execute(
        "SELECT requester_id, node_id, skill_id, state, trial FROM tasks WHERE id=?",
        (task_id,)).fetchone()
    if not t:
        raise NotFoundError(f"任务不存在：{task_id}")
    if t["requester_id"] != rater_id:
        raise ConflictError("只有发起那次调用的人能评价这一单")
    if t["state"] not in DONE_STATES:
        raise ConflictError(f"任务还没完成，不能评价（当前 {t['state']}）")

    agent_id = t["node_id"]
    owner = (registry.get(agent_id) or {}).get("principal_id")
    self_source = 1 if owner and owner == rater_id else 0

    prior = rater_stats(rater_id)
    normalized, mu_used = normalize_score(score, prior["mu_raw"], prior["n"])
    # 同源不计入；评分者未过最小样本门也只进本地校准池（不对外发布）
    credited = 0 if self_source else (1 if prior["n"] >= PUBLISH_MIN_RATER else 0)

    rid = new_id("rt")
    try:
        with tx():
            conn().execute(
                "INSERT INTO ratings (rating_id, task_id, agent_id, rater_id, skill, raw_score,"
                " normalized, mu_used, rater_n, self_source, credited, trial, note, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, task_id, agent_id, rater_id, t["skill_id"], score, normalized, mu_used,
                 prior["n"], self_source, credited, int(t["trial"] or 0), note, now_iso()),
            )
    except Exception as e:  # noqa: BLE001 - 唯一索引冲突 = 已经评过这一单
        if "UNIQUE" in str(e).upper():
            raise ConflictError("这一单已经评价过了")
        raise
    return {"rating_id": rid, "task_id": task_id, "agent_id": agent_id,
            "raw_score": score, "normalized": normalized, "mu_used": mu_used,
            "rater_n": prior["n"], "self_source": bool(self_source),
            "credited": bool(credited), "trial": bool(t["trial"])}


def list_for_agent(agent_id: str, *, credited_only: bool = True, limit: int = 50) -> list[dict]:
    q = ("SELECT * FROM ratings WHERE agent_id=?"
         + (" AND credited=1" if credited_only else "")
         + " ORDER BY rowid DESC LIMIT ?")
    return [dict(r) for r in conn().execute(q, (agent_id, limit)).fetchall()]


def ratings_for_tasks(task_ids: list[str]) -> dict[str, dict]:
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    rows = conn().execute(
        f"SELECT * FROM ratings WHERE task_id IN ({marks})", tuple(task_ids)).fetchall()
    return {r["task_id"]: dict(r) for r in rows}


def summary(agent_id: str) -> dict:
    """对外评分：只算 credited 的，且样本不够时**不给分**（给原因，不给空白）。"""
    row = conn().execute(
        "SELECT COUNT(*) AS samples, COUNT(DISTINCT rater_id) AS raters,"
        " AVG(normalized) AS score FROM ratings WHERE agent_id=? AND credited=1",
        (agent_id,)).fetchone()
    samples, raters = int(row["samples"] or 0), int(row["raters"] or 0)
    published = raters >= PUBLISH_MIN_RATERS
    return {
        "published": published,
        "score": round(float(row["score"]), 2) if (published and row["score"] is not None) else None,
        "samples": samples,
        "raters": raters,
        "needed_raters": max(0, PUBLISH_MIN_RATERS - raters),
    }


def counts(agent_id: str) -> dict:
    """该 agent 的评价计数（含自源与未发布），给毕业判据与界面用。"""
    row = conn().execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN self_source=0 THEN 1 ELSE 0 END) AS non_self,"
        " SUM(CASE WHEN self_source=0 AND credited=1 THEN 1 ELSE 0 END) AS public_cases,"
        " COUNT(DISTINCT CASE WHEN self_source=0 THEN rater_id END) AS distinct_raters"
        " FROM ratings WHERE agent_id=?", (agent_id,)).fetchone()
    return {"total": int(row["total"] or 0),
            "non_self": int(row["non_self"] or 0),
            "public_cases": int(row["public_cases"] or 0),
            "distinct_raters": int(row["distinct_raters"] or 0)}


# ---- 毕业判据（纯函数：拿素材算，不碰库）----

def card_gaps(card: dict) -> list[str]:
    """上架参数齐全性：缺一项就不许毕业。

    这条同时堵住另一个隐患：**参数不全就不可能出现"标了价却算不出钱"的单**。
    """
    ext = (card or {}).get("x-a2n") or {}
    gaps = []
    book = ext.get("price_book") or card.get("price_book") or {}
    priced = any(float(e.get("amount") or 0) > 0
                 for by_cur in (book or {}).values()
                 for spec in (by_cur or {}).values()
                 for e in ((spec or {}).get("dimensions") or []))
    if not priced:
        gaps.append("还没有标价（价目表里没有非零单价）")
    if not ((ext.get("metering") or {}).get("dimensions")):
        gaps.append("还没声明计量维度")
    if not (ext.get("sla") or {}):
        gaps.append("还没声明 SLA")
    if not ext.get("acceptance_template"):
        gaps.append("还没声明验收模板")
    if not ((card or {}).get("accepts") or ext.get("accepts")):
        gaps.append("还没声明结算方式（accepts）")
    return gaps


def graduate_blockers(*, card: dict, trial: dict, evidence: dict,
                      open_disputes: int = 0, unfinished: int = 0) -> list[str]:
    """四条判据全满足才准毕业；返回"还差什么"（空列表 = 可以毕业）。"""
    out: list[str] = []
    used, cap = int(trial.get("used") or 0), int(trial.get("cap") or 0)
    if trial.get("state") != "GRADUATED" and used < cap:
        out.append(f"免费额度还没用尽（{used}/{cap} 次完成）")
    out += card_gaps(card)
    non_self = int(evidence.get("non_self") or 0)
    if non_self < MIN_NON_SELF_CASES:
        out.append(f"公开证据不够：还差 {MIN_NON_SELF_CASES - non_self} 条非自源案例"
                   f"（现有 {non_self} 条）")
    if open_disputes:
        out.append(f"还有 {open_disputes} 个未了结的争议")
    if unfinished:
        out.append(f"还有 {unfinished} 单没走完（未完成/未签收）")
    return out


__all__ = ["normalize_score", "rater_stats", "rate", "summary", "counts",
           "list_for_agent", "ratings_for_tasks", "card_gaps", "graduate_blockers",
           "K_SHRINK", "PUBLISH_MIN_RATER", "PUBLISH_MIN_RATERS", "MIN_NON_SELF_CASES"]
