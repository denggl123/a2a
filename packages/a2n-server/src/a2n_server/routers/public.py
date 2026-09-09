"""对外公开：行情、凭证链、对账、管理台聚合。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException

from a2n_store import conn
from a2n_ledger import Ledger
from a2n_settlement import settlement
from a2n_settlement import reconcile
from a2n_market import stats
from a2n_notary import notary
from a2n_registry.reachability import describe

router = APIRouter(prefix="/v1", tags=["public"])


@router.get("/market/stats")
def market_stats():
    return stats()


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
    managed = conn().execute(
        "SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt FROM tasks WHERE requester_id=? AND state='SETTLED'",
        (principal,)
    ).fetchone()
    roster = conn().execute("SELECT roster FROM rosters WHERE principal_id=?", (principal,)).fetchone()
    return {
        "reconcile": rec,
        "ledger_chain": {"ok": ledger_ok, "message": ledger_msg},
        "notary_chain": {"ok": notary_ok, "message": notary_msg},
        "points_balance": Ledger().balance(principal),
        "provided": {"agents": int(provided["n"] or 0), "tasks_done": int(provided["d"] or 0),
                     "earned_points": int(provided["e"] or 0)},
        "managed": {"tasks_settled": int(managed["n"] or 0), "spent_points": int(managed["amt"] or 0),
                    "roster_size": len(json.loads(roster["roster"])) if roster else 0},
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
    roster_row = conn().execute("SELECT roster FROM rosters WHERE principal_id=?", (principal,)).fetchone()
    roster_ids = json.loads(roster_row["roster"]) if roster_row else []
    roster_agents = []
    for aid in roster_ids:
        a = conn().execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
        if a:
            d = dict(a)
            for k in ("compute", "sla", "price_hint", "metering"):
                if d.get(k):
                    d[k] = json.loads(d[k])
            roster_agents.append(d)
    return {"tasks": tasks_list, "roster": roster_agents}


@router.post("/ops/reset")
def reset(confirm: str = ""):
    if confirm != "yes":
        raise HTTPException(400, "需要 confirm=yes")
    reconcile.wipe_demo_data()
    return {"ok": True}
