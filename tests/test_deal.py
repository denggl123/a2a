"""一期交易：多账户、对等账户、达成交易、双向对账、出账。

这一组测试守的是**关系与事实**，不是资金：
- 关系：没有配对就不能交易，条款成交即冻结，额度不能超；
- 事实：双方各报各的，A2N 不替任何一方下结论，对不上就转争议。
"""
import pytest

from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_account import accounts, peers, supports
from a2n_deal import compute_amount_fen, deals, statements
from a2n_dispatch import discovery


def _card(name: str, skill: str = "ocr-pro", accepts: list | None = None) -> dict:
    card = {
        "name": name, "version": "1.0.0", "url": "http://localhost/a2a",
        "skills": [{"id": skill, "name": skill, "tags": ["demo"], "inputModes": ["application/json"]}],
        "x-a2n": {
            "deployment": {"region": "cn-east-2"},
            "sla": {"max_latency_ms": 5000},
            "price_hint": {skill: {"amount": 3, "unit": "fen_per_call"}},
            "metering": {"dimensions": [{"key": "call_count", "verifiable": True}]},
        },
    }
    if accepts is not None:
        card["accepts"] = accepts
    return card


def _agent(suffix: str, accepts: list | None = None, skill: str = "ocr-pro"):
    return registry.register(f"acct:p{suffix}", _card("n-" + suffix, skill, accepts))


@pytest.fixture(autouse=True)
def _wire_exposure():
    """额度检查需要交易层数据，测试里手动注入（生产由 wiring 装配）。"""
    peers.set_exposure(deals.exposure_fen)
    yield


def test_owner_can_hold_many_accounts():
    """一个主体多个账户：对公/对私/海外各一个，互不干扰。"""
    owner = f"acct:o{new_id('')[2:]}"
    a1 = accounts.create(owner, "对公-研发线", ref="6222****0001")
    a2 = accounts.create(owner, "对私-个人", ref=None)
    a3 = accounts.create(owner, "海外-Wise")
    got = accounts.list_by_owner(owner)
    assert {a["account_id"] for a in got} == {a1["account_id"], a2["account_id"], a3["account_id"]}
    assert len(got) == 3
    # 账户互不干扰：给一个配对，另一个不受影响
    ag = _agent(new_id("")[2:], accepts=["peer_account"])
    peers.propose(a1["account_id"], ag["agent_id"], auto_accept=True)
    assert len(peers.list_by_account(a1["account_id"])) == 1
    assert peers.list_by_account(a2["account_id"]) == []


def test_terms_keeps_only_known_fields():
    """未知字段不进库：防止塞进 A2N 执行不了的条款。"""
    owner = f"acct:o{new_id('')[2:]}"
    acc = accounts.create(owner, "测试账户")
    ag = _agent(new_id("")[2:], accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"], terms={
        "unit_prices": {"call_count": 3},
        "net_days": 15,
        "evil_clause": "出了事 A2N 全赔",       # 未知字段，应被丢弃
    }, auto_accept=True)
    assert "evil_clause" not in lk["terms"]
    assert lk["terms"]["net_days"] == 15
    assert lk["terms"]["unit_prices"] == {"call_count": 3}
    assert lk["terms"]["billing_cycle"] == "monthly"     # 默认值补上


def test_peer_state_machine_rejects_illegal_moves():
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"])
    assert lk["state"] == "PROPOSED"

    with pytest.raises(ValueError, match="非法状态迁移"):
        peers.suspend(lk["link_id"])          # PROPOSED 不能直接 SUSPENDED

    lk = peers.accept(lk["link_id"])
    assert lk["state"] == "ACTIVE"
    lk = peers.suspend(lk["link_id"])
    assert lk["state"] == "SUSPENDED"
    lk = peers.accept(lk["link_id"])          # 可恢复
    assert lk["state"] == "ACTIVE"
    lk = peers.close(lk["link_id"])
    assert lk["state"] == "CLOSED"
    with pytest.raises(ValueError, match="非法状态迁移"):
        peers.accept(lk["link_id"])           # CLOSED 是终态


