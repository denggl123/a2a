"""对外公开：行情、凭证链、对账、管理台聚合。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException

from a2n_store import conn
from a2n_ledger import Ledger
from a2n_settlement import settlement
from a2n_settlement import reconcile
from a2n_market import board as market_board
from a2n_market import stats
from a2n_notary import notary
from a2n_registry import registry, rosters
from a2n_registry.reachability import describe
from a2n_settlement.price import price_book
from a2n_server.routers.registry import _project_agent

router = APIRouter(prefix="/v1", tags=["public"])


@router.get("/market/stats")
def market_stats():
    return stats()


@router.get("/market/board")
def market_board_view(limit: int = 60):
    """行情板：按能力 × 币种聚合的成交与挂牌事实。

    公开只读、无身份 —— 里面**没有任何一个主体标识**：只有统计量
    （笔数/均价/区间/供给数）。逐笔成交属于交易双方的账，不进行情。
    """
    return market_board(limit)


@router.get("/market/skill/{skill_id}")
def market_skill(skill_id: str,
                 principal: str | None = Header(default=None, alias="X-Principal")):
    """某个能力的行情明细：价位与供给节点清单。

    供给节点一律走与发现页同一个投影（_project_agent）—— 行情不能变成
    绕开匿名收口的旁路，否则"业务面看不到供应商"在这里就漏了。
    """
    entry = next((s for s in market_board(limit=500)["skills"] if s["skill_id"] == skill_id), None)
    ids = [r["agent_id"] for r in conn().execute(
        "SELECT DISTINCT agent_id FROM skills WHERE skill_id=?", (skill_id,))]
    suppliers = []
    for aid in ids:
        a = registry.get(aid)
        if a:
            suppliers.append(_project_agent(a, principal))
    return {"skill_id": skill_id, "market": entry or {
        "skill_id": skill_id, "supply": len(ids), "online": 0,
        "requested": 0, "done": 0, "success_rate": 0.0, "quotes": [],
    }, "suppliers": suppliers}

@router.get("/notary/receipts")
def receipts(limit: int = 50):
    return notary.recent(limit)


@router.post("/notary/verify")
def notary_verify():
    ok, msg = notary.verify()
    return {"ok": ok, "message": msg}


@router.get("/ledger/entries")
def ledger_entries(account_id: str | None = None, limit: int = 100):
    return [dict(r) for r in Ledger().entries(account_id, limit)]


@router.post("/ledger/verify")
def ledger_verify():
    ok, msg = Ledger().verify_chain()
    return {"ok": ok, "message": msg}


@router.get("/ops/reconcile")
def do_reconcile():
    return reconcile.reconcile()


@router.get("/settlements")
def settlements(limit: int = 50):
    return settlement.list_orders(limit)


@router.get("/console/overview")
def overview(principal: str = Header(alias="X-Principal")):
    """管理台首页：一边看对账是否干净，一边看我提供/我管理的规模。"""
    rec = reconcile.reconcile()
    ledger_ok, ledger_msg = Ledger().verify_chain()
    notary_ok, notary_msg = notary.verify()
    provided = conn().execute(
        "SELECT COUNT(*) n, COALESCE(SUM(tasks_done),0) d, COALESCE(SUM(earned),0) e"
        " FROM agents WHERE principal_id=?", (principal,)
    ).fetchone()
    # 完成的任务：两种终态都算（SETTLED=积分结算完；ACCEPTED=非积分模式完成）。
    # 积分支出只算 SETTLED —— 非积分模式的钱走 deals/pay_charges 各自的账，
    # 两个口径混在一个 SUM 里就是拿别的量纲当钱花。
    managed = conn().execute(
        "SELECT COUNT(*) n FROM tasks WHERE requester_id=? AND state IN ('SETTLED','ACCEPTED')",
        (principal,)
    ).fetchone()
    spent = conn().execute(
        "SELECT COALESCE(SUM(amount),0) amt FROM tasks WHERE requester_id=? AND state='SETTLED'",
        (principal,)
    ).fetchone()
    return {
        "reconcile": rec,
        "ledger_chain": {"ok": ledger_ok, "message": ledger_msg},
        "notary_chain": {"ok": notary_ok, "message": notary_msg},
        "points_balance": Ledger().balance(principal),
        "provided": {"agents": int(provided["n"] or 0), "tasks_done": int(provided["d"] or 0),
                     "earned_points": int(provided["e"] or 0)},
        "managed": {"tasks_settled": int(managed["n"] or 0), "spent_points": int(spent["amt"] or 0),
                    "roster_size": len(rosters.get(principal))},
        "market": stats(),
    }


@router.get("/console/provided")
def provided(principal: str = Header(alias="X-Principal")):
    """我提供的：我的节点/agent 及其计量与收益。"""
    agents = conn().execute(
        "SELECT * FROM agents WHERE principal_id=? ORDER BY registered_at DESC", (principal,)
    ).fetchall()
    out = []
    for a in agents:
        d = dict(a)
        for k in ("compute", "sla", "price_hint", "metering", "connection"):
            if d.get(k):
                d[k] = json.loads(d[k])
        # 价目一律从 card 派生：v1 的 price_hint 列在 v2 卡上恒空
        d["price_book"] = price_book(json.loads(a["card_json"] or "{}"))
        usage = conn().execute(
            "SELECT COUNT(*) n, COALESCE(SUM(json_extract(dims,'$.call_count')),0) calls,"
            " COALESCE(SUM(json_extract(dims,'$.output_tokens')),0) tokens,"
            " COALESCE(AVG(json_extract(dims,'$.wall_time_ms')),0) avg_ms"
            " FROM usage_reports WHERE node_id=?", (a["agent_id"],)
        ).fetchone()
        d["usage"] = {"reports": int(usage["n"] or 0), "calls": int(usage["calls"] or 0),
                      "tokens": int(usage["tokens"] or 0), "avg_ms": int(usage["avg_ms"] or 0)}
        d["balance_points"] = Ledger().balance(a["agent_id"])
        d["connection_info"] = describe(d)
        out.append(d)
    return out


@router.get("/console/charges/{charge_id}")
def charge_detail(charge_id: str, principal: str = Header(alias="X-Principal")):
    """一笔付款凭证的明细，并告诉前端它挂在哪笔任务上。

    存在的理由只有一个：流水行 —— 凭证 —— 任务这三跳是一次点击的事，
    不该让用户自己拼 id。鉴权同 call_detail：付款方或供给方 owner 才看得到。
    """
    row = conn().execute("SELECT * FROM pay_charges WHERE charge_id=?", (charge_id,)).fetchone()
    if not row:
        raise HTTPException(404, "凭证不存在")
    d = dict(row)
    owner = conn().execute("SELECT principal_id FROM agents WHERE agent_id=?",
                           (d.get("agent_id"),)).fetchone()
    if d.get("principal_id") != principal and not (owner and owner["principal_id"] == principal):
        raise HTTPException(403, "这笔凭证与你无关")
    d.pop("mandate_chain", None)   # 授权链原文体积大且含内部字段，按需再取
    return {"charge": d, "task_id": d.get("task_id")}


@router.get("/console/provided-calls")
def provided_calls(agent_id: str | None = None, limit: int = 50,
                   principal: str = Header(alias="X-Principal")):
    """他人调用我的 agent 的**逐笔**记录（供给方视角）。

    事实来源就是 tasks 表本身：node_id 指向我名下的 agent，就是"别人点了
    我的单"。不另建表、不做第二份事实 —— 汇总口径（/console/provided 的
    tasks_done）与逐笔口径必须能对上，对不上就是有个地方在撒谎。

    只回我自己名下的 agent；agent_id 传了也只当过滤条件，不是越权入口。
    """
    q = ("SELECT t.id, t.skill_id, t.requester_id, t.node_id, t.state,"
         " t.currency, COALESCE(t.amount_minor, t.amount) AS amount_minor,"
         " t.amount, COALESCE(t.budget_minor, t.budget) AS budget_minor, t.budget,"
         " t.source, t.delivery, t.reject_reason, t.fail_reason,"
         " t.created_at, t.updated_at, a.name AS agent_name"
         " FROM tasks t JOIN agents a ON a.agent_id = t.node_id"
         " WHERE a.principal_id = ? AND t.node_id IS NOT NULL")
    args: list = [principal]
    if agent_id:
        q += " AND t.node_id = ?"
        args.append(agent_id)
    q += " ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(q, args)]


@router.get("/console/calls/{task_id}")
def call_detail(task_id: str, principal: str = Header(alias="X-Principal")):
    """一笔调用的全流程明细：任务事实 + 凭证链时间线 + 计量 + 结算锚点。

    鉴权是**行为相关方**判定，不是"登录了就能看"：发起方（requester）与
    被派单节点的 owner 各看各的视角，第三方一律 403。task_id 不可猜不假，
    但匿名无限枚举能把整张网络的下单量摸出来。

    时间线取 notary.for_task —— 章是链上原生事实，不是这里另糊一份。
    """
    row = conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    t = dict(row)
    try:
        t["unit_prices"] = json.loads(t.get("unit_prices") or "{}")
    except ValueError:
        t["unit_prices"] = {}
    try:
        t["payload"] = json.loads(t.get("payload") or "{}")
    except ValueError:
        pass

    agent = None
    if t.get("node_id"):
        a = conn().execute(
            "SELECT agent_id, principal_id, name, status FROM agents WHERE agent_id=?",
            (t["node_id"],)).fetchone()
        if a:
            agent = dict(a)
    if t.get("requester_id") == principal:
        role = "requester"
    elif agent and agent.get("principal_id") == principal:
        role = "provider"
    else:
        raise HTTPException(403, "这笔调用与你无关")
    if agent:
        agent.pop("principal_id", None)      # 供给方身份不外发（owner 自己也不需要）

    usage = conn().execute(
        "SELECT usage_id, dims, observed, variance, result_hash, status, created_at"
        " FROM usage_reports WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    if usage:
        u = dict(usage)
        for k in ("dims", "observed"):
            try:
                u[k] = json.loads(u.get(k) or "{}")
            except ValueError:
                pass
        usage = u

    charges = []
    for r in conn().execute(
        "SELECT charge_id, method, channel, skill, currency,"
        " COALESCE(amount_minor, amount_fen) AS amount_minor, amount_fen,"
        " unit_price_minor, unit_price_fen, call_count, state,"
        " authorized_at, captured_at, channel_ref, created_at"
        " FROM pay_charges WHERE task_id=? ORDER BY created_at", (task_id,)):
        charges.append(dict(r))

    # 对等账户：A2A 视图里挂着 deal_id 时才去取交易与对账
    deal = None
    at = conn().execute(
        "SELECT deal_id, settle_mode, context_id FROM a2a_tasks WHERE task_id=?",
        (task_id,)).fetchone()
    if at and at["deal_id"]:
        d = conn().execute("SELECT * FROM deals WHERE deal_id=?", (at["deal_id"],)).fetchone()
        if d:
            deal = dict(d)
            rec = conn().execute("SELECT * FROM deal_recons WHERE deal_id=?", (d["deal_id"],)).fetchone()
            deal["recon"] = dict(rec) if rec else None
            deal["reports"] = [dict(x) for x in conn().execute(
                "SELECT party, dims, COALESCE(amount_minor, amount_fen) AS amount_minor,"
                " amount_fen, currency, reported_at FROM deal_reports WHERE deal_id=?",
                (d["deal_id"],))]

    settlements = [dict(r) for r in conn().execute(
        "SELECT id, state, currency, COALESCE(amount_minor, amount) AS amount_minor,"
        " amount, splits, rule_ref, created_at FROM settlement_orders WHERE task_id=?",
        (task_id,))]

    return {
        "task": t, "role": role,
        "agent": agent,
        "requester_id": t.get("requester_id"),
        "settle_mode": (at["settle_mode"] if at else None),
        "timeline": notary.for_task(task_id),
        "usage": usage, "charges": charges, "deal": deal, "settlements": settlements,
    }


@router.get("/console/managed")
def managed(principal: str = Header(alias="X-Principal")):
    """我管理的：我发起的任务、我调用的别人、我的市场列表。"""
    rows = conn().execute(
        "SELECT * FROM tasks WHERE requester_id=? ORDER BY rowid DESC LIMIT 50", (principal,)
    ).fetchall()
    tasks_list = []
    for r in rows:
        d = dict(r)
        d["unit_prices"] = json.loads(d["unit_prices"])
        tasks_list.append(d)
    # 市场列表读 owner 模块；元素可能是 agent_id 字符串或 p2p offer 字典，
    # 这里只取字符串（按 agent_id 回表），字典元素跳过——不再假定单一形态
    roster_ids = [x for x in rosters.get(principal) if isinstance(x, str)]
    roster_agents = []
    for aid in roster_ids:
        # 回表走 a2n-registry 的取数口，不自己 SELECT：agents 表的行解码
        # （JSON 列展开、v1 遗留列剔除）只有一个地方做，这里再抄一遍就是第二个真相。
        a = registry.get(aid)
        if not a:
            continue
        # 与 /v1/registry/agents 同源投影：自己看自己的卡走 owner 通道（含真实地址，上架回填要用），
        # 别人看别人的卡走业务面投影（剔除 principal_id/peer_ip/connection 设施细节）。
        # 主语是当前调用方 —— 收藏者的"我的市场"是给收藏者本人看的，
        # 所以这里主语统一传 principal（自己看自己的也走 owner 通道）。
        d = _project_agent(a, principal)
        d["price_book"] = price_book(json.loads(a["card_json"] or "{}"))
        roster_agents.append(d)
    return {"tasks": tasks_list, "roster": roster_agents}


@router.post("/ops/reset")
def reset(confirm: str = ""):
    if confirm != "yes":
        raise HTTPException(400, "需要 confirm=yes")
    reconcile.wipe_demo_data()
    return {"ok": True}
