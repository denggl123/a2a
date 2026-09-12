"""走查收口：绑定去重 + 价目事实单一化 + CLI 可读性（P1-2 / DX-14）。

P1-2 现场：新用户绑一次支付宝，账户页出现「已绑定(4)」四条一模一样。
      根因是 `paymethods.register` 直接 INSERT，无去重。
DX-14 现场：`a2n_sdk list` 吐原始 registry 行，`price_hint` 一处 null、
      一处（card 里）有值 —— 因为 agents 表有个 v2 起就不再写入的遗留列；
      价格还按最小单位原样吐（`50000 USDC/call_count`），人要心算。
"""
from __future__ import annotations

import json

from a2n_account import paymethods
from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_settlement import price_book
from a2n_store import conn


def _p(tag: str = "dedup") -> str:
    return f"acct:{tag}-{new_id('')[2:8]}"


# ---------- P1-2 绑定去重 ----------

def test_rebinding_same_channel_upserts_in_place():
    """同主体 + 同渠道 = 换绑：不新增行，只更新凭据，保留首次绑定时间。"""
    p = _p()
    a = paymethods.register(p, "alipay", "138****0001")
    b = paymethods.register(p, "alipay", "139****0002")
    assert a["pm_id"] == b["pm_id"], "同渠道换绑应复用同一条记录，不产生重复行"
    assert b["ref"] == "139****0002", "凭据应换成最新"
    assert b["created_at"] == a["created_at"]
    rows = paymethods.list_by_owner(p, only_active=False)
    assert len(rows) == 1, f"重复绑定不该产生多条记录：{len(rows)}"


def test_rebinding_converges_legacy_duplicates():
    """历史脏数据（绕过 API 直插的重复行）在下一次绑定时收敛为一条。"""
    p = _p("legacy")
    for i in range(3):
        conn().execute(
            "INSERT INTO payment_methods (pm_id, principal_id, method, channel, ref,"
            " status, created_at, medium, currency) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"pm_legacy{i}_{new_id('')[:6]}", p, "direct_pay", "alipay",
             f"old{i}", "ACTIVE", f"2026-01-0{i+1}T00:00:00", "channel_pay", "CNY"))
    conn().commit()
    assert len(paymethods.list_by_owner(p)) == 3

    paymethods.register(p, "alipay", "138****0009")     # 换绑即收敛
    actives = paymethods.list_by_owner(p)
    assert len(actives) == 1, "重复行应被收敛为一条（其余 CLOSED）"
    assert actives[0]["ref"] == "138****0009"
    closed = [r for r in paymethods.list_by_owner(p, only_active=False)
              if r["status"] == "CLOSED"]
    assert len(closed) == 2


def test_different_channels_stay_separate():
    p = _p("multi")
    paymethods.register(p, "alipay", "a")
    paymethods.register(p, "bank_card", "b")
    assert sorted(paymethods.channels(p)) == ["alipay", "bank_card"]


def test_rebind_switches_currency_and_medium():
    """换绑可以改币种：媒介随之切换（USDC 渠道不能还挂在 CNY 媒介上）。"""
    p = _p("cur")
    paymethods.register(p, "stablecoin", "0xabc", currency="USDC")
    pm = paymethods.list_by_owner(p)[0]
    assert pm["currency"] == "USDC" and pm["medium"] == "stablecoin"
    paymethods.register(p, "stablecoin", "0xdef", currency="USDC")
    pm2 = paymethods.list_by_owner(p)[0]
    assert pm2["ref"] == "0xdef" and pm2["currency"] == "USDC"


# ---------- DX-14 价目事实单一化 ----------

def test_agent_row_no_longer_exposes_dead_price_hint_column():
    """价格事实只在 card；恒为 null 的 price_hint 列不再出现在 registry 投影。"""
    skill = "ocr-" + new_id("")[2:8]
    card = {"name": "ph-" + skill, "url": None,
            "skills": [{"id": skill, "name": skill}],
            "x-a2n": {"price_hint": {skill: {"amount": 3, "unit": "fen_per_call"}}}}
    aid = registry.register(_p("ph"), card)["agent_id"]

    row = registry.get(aid)
    assert "price_hint" not in row, "遗留的死列不该出现在投影里（会制造第二个真相）"
    # 唯一的价目真相仍可取：从 card 派生（v1 price_hint 折成 v2 形状，按次/CNY）
    book = price_book(json.loads(row["card_json"]))
    assert book[skill]["CNY"][0]["amount"] == 3
    assert book[skill]["CNY"][0]["key"] == "call_count"

    listed = next(a for a in registry.list_all() if a["agent_id"] == aid)
    assert "price_hint" not in listed