def test_no_deal_without_active_peer():
    """没有配对 = 没有交易。这是把陌生人交易变成熟人交易的机制。"""
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"])     # 还停在 PROPOSED
    with pytest.raises(ValueError, match="不能达成交易"):
        deals.open(lk["link_id"], "ocr-pro")


def test_terms_are_frozen_at_deal_time():
    """条款快照：成交那一刻冻结，事后改条款不影响已成交的单。"""
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"],
                       terms={"unit_prices": {"call_count": 3}}, auto_accept=True)
    d = deals.open(lk["link_id"], "ocr-pro")
    assert d["terms"]["unit_prices"] == {"call_count": 3}

    deals.report(d["deal_id"], "provider", {"call_count": 10})
    price_before = deals.get(d["deal_id"])["reports"][0]["amount_fen"]
    assert price_before == 30

    # 事后把单价改到 100 —— 已成交的单不受影响
    c = peers.get(lk["link_id"])
    conn_update = c["terms"] | {"unit_prices": {"call_count": 100}}
    from a2n_store import conn
    import json
    conn().execute("UPDATE peer_links SET terms=? WHERE link_id=?",
                   (json.dumps(conn_update, ensure_ascii=False), lk["link_id"]))
    conn().commit()
    assert deals.get(d["deal_id"])["terms"]["unit_prices"] == {"call_count": 3}


def test_discover_filters_peer_ready_agents():
    """筛选支持对等账户的 agent —— 一期唯一能成交的对象。"""
    s1, s2 = new_id("")[2:5], new_id("")[2:5]
    a_peer = _agent(s1, accepts=["peer_account"], skill="ocr-pro")
    a_prepaid = _agent(s2, accepts=["prepaid_points"], skill="ocr-pro")

    assert supports(a_peer["agent_id"])
    assert not supports(a_prepaid["agent_id"])

    got = discovery.query({"skill": "ocr-pro"}, filt={"accepts": ["peer_account"]}, limit=50)
    ids = {g["agent_id"] for g in got}
    assert a_peer["agent_id"] in ids
    assert a_prepaid["agent_id"] not in ids

    # 没声明 accepts = 保守视为只接受预付费，一期筛不到
    a_none = _agent(new_id("")[2:5], accepts=None, skill="ocr-pro")
    got2 = discovery.query({"skill": "ocr-pro"}, filt={"accepts": ["peer_account"]}, limit=50)
    assert a_none["agent_id"] not in {g["agent_id"] for g in got2}


def test_two_sided_report_then_reconcile_matched():
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"],
                       terms={"unit_prices": {"call_count": 3}}, auto_accept=True)
    d = deals.open(lk["link_id"], "ocr-pro")
    assert d["state"] == "AGREED"

    deals.report(d["deal_id"], "provider", {"call_count": 10})      # 30 分
    assert deals.get(d["deal_id"])["state"] == "AGREED"             # 只有一方，还没交付
    deals.report(d["deal_id"], "requester", {"call_count": 10})     # 30 分
    assert deals.get(d["deal_id"])["state"] == "DELIVERED"

    d = deals.reconcile(d["deal_id"])
    assert d["state"] == "RECONCILED"
    assert d["recon"]["matched"] == 1
    assert d["recon"]["amount_fen"] == 30


def test_reconcile_disagreement_goes_to_dispute_and_takes_lower():
    """双方对不上 → 转争议，且认定金额取较小者（宁可少收，不可多记）。"""
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"], auto_accept=True)
    d = deals.open(lk["link_id"], "ocr-pro")
    deals.report(d["deal_id"], "provider", {}, amount_fen=10000)    # 节点报 100 元
    deals.report(d["deal_id"], "requester", {}, amount_fen=2000)    # 使用方报 20 元
    d = deals.reconcile(d["deal_id"])
    assert d["state"] == "DISPUTED"
    assert d["recon"]["matched"] == 0
    assert d["recon"]["amount_fen"] == 2000          # 取较小者
    assert d["recon"]["delta_fen"] == 8000


