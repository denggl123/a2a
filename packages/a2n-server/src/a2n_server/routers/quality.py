"""质量证据：使用评价、模板偏差、试用与毕业。

这是"使用端能不能真实评估质量"的落点。三条纪律写死在数据形状里：

  1. **三段证据分开呈现、绝不合成一个总分**：
     ① 客观表现（谁测的）② 质量偏差（硬指标，可复算）③ 使用评价（归一化软评分）。
     合成一个总分等于把"平台探测的 200ms"和"某人心情好给的 90 分"搅在一起，
     出来的数字不可解释，也就不可验证。
  2. **每个数字带口径与样本数**：`rtt(平台探测 11 次)` / `72 分(来自 9 位使用者)`。
     "3 个人评的 88 分"和"300 个人评的 88 分"长得一样就是骗人。
  3. **样本不足时给原因，不给空白**：空白会被读成"这家很差"，
     必须写清是"还不够"，不是"不好"。

案例是**既有事实的投影**（tasks + usage_reports + ratings + 公证时间线），
不新增一份"案例表" —— 第二份真相迟早和第一份对不上。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from a2n_kernel.errors import A2NError
from a2n_acceptance import parse_template
from a2n_registry import registry, trial
from a2n_reputation import (counts, graduate_blockers, rate, ratings_for_tasks,
                            self_vs_independent, summary)
from a2n_store import conn

router = APIRouter(prefix="/v1", tags=["quality"])


def _avg(xs: list) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


# ---------------------------------------------------------------- 事实聚合

def objective_facts(agent_id: str) -> dict:
    """① 客观表现：只报事实与口径，不报解释。"""
    a = registry.get(agent_id) or {}
    conn_info = a.get("connection") if isinstance(a.get("connection"), dict) else {}
    observed = (conn_info or {}).get("observed") or {}
    rtt = [x for x in (observed.get("rtt") or []) if isinstance(x, int)]
    end_to_end = [x for x in (observed.get("total") or []) if isinstance(x, int)]
    cnt = conn().execute(
        "SELECT SUM(CASE WHEN state IN ('SETTLED','ACCEPTED') THEN 1 ELSE 0 END) done,"
        " SUM(CASE WHEN state='REJECTED' THEN 1 ELSE 0 END) rejected,"
        " SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END) failed,"
        " SUM(CASE WHEN trial=1 THEN 1 ELSE 0 END) trial_calls,"
        " SUM(CASE WHEN trial_kind='RECONNECT' THEN 1 ELSE 0 END) stability_calls"
        " FROM tasks WHERE node_id=?", (agent_id,)).fetchone()
    u = conn().execute(
        "SELECT COUNT(*) n, AVG(variance) v, SUM(attested) att FROM usage_reports"
        " WHERE node_id=?", (agent_id,)).fetchone()
    n_usage = int(u["n"] or 0)
    return {
        "rtt_avg_ms": _avg(rtt), "rtt_samples": len(rtt), "rtt_source": "平台探测",
        "observed_ms_avg": _avg(end_to_end), "observed_samples": len(end_to_end),
        "observed_source": "使用端实测",
        "done": int(cnt["done"] or 0), "rejected": int(cnt["rejected"] or 0),
        "failed": int(cnt["failed"] or 0), "trial_calls": int(cnt["trial_calls"] or 0),
        # 重连额度内的调用（节点重新上线采网络稳定参数用）：这些**不进质量模板**，
        # 但必须**看得见** —— 隔离不等于隐藏，否则"不在模板里"会变成"假装没发生"。
        "stability_calls": int(cnt["stability_calls"] or 0),
        "metering_avg_variance_ms": _avg([float(u["v"])]) if u["v"] is not None else None,
        "metering_attested": int(u["att"] or 0), "metering_samples": n_usage,
        "metering_source": "节点签名 + 平台观测对账",
    }


def quality_facts(agent_id: str) -> dict:
    """② 质量偏差（硬指标）：从 usage_reports 派生，绝不另存副本。

    **重连额度内的交付（`trial_kind='RECONNECT'`）不算进来** —— 那是节点重新
    上线采网络稳定参数用的，不是质量信号。被排除的样本数照样报出来
    （`stability_samples`），否则"不算进来"就变成"看不见"。

    `declared`（卡上有没有模板）与 `measured`（有没有算过偏差）是两件事，
    必须分开说 —— 否则一个"声明了模板但还没人调过"的节点会被报成
    "未声明验收模板"，让 owner 去补一个他早就声明了的东西。
    """
    row = conn().execute(
        "SELECT COUNT(*) n, AVG(u.quality) q,"
        " AVG(json_extract(u.deviation,'$.d_struct')) ds,"
        " AVG(json_extract(u.deviation,'$.d_completeness')) dc,"
        " AVG(json_extract(u.deviation,'$.d_content')) dk,"
        " SUM(CASE WHEN json_extract(u.deviation,'$.no_reference')=1 THEN 1 ELSE 0 END) nf"
        " FROM usage_reports u LEFT JOIN tasks t ON t.id = u.task_id"
        " WHERE u.node_id=? AND u.quality IS NOT NULL"
        " AND COALESCE(t.trial_kind,'') <> 'RECONNECT'", (agent_id,)).fetchone()
    stab = conn().execute(
        "SELECT COUNT(*) n FROM usage_reports u JOIN tasks t ON t.id = u.task_id"
        " WHERE u.node_id=? AND u.quality IS NOT NULL AND t.trial_kind='RECONNECT'",
        (agent_id,)).fetchone()
    n_stab = int(stab["n"] or 0)
    n = int(row["n"] or 0)
    if not n:
        # 没有偏差样本：再看卡上到底声没声明模板（这两句话不是一句）
        a = registry.get(agent_id) or {}
        try:
            card = json.loads(a.get("card_json") or "{}")
        except ValueError:
            card = {}
        tpl = parse_template(card)
        if tpl:
            note = (f"已声明验收模板 v{tpl['version']}，但还没有交付样本 —— "
                    f"不产生偏差（不伪造 0 偏差）")
        else:
            note = "未声明验收模板 —— 不产生偏差指标（不伪造 0 偏差）"
        if n_stab:
            note += (f"；另有 {n_stab} 次稳定性采样（重连额度）不计入 —— "
                     f"那是采网络稳定参数用的，不是质量证据")
        return {"declared": bool(tpl), "measured": False, "samples": 0, "quality": None,
                "components": None, "template_version": (tpl or {}).get("version"),
                "no_reference_samples": 0, "stability_samples": n_stab, "note": note}
    trow = conn().execute(
        "SELECT u.template_ref FROM usage_reports u LEFT JOIN tasks t ON t.id = u.task_id"
        " WHERE u.node_id=? AND u.template_ref IS NOT NULL"
        " AND COALESCE(t.trial_kind,'') <> 'RECONNECT'"
        " ORDER BY u.rowid DESC LIMIT 1", (agent_id,)).fetchone()
    tref = json.loads(trow["template_ref"]) if trow and trow["template_ref"] else {}
    return {
        "declared": True, "measured": True, "samples": n, "quality": round(float(row["q"]), 2),
        "components": {"d_struct": round(float(row["ds"] or 0), 4),
                       "d_completeness": round(float(row["dc"] or 0), 4),
                       "d_content": round(float(row["dk"] or 0), 4)},
        "template_version": tref.get("version"), "weights": tref.get("weights"),
        "no_reference_samples": int(row["nf"] or 0),
        "stability_samples": n_stab,
        "note": ("无参考的样本不计内容一致性（不是 0 偏差，是没测）"
                 + (f"；另有 {n_stab} 次稳定性采样（重连额度）不计入" if n_stab else "")),
    }


def case_list(agent_id: str, limit: int = 20) -> list[dict]:
    """④ 案例：既有事实的投影。三条排除写死在这里：

    · **排除自源调用**（`requester_id == 该 agent 的主人`）：自己调自己照吃试用额度
      （尊重提供者），但**不进公开案例** —— 否则"10 次免费"会被自开小号刷成证据。
      计数与证据分开算，两边都不吃亏。
    · **排除稳定性采样**（`trial_kind='RECONNECT'`）：那是节点重新上线采网络稳定
      参数用的，不是质量证据（与 `quality_facts` 同一判据，一处改两边同改）。
    · **不含调用方身份**：别人调的单不该把别人的名字挂出来。只给可核验的
      凭据指纹、口径数字与当次的偏差/评分。

    排除了多少条也照样报出来（`case_counts`）—— 否则"不进公开案例"就变成"看不见"。
    """
    owner = (registry.get(agent_id) or {}).get("principal_id")
    q = ("SELECT id, skill_id, state, trial, payload_hash, result_hash, currency, amount,"
         " created_at FROM tasks WHERE node_id=? AND state IN ('SETTLED','ACCEPTED')"
         " AND COALESCE(trial_kind,'') <> 'RECONNECT'")
    args: list = [agent_id]
    if owner:
        q += " AND requester_id <> ?"
        args.append(owner)
    q += " ORDER BY rowid DESC LIMIT ?"
    args.append(limit)
    rows = conn().execute(q, args).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    ratings = ratings_for_tasks(ids)
    usage = {r["task_id"]: dict(r) for r in conn().execute(
        f"SELECT task_id, quality, deviation, template_ref, attested, attest_reason, variance"
        f" FROM usage_reports WHERE task_id IN ({marks})", tuple(ids))}
    out = []
    for r in rows:
        tid = r["id"]
        u = usage.get(tid) or {}
        dev = json.loads(u["deviation"]) if u.get("deviation") else None
        rt = ratings.get(tid)
        out.append({
            "task_id": tid, "skill": r["skill_id"], "state": r["state"],
            "trial": bool(r["trial"]), "at": r["created_at"],
            "currency": r["currency"], "amount": r["amount"],
            # 凭据指纹：想让第三方复算时有抓手；不长到能反推内容
            "payload_hash": (r["payload_hash"] or "")[:16],
            "result_hash": (r["result_hash"] or "")[:16],
            "quality": u.get("quality"),
            "deviation": dev,
            "template_version": (json.loads(u["template_ref"]) or {}).get("version")
                                if u.get("template_ref") else None,
            "metering_attested": bool(u.get("attested")),
            "metering_reason": u.get("attest_reason"),
            "rating": ({"raw": rt["raw_score"], "normalized": rt["normalized"],
                        "credited": bool(rt["credited"]), "self_source": bool(rt["self_source"])}
                       if rt else None),
        })
    return out


# ---------------------------------------------------------------- 端点

def case_counts(agent_id: str) -> dict:
    """公开案例之外还剩多少条、为什么被排除 —— 让"排除"本身可见。"""
    owner = (registry.get(agent_id) or {}).get("principal_id")
    base = "FROM tasks WHERE node_id=? AND state IN ('SETTLED','ACCEPTED')"

    def one(sql: str, *args) -> int:
        return int(conn().execute("SELECT COUNT(*) n " + base + sql, args).fetchone()["n"] or 0)

    total = one("", agent_id)
    stab = one(" AND trial_kind='RECONNECT'", agent_id)
    self_n = (one(" AND requester_id=? AND COALESCE(trial_kind,'') <> 'RECONNECT'",
                  agent_id, owner) if owner else 0)
    return {"done_total": total, "public_basis": max(0, total - stab - self_n),
            "self_excluded": self_n, "stability_excluded": stab}

def evidence_summary(agent_id: str) -> dict:
    """列表页用的轻量证据摘要（三段各自独立，绝不合并成一个分）。

    列表行要能一眼看出"试用中 3/10 · 免费"与"有质量偏差 / 有评分"，
    但又不该在列表上摊开全部证据 —— 详情进 `/evidence`。
    """
    q = quality_facts(agent_id)
    return {
        "trial": trial.progress(agent_id),
        "ratings": summary(agent_id),
        "quality": {"declared": q["declared"], "measured": q["measured"],
                    "quality": q["quality"], "samples": q["samples"],
                    "stability_samples": q["stability_samples"],
                    "template_version": q["template_version"]},
        # 自源 vs 独立的差值参数（列表行也给，因为它是"该不该点进去看"的判断依据）
        "self_source": self_vs_independent(agent_id),
    }


@router.get("/agents/{agent_id}/evidence")
def evidence(agent_id: str, limit: int = 20):
    """一个 agent 的四段证据（公开只读）。"""
    if not registry.get(agent_id):
        raise HTTPException(404, "agent 不存在")
    return {
        "agent_id": agent_id,
        "objective": objective_facts(agent_id),
        "quality": quality_facts(agent_id),
        "ratings": summary(agent_id),
        # 自调用被允许，但要让使用者**看得见**它有没有、差多少 —— 给差异，不给结论
        "self_source": self_vs_independent(agent_id),
        "trial": trial.progress(agent_id),
        "cases": case_list(agent_id, limit),
        # 案例排除了多少条、为什么（隔离不等于隐藏）
        "case_counts": case_counts(agent_id),
    }


def _gate_facts(agent_id: str) -> tuple[dict, dict, list[str]]:
    a = registry.get(agent_id) or {}
    card = json.loads(a.get("card_json") or "{}")
    prog = trial.progress(agent_id)
    ev = counts(agent_id)
    disp = conn().execute(
        "SELECT COUNT(*) n FROM disputes d JOIN tasks t ON t.id = d.task_id"
        " WHERE t.node_id=? AND d.state='OPEN'", (agent_id,)).fetchone()
    unfin = conn().execute(
        "SELECT COUNT(*) n FROM tasks WHERE node_id=? AND state IN"
        " ('CREATED','ASSIGNED','SUBMITTED')", (agent_id,)).fetchone()
    blockers = graduate_blockers(card=card, trial=prog, evidence=ev,
                                 open_disputes=int(disp["n"] or 0),
                                 unfinished=int(unfin["n"] or 0))
    return prog, card, blockers


def owner_gate(agent_id: str) -> dict:
    """owner 视角的试用/毕业情况（含"还差什么"）。"""
    prog, _card, blockers = _gate_facts(agent_id)
    return {"trial": prog, "evidence": counts(agent_id),
            "blockers": blockers, "can_graduate": not blockers}


@router.get("/agents/{agent_id}/trial")
def trial_view(agent_id: str,
               principal: str | None = Header(default=None, alias="X-Principal")):
    """试用进度。**毕业阻塞原因只给 owner**（它不是公开信息的一部分）。"""
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    prog = trial.progress(agent_id)
    out = {"agent_id": agent_id, "trial": prog, "evidence": counts(agent_id),
           "price_visible": not prog["trial"]}
    if principal and a.get("principal_id") == principal:
        gate = owner_gate(agent_id)
        out["blockers"] = gate["blockers"]
        out["can_graduate"] = gate["can_graduate"]
    return out


@router.post("/agents/{agent_id}/graduate")
def graduate(agent_id: str, principal: str = Header(alias="X-Principal")):
    """毕业：此后才允许收费。四条判据缺一项就点不动 —— 且**写清还差什么**。"""
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    if a.get("principal_id") != principal:
        raise HTTPException(403, "只能给自己的 agent 毕业")
    prog, _card, blockers = _gate_facts(agent_id)
    if blockers:
        raise HTTPException(409, {"error": "还不能毕业", "blockers": blockers})
    trial.graduate(agent_id)
    return {"agent_id": agent_id, "trial": trial.progress(agent_id), "graduated": True}


@router.post("/agents/{agent_id}/trial/grant")
def grant_trial(agent_id: str, principal: str = Header(alias="X-Principal")):
    """重连补额（有界）：只在额度用尽、且同一身份本自然日还没补过时生效。"""
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    if a.get("principal_id") != principal:
        raise HTTPException(403, "只能给自己名下的 agent 补额")
    _state, msg = trial.grant(agent_id)
    return {"agent_id": agent_id, "message": msg, "trial": trial.progress(agent_id)}


class RatingIn(BaseModel):
    score: int
    note: str | None = None


@router.post("/tasks/{task_id}/rating")
def rate_task(task_id: str, body: RatingIn,
              principal: str = Header(alias="X-Principal")):
    """给一次**具体交付**打分。绑定 task_id（一次交付一条评价）+ 归一化 + 同源不计入。"""
    if not principal:
        raise HTTPException(401, "缺少 X-Principal")
    try:
        return rate(task_id, principal, body.score, body.note)
    except A2NError as e:
        raise HTTPException(e.http_status, str(e))
