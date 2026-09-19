"""市场演示档：挂牌价必须真的是两条独立挂牌，免费档必须不带价目表。

配套 tests/test_free_demo_agents.py：那边管"自愿免费"的夹具，这边管"可售卖"的。
两者最容易一起坏的地方是**措辞**（对外文案不许承诺"永久"）与**精度**
（最小单位换算的事实源只能有持牌层那一处）。
"""
import importlib.util
from pathlib import Path

import pytest

from a2n_custodian.media import by_currency
from a2n_p2p import Identity
from a2n_p2p.attest import verify_selfproof
from a2n_settlement import is_free, price_book

spec = importlib.util.spec_from_file_location(
    "market_demo_agents", Path(__file__).resolve().parents[1] / "scripts/run_market_demo_agents.py")
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)


def test_own_utilities_actually_work():
    assert market.minify_json('{ "a" : 1 }')["text"] == '{"a":1}'


def test_minify_rejects_bad_json_instead_of_pretending():
    with pytest.raises(ValueError):
        market.minify_json("not json")


@pytest.mark.parametrize("currency,major,minor", [
    ("CNY", "0.01", 1),
    ("CNY", "0.015", 2),      # 1.5 分 → 四舍五入到 2 分
    ("USDC", "0.0015", 1500),  # 6 位小数
    ("USDC", "0.003", 3000),
])
def test_minor_uses_the_licence_layer_exponent(currency, major, minor):
    assert market._minor(major, currency) == minor
    assert market._minor(major, currency) == round(
        float(major) * (10 ** int(by_currency(currency)[0]["exponent"])))


def test_unknown_currency_is_refused_not_guessed():
    with pytest.raises(ValueError):
        market._minor("1", "XYZ")


@pytest.mark.parametrize("profile", market.MARKET, ids=[p[0] for p in market.MARKET])
def test_cards_signed_and_stable(profile):
    identity = Identity.generate()
    card = market.build_card(profile, identity)
    assert verify_selfproof(card)[0]
    assert card == market.build_card(profile, identity)
    # 卡片描述是买家可见的对外文案：说"不收费"（事实）可以，说"永久"（承诺）不行。
    assert "永久" not in card["description"], card["description"]
    assert "非模型推理" in card["description"]


@pytest.mark.parametrize("profile", [p for p in market.MARKET if p[4]],
                         ids=[p[0] for p in market.MARKET if p[4]])
def test_paid_offers_declare_two_independent_listings(profile):
    card = market.build_card(profile, Identity.generate())
    skill = profile[2]
    assert not is_free(card, skill)
    # 两个币种是两条独立挂牌：都按**自己的**最小单位存，不是一个价换算两遍
    book = price_book(card)[skill]
    assert sorted(book) == sorted(profile[4])
    for cur, major in profile[4].items():
        amount = book[cur][0]["amount"]
        assert amount == market._minor(major, cur)
    # 收费档必须有能付的钱路，否则演示里没人下得了单
    assert set(card.get("accepts") or []) == set(market.PAID_ACCEPTS)


@pytest.mark.parametrize("profile", [p for p in market.MARKET if not p[4]],
                         ids=[p[0] for p in market.MARKET if not p[4]])
def test_free_offers_carry_no_price_book_and_say_so(profile):
    card = market.build_card(profile, Identity.generate())
    assert is_free(card, profile[2])
    assert "price_book" not in card["x-a2n"]
    assert "不收费" in card["description"]
    assert "accepts" not in card


def test_uid_is_derived_from_the_key_so_restart_reuses_the_listing():
    a = Identity.generate()
    first = {p[0]: market.build_card(p, a)["x-a2n"]["uid"] for p in market.MARKET}
    # 同一把钥匙 → 同一批 uid ⇒ 重启走 update 分支，不会像随机 uid 那样多注册一条
    assert first == {p[0]: market.build_card(p, a)["x-a2n"]["uid"] for p in market.MARKET}
    assert len(set(first.values())) == len(market.MARKET)      # 档与档之间不撞
    # 换了钥匙才是"新上架"：uid 必须跟着变
    other = {market.build_card(p, Identity.generate())["x-a2n"]["uid"] for p in market.MARKET}
    assert not (set(first.values()) & other)


def test_market_covers_more_than_one_category():
    """只有 OCR 的话，发现页新增的分类筛选就没有真身可看。"""
    assert len({p[2] for p in market.MARKET}) >= 4
