"""多币种与扩展性：媒介注册表、按币种定价、选币门禁、双写与流水视图。

守的是三条纪律：
1. **扩展 = 注册数据，不改代码**：新币种/新媒介/新计价维度注册即生效；
2. **币种是事实不是默认值**：跨币种对账、混币出单、静默换币一律拒绝；
3. **双写不断供**：新列 amount_minor 与旧列 _fen 并存，读取 COALESCE。
"""
import pytest

from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_account import accounts, paymethods, peers
from a2n_custodian import MEDIA, UnknownMedium, register_medium
from a2n_deal import deals, statements
from a2n_gateway import choose_currency, invoke
from a2n_settlement import (DIMS, quote, register_dimension,
                            supported_currencies)
from a2n_store import conn
from a2n_transport import hub


@pytest.fixture(autouse=True)
def _clean_media_registry():
    """媒介注册表是全局字典：测试注册的扩展媒介用完即拆，避免跨用例污染。"""
    yield
    for code in ("bank_eur", "wallet_b"):
        MEDIA.pop(code, None)


# ---------------- 工具 ----------------

def _card(name: str, skill: str = "ocr-pro", accepts: list | None = None,
          price_book: dict | None = None) -> dict:
    ext: dict = {
        "compute": {"gpu": "4090", "vram_gb": 24, "region": "cn-east-2"},
        "metering": {"dimensions": [{"key": "call_count", "verifiable": True}]},
    }
    if price_book:
        ext["price_book"] = price_book
    else:
        ext["price_hint"] = {skill: {"amount": 3, "unit": "fen_per_call"}}
    card = {
        "name": name, "version": "1.0.0", "url": "http://localhost/a2a",
        "skills": [{"id": skill, "name": skill, "tags": [], "inputModes": ["application/json"]}],
        "x-a2n": ext,
    }
    if accepts is not None:
        card["accepts"] = accepts
    return card


# ---------------- 媒介注册表：扩展 = 加数据 ----------------

def test_new_currency_is_register_not_code():
    """注册一种新币种媒介后，账户层立刻认识它 —— 业务代码零改动。"""
    register_medium({"code": "bank_eur", "currency": "EUR", "exponent": 2,
                     "label": "欧元渠道", "auto_pay": True})
    owner = f"acct:o{new_id('')[2:]}"
    acc = accounts.create(owner, "欧洲-SEPA", currency="EUR")
    assert acc["currency"] == "EUR"
    assert acc["medium"] == "bank_eur"
    assert acc["exponent"] == 2


def test_unregistered_currency_rejected():
    """没注册的币种宁可拒绝，也不猜 —— 静默回退等于替业务做资金决策。"""
    owner = f"acct:o{new_id('')[2:]}"
    with pytest.raises(UnknownMedium):
        accounts.create(owner, "幽灵账户", currency="GHOST")


def test_same_currency_multi_medium_requires_explicit_choice():
    """同币种出现第二个媒介后，必须显式指定 —— 不替主体挑钱包。"""
    register_medium({"code": "wallet_b", "currency": "USDC", "exponent": 6,
                     "label": "第二家 USDC 钱包"})
    owner = f"acct:o{new_id('')[2:]}"
    with pytest.raises(ValueError, match="显式指定"):
        accounts.create(owner, "未指定媒介", currency="USDC")
    ok = accounts.create(owner, "指定媒介", currency="USDC", medium="wallet_b")
    assert ok["medium"] == "wallet_b"
    # 媒介与币种不符也要挡住
    with pytest.raises(ValueError, match="不符"):
        accounts.create(owner, "错配", currency="CNY", medium="wallet_b")


def test_default_account_per_currency():
    """同主体每个币种各有一个默认账户，设新默认自动让位旧的。"""
    owner = f"acct:o{new_id('')[2:]}"
    a1 = accounts.create(owner, "CNY主", currency="CNY", is_default=True)
    a2 = accounts.create(owner, "USDC", currency="USDC", medium="stablecoin", is_default=True)
    a3 = accounts.create(owner, "CNY备", currency="CNY", is_default=True)
    assert accounts.default_of(owner, "CNY")["account_id"] == a3["account_id"]
    assert accounts.default_of(owner, "USDC")["account_id"] == a2["account_id"]
    assert accounts.default_of(owner, "CNY")["account_id"] != a1["account_id"]


# ---------------- 计价维度与多币计价引擎 ----------------

