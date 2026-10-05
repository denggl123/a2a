import pytest
import uuid
from a2n_sdk.cards import build_card, auto_desc, validate_card
from a2n_sdk.pricing import quote
from a2n_sdk.dimensions import DIMS, register_dimension, is_billable
from a2n_acceptance import BaselineSamplePolicy, set_policy
ValidationError = ValueError

def test_build_card_full_structure():
    card = build_card(
        skills=["ocr-pro", {"id": "tts", "name": "语音合成", "tags": ["audio"]}],
        name="my-ocr", deployment={"region": "cn-east-2"},
        accepts=["peer_account", "direct_pay:alipay", "x402"],
        metering=["call_count", "output_tokens"],
        price={"ocr-pro": {"CNY": 3, "USDC": {"dimensions": [{"key": "call_count",
                                                              "amount": 5000000, "per": 1}]}}})
    assert card["name"] == "my-ocr"
    assert uuid.UUID(card["x-a2n"]["uid"])          # 合法 UUID
    assert card["accepts"] == ["peer_account", "direct_pay:alipay", "x402"]
    assert [s["id"] for s in card["skills"]] == ["ocr-pro", "tts"]
    assert card["skills"][1]["inputModes"] == ["application/json"]
    # 简写价展开为 v2 每次（call_count）单价；v2 完整写法原样透传
    pb = card["x-a2n"]["price_book"]["ocr-pro"]
    assert pb["CNY"] == {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]}
    assert pb["USDC"]["dimensions"][0]["amount"] == 5000000
    # 计量维度：可计费的才进账单
    dims = card["x-a2n"]["metering"]["dimensions"]
    assert [d["key"] for d in dims] == ["call_count", "output_tokens"]
    # 描述未填 → 自动生成且提到技能名与部署属地（不写硬件细节，能力是黑盒）
    assert "OCR 识别" in card["description"] or "ocr-pro" in card["description"]
    assert "cn-east-2" in card["description"]
    assert card["x-a2n"]["deployment"] == {"region": "cn-east-2"}

def test_auto_desc_matches_console_logic():
    d = auto_desc([{"id": "ocr-pro", "name": "OCR 识别"}], {"region": "cn-east-2"})
    assert d.startswith("提供OCR 识别（ocr-pro）服务 · 部署于 cn-east-2")
    assert "4090" not in d

def _card(skill: str, **ext_extra) -> dict:
    return {"name": "t-" + skill, "url": "http://localhost:9000/a2a",
            "skills": [{"id": skill, "tags": []}], "x-a2n": {**ext_extra}}

def test_acceptance_policy_replaceable():
    class AlwaysPass:
        key = "test.always_pass"
        version = "0"

        def judge(self, task, usage, result_hash, started_at):
            return {"passed": True, "score": 1.0, "reasons": [], "policy": self.key}

    old = BaselineSamplePolicy()
    set_policy(AlwaysPass())
    from a2n_acceptance import judge
    try:
        out = judge({"sla": "{}"}, {"wall_time_ms": 1}, "hash", None)
        assert out["passed"] is True and out["policy"] == "test.always_pass"
    finally:
        set_policy(old)                    # 恢复默认，不污染其他测试
    assert judge({"sla": "{}"}, None, None, None)["passed"] is False

def test_validate_card_accepts_minimal():
    validate_card(_card("s"))              # 不抛即过

def test_validate_card_url_optional_for_relay_nodes():
    """relay/pull 节点没有自有公网入口：url 可空，对外走平台门牌号。"""
    validate_card({"name": "relay-node", "skills": [{"id": "s"}]})          # 缺 url 键
    validate_card({"name": "relay-node", "url": None, "skills": [{"id": "s"}]})
    validate_card({"name": "relay-node", "url": "", "skills": [{"id": "s"}]})

def test_validate_card_rejects_bad_shapes():
    with pytest.raises(ValidationError, match="缺少必填字段"):
        validate_card({"name": "x"})
    with pytest.raises(ValidationError, match="url"):
        validate_card({"name": "x", "url": "ftp://x", "skills": [{"id": "s"}]})
    with pytest.raises(ValidationError, match="非空数组"):
        validate_card({"name": "x", "url": "http://a", "skills": []})
    with pytest.raises(ValidationError, match="UUID"):
        validate_card(_card("s", uid="not-a-uuid"))
    with pytest.raises(ValidationError, match="amount"):
        validate_card(_card("s", price_book={"s": {"CNY": {"dimensions": [
            {"key": "call_count", "amount": -1, "per": 1}]}}}))
    with pytest.raises(ValidationError, match="per"):
        validate_card(_card("s", price_book={"s": {"CNY": {"dimensions": [
            {"key": "call_count", "amount": 3, "per": 0}]}}}))

def test_dimension_registry_extends_billable():
    """新计量维度注册即可计费 —— 结算代码不认识具体维度，只问注册表。"""
    register_dimension({"key": "render_seconds", "verifiable": True, "billable": True,
                        "label": "渲染秒数"})
    assert "render_seconds" in DIMS
    from a2n_sdk.dimensions import is_billable
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
