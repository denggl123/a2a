"""结算账户绑定引导：分渠道引导 schema + 校验 + 绑定后自动结算。

产品语义（用户诉求）：选择绑定账户 → 支付宝有支付宝引导页、安全币有安全币
引导页 → 绑定了就自动结算。底层原则不变：A2N 只记录绑定声明，不碰钱不验真伪；
没有引导的渠道照样能绑（渠道是数据，协议不设限）。
"""
from __future__ import annotations

import json

import pytest

from a2n_account import all_guides, guide_of, masked_ref, ref_of, validate_binding
from a2n_registry import registry
from a2n_kernel.hashing import new_id
from a2n_server.routers.payments import PayMethodIn, channels, list_mine, register


def _principal() -> str:
    return "acct:bind-" + new_id("")[2:8]


# ---------- 引导注册表 ----------

def test_builtin_guides_present():
    chans = {g["channel"] for g in all_guides()}
    assert {"alipay", "wechat_pay", "bank_card", "stablecoin", "x402_wallet"} <= chans


def test_guide_has_fields_and_steps():
    g = guide_of("alipay")
    assert g["currency"] == "CNY"
    keys = [f["key"] for f in g["fields"]]
    assert "account" in keys and "holder" in keys
    assert g["steps"]                                    # 支付宝引导页有步骤


def test_stablecoin_guide_is_security_coin():
    """安全币引导：钱包地址 + 网络 + 资产，USDC 结算。"""
    g = guide_of("stablecoin")
    assert g["currency"] == "USDC"
    keys = [f["key"] for f in g["fields"]]
    assert keys == ["wallet", "network", "asset"]
    net = [f for f in g["fields"] if f["key"] == "network"][0]
    assert "a2n-settlement" in net["options"]


def test_guide_registration_is_extension_point():
    """新渠道 = register_guide 一条，不改任何分发逻辑。"""
    from a2n_account import register_guide
    register_guide({"channel": "demo_bank", "label": "演示银行", "currency": "CNY",
                    "fields": [{"key": "acct", "label": "账号", "required": True}]})
    assert guide_of("demo_bank")["label"] == "演示银行"


# ---------- 绑定校验 ----------

def test_binding_requires_mandatory_fields():
    with pytest.raises(ValueError, match="缺少必填项"):
        validate_binding("alipay", {})                   # 什么都没给
    with pytest.raises(ValueError, match="缺少必填项"):
        validate_binding("alipay", {"account": "13800000000"})   # 缺实名


def test_binding_validates_patterns_and_options():
    with pytest.raises(ValueError, match="格式不对"):
        validate_binding("alipay", {"account": "not-a-phone", "holder": "张三"})
    with pytest.raises(ValueError, match="只能是"):
        validate_binding("stablecoin", {"wallet": "0xabc123",
                                        "network": "solana", "asset": "USDC"})


def test_binding_unknown_channel_passes_through():
    """渠道是数据：无引导的渠道原样通过，协议不设限。"""
    out = validate_binding("mystery_channel", {"whatever": "x", "blank": "  "})
    assert out == {"whatever": "x"}


def test_ref_of_single_vs_multi():
    assert ref_of({"account": "13800000000"}) == "13800000000"
    assert json.loads(ref_of({"account": "a", "holder": "b"})) == {"account": "a", "holder": "b"}


def test_masked_ref_hides_sensitive():
    m = masked_ref(json.dumps({"account": "13800000000", "holder": "张三"}, ensure_ascii=False))
    d = json.loads(m)
    assert d["account"].startswith("138") and "13800000000" not in m
    assert d["holder"] == "张三"                          # 非敏感字段不掩码
    assert masked_ref("13800000000").startswith("138")


# ---------- 路由层：绑定 → 自动结算 ----------

def test_channels_endpoint():
    out = channels()
    assert any(c["channel"] == "alipay" for c in out["channels"])
    assert "自动结算" in out["note"]


def test_register_with_fields_then_auto_settle():
    """按引导绑定（fields）→ ref 规范化存储 → 响应声明自动结算。"""
    p = _principal()
    pm = register(PayMethodIn(channel="alipay",
                              fields={"account": "13800000000", "holder": "张三"}),
                  principal=p)
    assert pm["status"] == "ACTIVE"
    assert json.loads(pm["ref"]) == {"account": "13800000000", "holder": "张三"}
    assert pm["masked_ref"] != pm["ref"] and "13800000000" not in pm["masked_ref"]
    assert pm["auto_settle"]["enabled"] is True
    assert pm["currency"] == "CNY"                       # 缺省取引导默认币种


def test_register_rejects_bad_binding_with_clear_message():
    p = _principal()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        register(PayMethodIn(channel="stablecoin", fields={"wallet": ""}), principal=p)
    assert ei.value.status_code == 400 and "缺少必填项" in ei.value.detail


def test_register_legacy_ref_still_accepted():
    """老式单凭据（冒烟脚本/SDK 既有用法）原样兼容。"""
    p = _principal()
    pm = register(PayMethodIn(channel="alipay", ref="138****0001"), principal=p)
    assert pm["ref"] == "138****0001" and pm["status"] == "ACTIVE"


def test_bound_channel_auto_settles():
    """绑定了就自动结算：绑定（多字段引导）→ compatible 出现 direct_pay:alipay。"""
    from a2n_server.routers.payments import compatible
    p = _principal()
    card = {"name": "paid", "url": None, "accepts": ["peer_account", "direct_pay:alipay"],
            "skills": [{"id": "s" + new_id("")[2:8], "name": "s"}]}
    ag = registry.register(p, card)["agent_id"]
    register(PayMethodIn(channel="alipay",
                         fields={"account": "13911112222", "holder": "李四"}),
             principal=p)
    out = compatible(agent_id=ag, principal=p)
    assert "direct_pay:alipay" in out["can_call_with"]
    assert out["missing"] == ["peer_account"] or "peer_account" in out["missing"]


def test_list_masks_ref():
    p = _principal()
    register(PayMethodIn(channel="alipay",
                         fields={"account": "13800000000", "holder": "王五"}),
             principal=p)
    rows = list_mine(channel=None, principal=p)
    assert rows and all("13800000000" not in json.dumps(r["masked_ref"]) for r in rows)