def test_dimension_registry_extends_billable():
    """新计量维度注册即可计费 —— 结算代码不认识具体维度，只问注册表。"""
    register_dimension({"key": "render_seconds", "verifiable": True, "billable": True,
                        "label": "渲染秒数"})
    assert "render_seconds" in DIMS
    from a2n_settlement import is_billable
    assert is_billable("render_seconds")
    # 仅计量不计费的维度不进金额
    assert not is_billable("gpu_seconds")


def test_quote_multi_dim_per_and_final_rounding():
    """多维度叠加、按千收取、先精确累加末次取整 —— 逐项可复核。"""
    entries = [{"key": "call_count", "amount": 300, "per": 1},
               {"key": "output_tokens", "amount": 1, "per": 1000}]
    # call_count=3 → 900；output_tokens=1500 → 1500/1000*1=1.5 → 合计 901.5 → 取整 902
    out = quote(entries, {"call_count": 3, "output_tokens": 1500})
    assert out["amount_minor"] == 902
    assert out["capped"] is False
    assert len(out["lines"]) == 2
    # 不可计费维度（如 gpu_seconds）不进金额
    out2 = quote(entries, {"call_count": 1, "gpu_seconds": 99999})
    assert out2["amount_minor"] == 300
    # 预算封顶
    out3 = quote(entries, {"call_count": 100}, budget_minor=500)
    assert out3["amount_minor"] == 500 and out3["capped"] is True


# ---------------- 选币门禁 ----------------

def _register_multicurrency_agent(suffix: str):
    book = {"ocr-pro": {
        "CNY": {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]},
        "USDC": {"dimensions": [{"key": "call_count", "amount": 5000000, "per": 1}]},
    }}
    return registry.register(f"acct:m{suffix}", _card(f"mc-{suffix}",
                                                      accepts=["direct_pay"],
                                                      price_book=book))


def test_choose_currency_rules():
    suffix = new_id("")[2:6]
    agent = _register_multicurrency_agent(suffix)
    card = _card("x", accepts=["direct_pay"], price_book={"ocr-pro": {
        "CNY": {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]},
        "USDC": {"dimensions": [{"key": "call_count", "amount": 5, "per": 1}]}}})
    # 指定支持币种 → 原样返回
    assert choose_currency(card, "ocr-pro", "usdc") == "USDC"
    # 未指定 → 默认 CNY
    assert choose_currency(card, "ocr-pro") == "CNY"
    # 价目表没有 CNY → 取第一个币种，不猜
    book_usd_only = {"ocr-pro": {"USDC": {"dimensions": [{"key": "call_count", "amount": 5, "per": 1}]}}}
    card2 = _card("y", accepts=["direct_pay"], price_book=book_usd_only)
    assert choose_currency(card2, "ocr-pro") == "USDC"
    # 指定不支持的币种 → 明说，不静默换
    with pytest.raises(PermissionError, match="不支持"):
        choose_currency(card2, "ocr-pro", "CNY")
    assert agent["agent_id"]   # agent 已注册可用


def test_invoke_with_currency_end_to_end(monkeypatch):
    """多币直付端到端：USDC 价目 + USDC 渠道 → 凭证/任务/流水三处同币种。"""
    suffix = new_id("")[2:6]
    agent = _register_multicurrency_agent(suffix)
    agent_id = agent["agent_id"]
    principal = f"acct:c{suffix}"

    # 使用方登记一个 USDC 渠道
    paymethods.register(principal, "usdc-wallet", currency="USDC")

    # 通道转发就地返回成功（invoke 是 inline 交付）
    monkeypatch.setattr(hub, "forward", lambda *a, **k: {
        "status": 200, "body": {"body": {"answer": "42"}}})

    result = invoke(agent_id, principal, "ocr-pro", currency="USDC")
    assert result.ok and result.currency == "USDC"

    # 凭证：币种与最小单位金额快照 + 旧 _fen 双写
    row = conn().execute("SELECT * FROM pay_charges WHERE task_id=?",
                         (result.task_id,)).fetchone()
    assert row["currency"] == "USDC"
    assert row["unit_price_minor"] == 5000000 and row["amount_minor"] == 5000000
    assert row["amount_fen"] == 5000000        # 双写：老读取方不断供
    assert row["medium"] == "stablecoin"

    # 任务：同币种
    task = conn().execute("SELECT currency, amount_minor FROM tasks WHERE id=?",
                          (result.task_id,)).fetchone()
    assert task["currency"] == "USDC" and task["amount_minor"] == 5000000

    # 指定 agent 价目表里没有的币种 → 拒绝且不产生任何账
    with pytest.raises(PermissionError):
        invoke(agent_id, principal, "ocr-pro", currency="EUR")
    charges = conn().execute("SELECT COUNT(*) c FROM pay_charges WHERE principal_id=?",
                             (principal,)).fetchone()
    assert charges["c"] == 1