def test_credit_limit_blocks_new_deals():
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"],
                       terms={"credit_limit_fen": 100}, auto_accept=True)
    d = deals.open(lk["link_id"], "ocr-pro")
    deals.report(d["deal_id"], "provider", {}, amount_fen=150)
    deals.report(d["deal_id"], "requester", {}, amount_fen=150)
    deals.reconcile(d["deal_id"])
    assert deals.exposure_fen(lk["link_id"]) == 150

    ok, why = peers.usable(lk["link_id"])
    assert not ok and "额度" in why
    with pytest.raises(ValueError, match="不能达成交易"):
        deals.open(lk["link_id"], "ocr-pro")


def test_statement_aggregates_and_is_idempotent():
    suffix = new_id("")[2:]
    acc = accounts.create(f"acct:o{suffix}", "账户")
    ag = _agent(suffix, accepts=["peer_account"])
    lk = peers.propose(acc["account_id"], ag["agent_id"], auto_accept=True)
    for amt in (500, 700):
        d = deals.open(lk["link_id"], "ocr-pro")
        deals.report(d["deal_id"], "provider", {}, amount_fen=amt)
        deals.report(d["deal_id"], "requester", {}, amount_fen=amt)
        deals.reconcile(d["deal_id"])

    s = statements.issue(lk["link_id"], "2026-09")
    assert s["total_fen"] == 1200 and s["deal_count"] == 2

    # 幂等：同账期再出一次不会重复计入
    s2 = statements.issue(lk["link_id"], "2026-09")
    assert s2["statement_id"] == s["statement_id"] and s2["total_fen"] == 1200

    # 出账后不再计入未结敞口
    assert deals.exposure_fen(lk["link_id"]) == 0


def test_multidim_discovery_filters():
    """多维度查找：属地/延迟/标签/计量维度/信誉/预算/结算方式，可任意组合。

    算力是封装黑盒，不是网络契约——硬件字段（gpu/vram/cpu/并发）不进发现筛选，
    只保留部署属地（数据驻留/合规关心的事实）。
    """
    suffix = new_id("")[2:6]
    card = {
        "name": "强节点", "version": "1.0.0", "url": "http://localhost/a2a",
        "accepts": ["peer_account"],
        "skills": [{"id": "render", "name": "render", "tags": ["render", "batch"],
                    "inputModes": ["application/json"]}],
        "x-a2n": {
            "deployment": {"region": "cn-north"},
            "sla": {"max_latency_ms": 3000},
            "price_hint": {"render": {"amount": 50, "unit": "fen_per_call"}},
            "metering": {"dimensions": [{"key": "gpu_seconds", "verifiable": True},
                                        {"key": "output_tokens", "verifiable": True}]},
        },
    }
    ag = registry.register(f"acct:m{suffix}", card)["agent_id"]

    def q(filt: dict) -> set:
        return {g["agent_id"] for g in
                discovery.query({"skill": "render"}, filt=filt, limit=50)}

    assert ag in q({"region": "cn-north"})
    assert ag not in q({"region": "cn-south"})
    assert ag in q({"max_latency_ms": 3000}) and ag not in q({"max_latency_ms": 1000})
    assert ag in q({"tags": ["batch"]}) and ag not in q({"tags": ["翻译"]})
    assert ag in q({"metering_dim": "output_tokens"})
    assert ag not in q({"metering_dim": "wall_time"})       # 节点没声明这个计量维度
    assert ag in q({"min_reputation": 0.5}) and ag not in q({"min_reputation": 0.9})
    assert ag in q({"max_price_hint": 50}) and ag not in q({"max_price_hint": 10})
    # 组合：全都满足才出现
    assert ag in q({"region": "cn-north", "tags": ["batch"],
                    "accepts": ["peer_account"]})
    assert ag not in q({"region": "cn-north", "tags": ["翻译"]})


def test_no_amount_is_not_invented():
    """无可计费计量 = 0 分，绝不兜底造一笔来源不明的钱。"""
    assert compute_amount_fen({"call_count": 3}, {}) == 0
    assert compute_amount_fen({}, {"call_count": 10}) == 0
    assert compute_amount_fen({"call_count": 3}, {"call_count": 2}) == 6
