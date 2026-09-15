"""结算收口（P1 / docs/OPTIMIZATION-PLAN.md §3.2）的护栏。

守五条：

  1. **单一触发点**：不管哪条记账路，结果都从 `closing.record` 进同一张表；
  2. **幂等**：task_id 是键 —— 重复事件**不二次计数**，也不许事后改金额；
  3. **失败可见**：结算失败必落一条待处理事实（绝不静默），
     已结的单**不许被降级**成待处理（那会凭空多出一笔差额）；
  4. **日切**：对账 + 汇总落库，同日重跑是覆盖（追加会把"跑了几次"变成假历史）；
  5. **口径**：金额按最小单位、**绝不跨币种相加**；运维视图不泄漏主体。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from a2n_kernel.hashing import new_id
from a2n_ledger import Ledger
from a2n_registry import registry
from a2n_settlement import closing
from a2n_settlement.service import settlement
from a2n_store import conn
from a2n_task import tasks
from a2n_server.routers.custodian import DepositIn, deposit

MODE_POINTS = closing.MODE_POINTS


def _card(skill: str, *, price: int | None = 5,
          accepts: list[str] | None = None) -> dict:
    ext = {
        "deployment": {"region": "cn-east-2"},
        "sla": {"max_latency_ms": 5000},
        "metering": {"dimensions": [{"key": "call_count", "verifiable": True}]},
    }
    if price is not None:
        ext["price_book"] = {skill: {"CNY": {"dimensions": [
            {"key": "call_count", "amount": price, "per": 1}]}}}
    return {"name": "sc-" + new_id("")[2:6], "version": "1.0.0", "url": None,
            "skills": [{"id": skill, "name": skill, "tags": []}],
            "accepts": ["peer_account"] if accepts is None else accepts,
            "x-a2n": ext}


def _provider(skill: str, *, price: int | None = 5,
              accepts: list[str] | None = None) -> dict:
    a = registry.register(f"acct:{new_id('')[:6]}",
                          _card(skill, price=price, accepts=accepts))
    registry.heartbeat(a["agent_id"])
    return a


def _points_call(skill: str, *, price: int = 5) -> tuple[dict, dict]:
    """走一次**积分**结算：充值 → 建任务（冻结）→ 提交（settle=True）。"""
    buyer = f"acct:b{new_id('')[:6]}"
    deposit(DepositIn(account_id=buyer, amount_fen=10000))
    agent = _provider(skill, price=price)
    t = tasks.create(buyer, skill, {"text": "hi"}, budget=100,
                     preferred_agents=[agent["agent_id"]], hold_budget=True)
    res = tasks.submit(t["id"], agent["agent_id"], {"text": "HI"},
                       {"call_count": 1, "wall_time_ms": 3}, settle=True)
    return agent, {"task": t, "res": res, "buyer": buyer}


# ---------------------------------------------------------------- 幂等

def test_record_is_idempotent_by_task_id():
    skill = "sc-" + new_id("")[2:6]
    _, ctx = _points_call(skill)
    tid = ctx["task"]["id"]

    row = closing.get(tid)
    assert row and row["state"] == closing.STATE_SETTLED
    assert row["mode"] == MODE_POINTS and row["ref"]          # 凭据号（so_id）落下来了

    # 重复上报同一单：不二次计数，也不改金额（改金额=事后篡改一笔已入账的账）
    again = closing.record(tid, MODE_POINTS, 99999, "CNY", ref="so_forged")
    assert again["already"] is True
    assert again["amount_minor"] == row["amount_minor"] and again["ref"] == row["ref"]
    assert conn().execute("SELECT COUNT(*) n FROM settlements WHERE task_id=?",
                          (tid,)).fetchone()["n"] == 1


def test_settled_is_never_downgraded_to_pending():
    skill = "sc-" + new_id("")[2:6]
    _, ctx = _points_call(skill)
    tid = ctx["task"]["id"]
    r = closing.mark_pending(tid, MODE_POINTS, "假的失败")
    assert r["downgraded"] is False and r["state"] == closing.STATE_SETTLED
    assert closing.get(tid)["state"] == closing.STATE_SETTLED


# ---------------------------------------------------------------- 单一触发点

def test_points_path_records_settlement_same_transaction():
    """积分路径：结算记录与"任务 → SETTLED"同生共死（同一个事务里落）。"""
    skill = "sc-" + new_id("")[2:6]
    _, ctx = _points_call(skill, price=5)
    tid = ctx["task"]["id"]
    res = ctx["res"]
    assert res["passed"] is True
    assert tasks.get(tid)["state"] == "SETTLED"
    row = closing.get(tid)
    assert row["state"] == closing.STATE_SETTLED
    assert row["ref"] == res["settlement"]["so_id"]        # 与分账单一一对应


def test_gateway_path_records_free_and_paid_modes(monkeypatch):
    """对等/直付/x402/免费都从同一个 record 进表（这里验免费那条）。"""
    from a2n_gateway import call as gw

    skill = "sc-" + new_id("")[2:6]
    # 真免费 = 既无价目表、又不接受任何结算方式。
    # 只清空价目表而留着 accepts:["peer_account"]，门禁会认成"收费"
    # （charging() 看的是 accepts，不是价目表）。
    agent = _provider(skill, price=None, accepts=[])

    def _fwd(agent_id, method, path, body, caller=None, task_id=None, dims=None):
        return {"status": 200, "body": {"body": {"text": "OK"}}}
    monkeypatch.setattr(gw.hub, "forward", _fwd)

    out = gw.invoke(agent["agent_id"], f"acct:{new_id('')[:6]}", skill=skill)
    assert out.state == "ACCEPTED"                         # 免费不走积分 → 停在 ACCEPTED
    row = closing.get(out.task_id)
    assert row["state"] == closing.STATE_SETTLED
    assert row["mode"] == closing.MODE_FREE and row["amount_minor"] == 0


# ---------------------------------------------------------------- 失败可见

def test_settlement_failure_lands_in_pending_not_silently(monkeypatch):
    """结算炸了：事务回滚 → 但在**事务外**补一条待处理，让失败留在账上。"""
    skill = "sc-" + new_id("")[2:6]
    buyer = f"acct:b{new_id('')[:6]}"
    deposit(DepositIn(account_id=buyer, amount_fen=10000))
    agent = _provider(skill, price=5)
    t = tasks.create(buyer, skill, {"text": "hi"}, budget=100,
                     preferred_agents=[agent["agent_id"]], hold_budget=True)

    def _boom(*a, **k):
        raise RuntimeError("托管方拒单：模拟结算失败")
    monkeypatch.setattr(settlement, "settle", _boom)

    with pytest.raises(RuntimeError):
        tasks.submit(t["id"], agent["agent_id"], {"text": "HI"},
                     {"call_count": 1}, settle=True)

    # 事务整体回滚：任务没被推进到 SETTLED（不留半截事实）
    assert tasks.get(t["id"])["state"] == "ASSIGNED"
    row = closing.get(t["id"])
    assert row is not None and row["state"] == closing.STATE_PENDING
    assert "模拟结算失败" in row["reason"]
    assert any(p["task_id"] == t["id"] for p in closing.pending())


# ---------------------------------------------------------------- 日切与口径

def test_daily_cut_persists_and_is_idempotent_per_day():
    skill = "sc-" + new_id("")[2:6]
    _, ctx = _points_call(skill)
    # 日期**从这笔结算自己记下的那一天取**，不要硬编码：
    # 写死一个日期，第二天跑就必然 0 笔 —— 那是测试的时间炸弹，不是产品问题。
    day = closing.get(ctx["task"]["id"])["created_at"][:10]
    first = closing.daily_cut(day)
    assert first["reconcile"]["balanced"] is True
    again = closing.daily_cut(day)
    assert again["row"]["id"] == first["row"]["id"]         # 同日覆盖，不追加
    assert conn().execute("SELECT COUNT(*) n FROM reconciliations WHERE day=?",
                          (day,)).fetchone()["n"] == 1
    hist = closing.history(5)
    assert hist[0]["day"] == day and hist[0]["settled_count"] >= 1


def test_summary_counts_are_counts_and_money_never_mixes_currencies():
    base = {r["currency"]: r["amount_minor"] for r in closing.by_currency()}
    skill = "sc-" + new_id("")[2:6]
    _, ctx = _points_call(skill)                            # 积分那条也是 CNY
    pts = closing.get(ctx["task"]["id"])["amount_minor"]
    closing.record("t_cny_" + new_id("")[:6], "peer_account", 300, "CNY", ref="dl_1")
    closing.record("t_usd_" + new_id("")[:6], "x402", 5000000, "USDC", ref="pc_1")

    s = closing.today_summary()
    assert isinstance(s["due_count"], int) and isinstance(s["pending_count"], int)
    cur = {r["currency"]: r for r in s["settled_by_currency"]}
    # 本测试只往 CNY 里加了 300 + 积分那一笔（**同一币种**才允许相加）
    assert cur["CNY"]["amount_minor"] - base.get("CNY", 0) == 300 + pts
    assert cur["USDC"]["amount_minor"] - base.get("USDC", 0) == 5000000
    # 分行：绝不出现"把 USDC 的 10⁻⁶ 和 CNY 的分加起来"的合计
    assert {"CNY", "USDC"} <= set(cur)
    assert len(s["settled_by_currency"]) == len(cur)


def test_alert_state_flags_pending():
    skill = "sc-" + new_id("")[2:6]
    tid = "t_pend_" + new_id("")[:6]
    closing.mark_pending(tid, MODE_POINTS, "托管方超时")
    a = closing.alert_state()
    assert a["alert"] is True and any("待处理" in r for r in a["reasons"])


# ---------------------------------------------------------------- 运维接口

def test_ops_endpoints_require_principal_and_do_not_leak_subjects():
    from a2n_server.app import app

    c = TestClient(app)
    assert c.get("/v1/ops/settlement").status_code == 400        # 缺身份 → 400（单一判据）

    skill = "sc-" + new_id("")[2:6]
    agent, ctx = _points_call(skill)
    closing.mark_pending("t_pend_" + new_id("")[:6], MODE_POINTS, "托管方超时")

    r = c.get("/v1/ops/settlement", headers={"X-Principal": "acct:ops"})
    assert r.status_code == 200
    body = r.json()
    assert {"due_count", "settled_count", "pending_count"} <= set(body["summary"])
    assert body["alert"]["alert"] is True
    blob = json.dumps(body, ensure_ascii=False)
    # 运维聚合视图不泄漏主体：调用方/供给方身份不出现在待处理与已结清单里
    pend = body["recent_pending"]
    assert pend and all(set(p) == set(("task_id", "mode", "state", "amount_minor",
                                       "currency", "ref", "reason", "attempts",
                                       "created_at", "updated_at")) for p in pend)
    # 凭据号要能穿过白名单：不然运维页的"凭据"列永远是"—"，
    # "能追回分账单/直付回执"就成了一句空话（这个缺口是活库核验⑩抓出来的）。
    paid_rows = [r for r in body["recent_settled"] if r["mode"] != "free"]
    assert paid_rows and all(r["ref"] for r in paid_rows)
    assert ctx["buyer"] not in blob and agent["agent_id"] not in blob

    # 立即日切：可重复跑
    r2 = c.post("/v1/ops/closing/run", headers={"X-Principal": "acct:ops"})
    assert r2.status_code == 200 and "reconcile" in r2.json()
    # 明细分页端点也在
    assert c.get("/v1/ops/settlement/pending",
                 headers={"X-Principal": "acct:ops"}).status_code == 200
    assert c.get("/v1/ops/closing/history",
                 headers={"X-Principal": "acct:ops"}).status_code == 200
