"""本轮整顿的回归护栏：价目贯通、计费归一、资金原子性、表归属。

每一条都对应一个曾经真实存在的缺陷 —— 测试不是为了覆盖率，
是为了让这些坑不再以别的形态回来。

注意：套件共用一个临时库，所以这里的 skill 名一律带随机后缀（否则
limit 截断会漏掉自己注册的 agent），充值一律走持牌方回调（否则破坏
"网内积分总量 == 托管总额"这条不变量）。
"""
from __future__ import annotations

import threading
import uuid

import pytest

from a2n_deal.deal import compute_amount_fen
from a2n_dispatch import discovery
from a2n_registry import registry
from a2n_settlement.service import compute_amount


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _card(skill: str, v1: bool = False) -> dict:
    ext = {}
    if v1:
        ext["price_hint"] = {skill: {"amount": 3, "unit": "point_per_call"}}
    else:
        ext["price_book"] = {skill: {"CNY": {"dimensions": [
            {"key": "call_count", "amount": 3, "per": 1}]}}}
    return {"name": "t-" + skill, "url": "http://localhost:9000/a2a",
            "skills": [{"id": skill, "tags": []}], "x-a2n": ext}


def _found(skill: str) -> list[dict]:
    return discovery.query({"skill": skill}, limit=100)


# ---------- 1. v2 价目贯通：发现结果必须给出 v2 价目与币种 ----------

def test_discovery_exposes_v2_price_book():
    skill = _uniq("s-v2")
    a = registry.register("p-book", _card(skill))
    row = next(r for r in _found(skill) if r["agent_id"] == a["agent_id"])
    # 曾经只读 v1 的 price_hint 列，v2 卡恒空 → 筛选与展示全哑
    assert row["price_book"][skill]["CNY"][0]["amount"] == 3
    assert row["currencies"] == ["CNY"]


def test_discovery_still_reads_v1_card():
    """v1 老卡不改一行也要能读到价（price_book() 内部回退）。"""
    skill = _uniq("s-v1")
    a = registry.register("p-v1", _card(skill, v1=True))
    row = next(r for r in _found(skill) if r["agent_id"] == a["agent_id"])
    assert row["price_book"][skill]["CNY"][0]["amount"] == 3


def test_budget_filter_works_on_v2_card():
    skill = _uniq("s-budget")
    registry.register("p-filter", _card(skill))
    hit = discovery.query({"skill": skill}, {"max_price_minor": 10}, limit=100)
    none = discovery.query({"skill": skill}, {"max_price_minor": 1}, limit=100)
    assert hit and not none      # 单价 3 分：上限 10 命中，上限 1 落空


# ---------- 2. 计费归一：两处算法必须同结果 ----------

def test_deal_and_settlement_agree_on_billable_only():
    """gpu_seconds 不可计费：成交域曾经照样给它算钱。"""
    dims = {"call_count": 2, "gpu_seconds": 999}
    prices = {"call_count": 5, "gpu_seconds": 100}
    assert compute_amount_fen(prices, dims) == compute_amount(prices, dims, 10 ** 9) == 10


def test_deal_honours_per_in_v2_entries():
    per_entry = {"output_tokens": {"amount": 10, "per": 1000}}   # 每千 token 10
    assert compute_amount_fen(per_entry, {"output_tokens": 2000}) == 20


# ---------- 3. 资金原子性：并发提现不许超提 ----------

def test_concurrent_withdraw_cannot_overdraw():
    from a2n_server.routers.custodian import DepositIn, deposit
    from a2n_wallet import Wallet

    acc = _uniq("acc-w")
    deposit(DepositIn(account_id=acc, amount_fen=100))   # 走持牌方：托管与积分配对

    w = Wallet()
    results, errors = [], []

    def go():
        try:
            results.append(w.request(acc, 100))
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))

    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # 余额 100，四笔各提 100：只能成功一笔，其余余额不足
    assert len(results) == 1, f"超提了：成功 {len(results)} 笔，errors={errors}"
    assert len(errors) == 3


# ---------- 4. 表归属：写 agents 表只能经 registry ----------

def test_reputation_written_through_registry():
    a = registry.register("p-rep", _card(_uniq("s-rep")))
    before = registry.get(a["agent_id"])["reputation"]
    from a2n_reputation import apply_event
    after = apply_event(a["agent_id"], "acceptance.passed")
    assert after == pytest.approx(before + 0.02, abs=1e-4)
    assert registry.get(a["agent_id"])["reputation"] == after   # 落到库里了

    down = apply_event(a["agent_id"], "acceptance.failed")
    assert down == pytest.approx(after - 0.08, abs=1e-4)


def test_reputation_clamped():
    a = registry.register("p-clamp", _card(_uniq("s-clamp")))
    from a2n_reputation import apply_event
    for _ in range(20):
        apply_event(a["agent_id"], "arbitration.lost")
    assert registry.get(a["agent_id"])["reputation"] == 0.0


def test_reputation_source_of_truth_in_registry_only():
    """架构护栏：信誉包不许自己 UPDATE agents（越界直写别人的表）。"""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "packages/a2n-reputation/src"
    for f in src.glob("**/*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        sql = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "UPDATE agents" in n.value]
        assert not sql, f"{f.name} 越界直写 agents 表：{sql}"