# ---------- DX-14 CLI 精简表 ----------

def test_cli_slim_view_reads_price_from_card_only():
    """CLI 默认吐精简表：不夹带 card_json 整串，价格只从 card 派生。"""
    from a2n_sdk.__main__ import _price_of_card, _slim_agent

    card = {"name": "n", "skills": [{"id": "ocr"}], "accepts": ["direct_pay:x"],
            "x-a2n": {"price_book": {"ocr": {"CNY": {"dimensions": [
                {"key": "call_count", "amount": 3, "per": 1}]}}}}}
    assert _price_of_card(card)[0] == "0.03 CNY/call_count"

    slim = _slim_agent({"agent_id": "ag_1", "name": "n",
                        "card_json": json.dumps(card), "reputation": 0.5,
                        "compute": {"region": "cn-east-2"}, "status": "PROBATION"})
    assert "card_json" not in slim, "默认不该吐整串 card_json"
    assert "price_hint" not in slim, "也不该再有第二个价目列"
    assert slim["skills"] == ["ocr"]
    assert slim["price"] == "0.03 CNY/call_count"
    assert slim["accepts"] == ["direct_pay:x"]
    assert slim["region"] == "cn-east-2"


def test_cli_price_text_v1_hint_and_free():
    from a2n_sdk.__main__ import _price_of_card

    assert _price_of_card({"x-a2n": {"price_hint": {"ocr": {"amount": 3}}}})[0] == "0.03 CNY/call"
    assert _price_of_card({"x-a2n": {}})[0] == "免费"
    assert _price_of_card({})[0] == "免费"


def test_cli_price_is_readable_and_still_exposes_minor():
    """价格给两种口径：人读主单位、机器读最小单位 —— 别让人心算 10 的幂。"""
    from a2n_sdk.__main__ import _money, _slim_agent, _slim_found

    assert _money(3, "CNY") == "0.03 CNY"
    assert _money(50000, "USDC") == "0.05 USDC"
    assert _money(5, "JPY") == "5 JPY"          # 零位小数币种
    assert _money(0, "ZZZ") == "0 ZZZ"          # 不认识的币种按两位兜底

    book = {"ocr": {"USDC": {"dimensions": [{"key": "call_count", "amount": 50000}]}}}
    found = _slim_found({"agent_id": "ag_x", "name": "n", "price_book": book})
    assert found["price"] == "0.05 USDC/call_count"
    assert found["price_minor"] == 50000, "机器要的原始值也得在，否则得自己乘回来"

    card = {"skills": [{"id": "ocr"}], "x-a2n": {"price_book": book}}
    slim = _slim_agent({"agent_id": "ag_x", "name": "n",
                        "card_json": json.dumps(card), "reputation": 0.5})
    assert slim["price"] == "0.05 USDC/call_count"
    assert slim["price_minor"] == 50000 and slim["currency"] == "USDC"

    free = _slim_agent({"agent_id": "ag_f", "name": "n",
                        "card_json": json.dumps({"skills": [{"id": "ocr"}]})})
    assert free["price"] == "免费" and free["price_minor"] is None


# ---------- 金额展示口径（三层同一条规则） ----------

def test_money_display_drops_meaningless_zeros():
    """与控制台 / CLI 同口径：链上币种去掉尾零，本位币保底两位小数。

    现场的毛病是流水里显示 `0.050000 USDC`（读起来像噪声），
    而 `¥0` / `¥3` 又会看着像没填完 —— 两头都得管。
    """
    from a2n_custodian.media import money

    assert money(50000, "stablecoin")["display"] == "0.05 USDC"
    assert money(5000000, "stablecoin")["display"] == "5 USDC"
    assert money(0, "stablecoin")["display"] == "0 USDC"
    assert money(3, "channel_pay")["display"] == "0.03 CNY"
    assert money(300, "channel_pay")["display"] == "3.00 CNY"
    assert money(0, "channel_pay")["display"] == "0.00 CNY"