def test_direct_channel_currency_must_match(monkeypatch):
    """渠道绑定了币种就得认：USDC 渠道接不了 CNY 单 —— 门禁按币种过滤渠道。"""
    suffix = new_id("")[2:6]
    agent = _register_multicurrency_agent(suffix)
    agent_id = agent["agent_id"]
    principal = f"acct:x{suffix}"
    paymethods.register(principal, "usdc-wallet", currency="USDC")
    with pytest.raises(PermissionError, match="没有可用的支付方式"):
        invoke(agent_id, principal, "ocr-pro", currency="CNY")


def test_ledger_view_splits_payer_and_payee(monkeypatch):
    """流水视图：一笔成交拆付/收两行，双方各看各的方向，币种如实。"""
    suffix = new_id("")[2:6]
    agent = _register_multicurrency_agent(suffix)
    agent_id = agent["agent_id"]
    principal = f"acct:v{suffix}"
    paymethods.register(principal, "usdc-wallet", currency="USDC")
    monkeypatch.setattr(hub, "forward", lambda *a, **k: {
        "status": 200, "body": {"body": {"ok": True}}})
    result = invoke(agent_id, principal, "ocr-pro", currency="USDC")
    assert result.ok

    rows = conn().execute(
        "SELECT * FROM v_account_ledger WHERE ref_id=?", (result.settle["id"],)).fetchall()
    by_owner = {r["owner_id"]: r for r in rows}
    assert by_owner[principal]["direction"] == "out"
    assert by_owner[agent_id]["direction"] == "in"
    assert all(r["currency"] == "USDC" and r["amount_minor"] == 5000000 for r in rows)

    # 按币种过滤 + 主体视角
    mine = conn().execute(
        "SELECT COUNT(*) c FROM v_account_ledger WHERE owner_id=? AND currency='USDC'",
        (principal,)).fetchone()
    assert mine["c"] >= 1


# ---------------- 对等账户：条款币种 ----------------

def _peer_with_terms(owner: str, agent_id: str, currency: str | None = None,
                     unit_prices: dict | None = None) -> dict:
    acc = accounts.create(owner, "测试账户", currency=currency or "CNY",
                          medium="stablecoin" if currency else None)
    terms = {"unit_prices": unit_prices or {"call_count": 2}}
    if currency:
        terms["currency"] = currency
    return peers.propose(acc["account_id"], agent_id, terms=terms, auto_accept=True)


def test_deal_terms_freeze_currency_and_statement():
    """条款币种冻结：成交时 USDC，上报/对账/出账全程 USDC，账单给最小单位总额。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:d{suffix}", _card(f"d-{suffix}", accepts=["peer_account"]))
    owner = f"acct:dd{suffix}"
    link = _peer_with_terms(owner, agent["agent_id"], currency="USDC",
                            unit_prices={"call_count": 1000000})
    d = deals.open(link["link_id"], "ocr-pro")
    assert d["terms"]["currency"] == "USDC"
    deals.report(d["deal_id"], "provider", {"call_count": 3})
    deals.report(d["deal_id"], "requester", {"call_count": 3})
    rec = deals.reconcile(d["deal_id"])
    assert rec["recon"]["currency"] == "USDC"
    assert rec["recon"]["amount_minor"] == 3000000

    st = statements.issue(link["link_id"])
    assert st["currency"] == "USDC"
    assert st["total_minor"] == 3000000


def test_report_currency_mismatch_rejected():
    """上报币种与条款不符 → 拒绝：同一笔交易不能一方报美元一方报人民币。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:r{suffix}", _card(f"r-{suffix}", accepts=["peer_account"]))
    owner = f"acct:rr{suffix}"
    link = _peer_with_terms(owner, agent["agent_id"], currency="USDC",
                            unit_prices={"call_count": 1})
    d = deals.open(link["link_id"], "ocr-pro")
    with pytest.raises(ValueError, match="不符"):
        deals.report(d["deal_id"], "provider", {"call_count": 1}, currency="CNY")


