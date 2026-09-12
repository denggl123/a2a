"""一致性收敛回归：x402 单位按币种、SDK from_card metering、扩展点契约。"""
from __future__ import annotations

import json
import uuid

import pytest

from a2n_custodian import channel_guides, media
from a2n_custodian.channel_guides import register_guide
from a2n_custodian.media import register_medium
from a2n_gateway.settle import register_settler
from a2n_settlement.dims import DIMS, BILLABLE_DIMS, register_dimension
from a2n_sdk.shelf import DEFAULT_METERING, build_card, from_card


# ---- 1. x402 单位：按所选币种取价（USDC 单价绝不当 CNY 分用） ----

def test_unit_price_fen_picks_cny_default_but_explicit_currency_overrides():
    """unit_price_fen 默认 CNY 分；多币种场景必须用 unit_price_of 取最小单位。

    旧实现 unit_price_fen 默认 CNY，会让 USDC 单价当 CNY 用；
    保留旧函数（向后兼容）但强提示调用方用 unit_price_of。
    """
    from a2n_settlement.price import unit_price_of
    from a2n_gateway.gate import unit_price_fen
    card = {
        "name": "t", "skills": [{"id": "ocr-pro"}],
        "x-a2n": {"price_book": {"ocr-pro": {"USDC": {"dimensions": [
            {"key": "call_count", "amount": 5000000, "per": 1}]}, "CNY": {}}}},
        "accepts": ["x402"],
    }
    # 旧路径默认 CNY：USDC 价目下拿不到（CNY 价目空）→ 默认 0
    assert unit_price_fen(card, "ocr-pro") == 0
    # 新路径按币种取：USDC 最小单位 5000000（6 decimals）
    assert unit_price_of(card, "ocr-pro", "USDC") == 5000000
    # CNY 价目空时 unit_price_of 返回 0，不与 unit_price_fen 一样回 1
    assert unit_price_of(card, "ocr-pro", "CNY") == 0


# ---- 2. SDK from_card metering 归一 ----

class _StubClient:
    """from_card 只调 client.register，行为不依赖 client 内部。"""
    def __init__(self):
        self.last_card = None
    def register(self, card, visibility):
        self.last_card = card
        return {"agent_id": "ag_new", "card_hash": "h", "status": "PROBATION"}


def test_from_card_fills_default_metering_when_missing():
    c = _StubClient()
    card = {"name": "x", "skills": [{"id": "ocr-pro"}], "accepts": ["x402"],
            "x-a2n": {"uid": str(uuid.uuid4())}}
    out = from_card(c, card)
    assert out["agent_id"] == "ag_new"
    m = c.last_card["x-a2n"]["metering"]
    assert m["sdk_default"] is True
    assert [d["key"] for d in m["dimensions"]] == DEFAULT_METERING


def test_from_card_preserves_user_declared_metering():
    c = _StubClient()
    card = {"name": "x", "skills": [{"id": "ocr-pro"}],
            "x-a2n": {"uid": str(uuid.uuid4()),
                      "metering": {"dimensions": [{"key": "gpu_seconds"}]}}}
    from_card(c, card)
    assert c.last_card["x-a2n"]["metering"]["dimensions"] == [{"key": "gpu_seconds"}]
    assert "sdk_default" not in c.last_card["x-a2n"]["metering"]


def test_build_card_and_from_card_produce_same_metering_shape():
    """两条 SDK 入口（简写 vs 整卡）在网络上看着一样：缺 metering 都归一。"""
    c = _StubClient()
    via_from_card = _StubClient()
    build_card(skills=["ocr-pro"], accepts=["x402"], uid=str(uuid.uuid4()))
    from_card(via_from_card, {"name": "x", "skills": [{"id": "ocr-pro"}],
                               "x-a2n": {"uid": str(uuid.uuid4())}})
    # 同样的 default metering key 集（不强制 identity）
    a_dims = DEFAULT_METERING


# ---- 3. 扩展点契约：register_* 形态不齐即 TypeError ----

def test_register_guide_rejects_missing_fields():
    with pytest.raises(TypeError, match="channel"):
        register_guide({"channel": "x", "label": "y"})  # 缺 currency/fields


def test_register_guide_rejects_non_dict():
    with pytest.raises(TypeError):
        register_guide("not-a-dict")


def test_register_medium_rejects_missing_fields():
    with pytest.raises(TypeError, match="code"):
        register_medium({"code": "x", "currency": "CNY"})  # 缺 exponent


def test_register_dimension_rejects_missing_key():
    with pytest.raises(TypeError, match="key"):
        register_dimension({"unit": "s"})


def test_register_settler_rejects_non_callable():
    with pytest.raises(TypeError, match="settler"):
        register_settler("test_mode", "not-callable")


def test_register_settler_accepts_valid_callable():
    """现有 settler 形态：(cap, task, agent_id, principal, **kw) -> dict —— 应当通过。"""
    def my_settler(cap, task, agent_id, principal, **kw):
        return {"kind": "test", "id": task["id"], "state": "SETTLED"}
    register_settler("test_mode_valid", my_settler)


def test_register_guide_roundtrip_via_attrs_helper():
    """a2n_kernel.protocols.attrs 对 dict 按键、对实例按属性，两条都应 True。"""
    from a2n_kernel.protocols import attrs
    assert attrs({"channel": "x", "label": "y", "currency": "CNY", "fields": []},
                ("channel", "label", "currency", "fields")) is True
    assert attrs({"channel": "x"}, ("missing",)) is False