def test_reconcile_across_currencies_rejected():
    """双方上报不同币种 → 对账不开认定：跨币种的\"一致\"没有意义。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:w{suffix}", _card(f"w-{suffix}", accepts=["peer_account"]))
    owner = f"acct:ww{suffix}"
    link = _peer_with_terms(owner, agent["agent_id"])
    d = deals.open(link["link_id"], "ocr-pro")
    # 绕过 report 的条款校验，直接构造两份不同币种的上报（模拟历史脏数据）
    ts = __import__("a2n_kernel", fromlist=["now_iso"]).now_iso()
    for party, cur in (("provider", "CNY"), ("requester", "USDC")):
        conn().execute(
            "INSERT INTO deal_reports (id, deal_id, party, dims, amount_fen, evidence,"
            " reported_at, currency, amount_minor) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"dr_{new_id('')}", d["deal_id"], party, "{}", 100, "{}", ts, cur, 100))
    # 双方都上报后状态机才会进 DELIVERED；手工插入不触发，这里如实补状态
    conn().execute("UPDATE deals SET state='DELIVERED' WHERE deal_id=?", (d["deal_id"],))
    conn().commit()
    with pytest.raises(ValueError, match="跨币种"):
        deals.reconcile(d["deal_id"])


def test_mixed_currency_statement_rejected():
    """混币不出单：把两种币加在一个 total 里不是汇总，是造假。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:mx{suffix}", _card(f"mx-{suffix}", accepts=["peer_account"]))
    owner = f"acct:mm{suffix}"
    link = _peer_with_terms(owner, agent["agent_id"])
    # 同一配对下做两笔币种不同的已对账交易（直接写 recon 模拟历史数据）
    for cur in ("CNY", "USDC"):
        d = deals.open(link["link_id"], "ocr-pro")
        ts = __import__("a2n_kernel", fromlist=["now_iso"]).now_iso()
        conn().execute(
            "INSERT INTO deal_recons (deal_id, amount_fen, delta_fen, matched, policy,"
            " created_at, currency, amount_minor, delta_minor) VALUES (?,?,?,?,?,?,?,?,?)",
            (d["deal_id"], 100, 0, 1, "test", ts, cur, 100, 0))
        conn().execute("UPDATE deals SET state='RECONCILED' WHERE deal_id=?", (d["deal_id"],))
    conn().commit()
    with pytest.raises(ValueError, match="混币"):
        statements.issue(link["link_id"])


# ---------------- 支付方式登记 ----------------

def test_paymethod_currency_binding():
    """支付方式登记绑币种：未注册币种拒绝，登记后可查。"""
    principal = f"acct:pm{new_id('')[2:]}"
    pm = paymethods.register(principal, "alipay", currency="CNY")
    assert pm["currency"] == "CNY" and pm["medium"] == "channel_pay"
    with pytest.raises(UnknownMedium):
        paymethods.register(principal, "ghost-pay", currency="GHOST")


def test_supported_currencies_of_v2_book():
    """v2 价目表的币种清单：agent 按币种分开标价，平台只读不猜。"""
    card = _card("z", price_book={"ocr-pro": {
        "CNY": {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]},
        "USDC": {"dimensions": [{"key": "call_count", "amount": 5, "per": 1},
                                 {"key": "output_tokens", "amount": 2, "per": 1000}]}}})
    assert supported_currencies(card, "ocr-pro") == ["CNY", "USDC"]
    # v1 老卡折算后仍是 CNY 单币
    assert supported_currencies(_card("old"), "ocr-pro") == ["CNY"]


def test_update_card_reprice():
    """供给方改价：card_hash 重算、下一单见新行情；已成交快照本就冻结不受影响。"""
    import json as _json
    from a2n_registry import registry
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:u{suffix}", _card(f"u-{suffix}", accepts=["peer_account"]))
    aid = agent["agent_id"]
    card = _card(f"u-{suffix}", accepts=["peer_account"])
    card["x-a2n"]["price_book"] = {"ocr-pro": {
        "USDC": {"dimensions": [{"key": "call_count", "amount": 7000000, "per": 1}]}}}
    updated = registry.update_card(aid, card)
    assert updated["card_hash"] != agent["card_hash"]        # 派单校验从此认新版
    assert supported_currencies(_json.loads(updated["card_json"]), "ocr-pro") == ["USDC"]
    with pytest.raises(ValueError, match="不存在"):
        registry.update_card("ag_none", card)
