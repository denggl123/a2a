"""绿灯 agent（`a2n_sdk.greenlight`）与本机节点行业专家档的守卫测试。

三件事最容易一起坏：

* **交付物形状**：每份都要报出 deliverable/delivered，输入不足要如实报错；
* **模板对齐**：本机节点声明的验收模板必须与真实交付**逐字段一致** ——
  多一个或少一个都会被记成结构偏差，"已开业"档就会莫名掉分
  （`a2n_acceptance.template` 把"多出的字段"也算不符）；
* **单一事实源**：本机节点与容器必须从同一张 SPECS 表取 handler，
  两处各写一份迟早对不上。
"""
import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

from a2n_acceptance.template import deviation
from a2n_sdk import greenlight

SAMPLE = {
    "video-short": "新品咖啡机\n一键出咖啡",
    "video-script": "演示主题\n要点一",
    "finance-report": "销售回款 120000\n采购支出 -70000",
    "legal-contract": "标的 软件定制开发",
    "game-design": "塔防",
    "ecom-listing": "便携咖啡机\n冷萃三分钟",
    "resume-polish": "后端工程师\n做过支付网关",
    "trip-plan": "杭州 2天\n西湖\n灵隐寺",
    "course-outline": "Python入门\n会写脚本",
    "menu-pricing": "招牌牛肉面 8.5\n蛋炒饭 6",
}


@pytest.mark.parametrize("spec", greenlight.SPECS, ids=[s.slug for s in greenlight.SPECS])
def test_every_spec_is_deterministic_and_declares_what_it_delivers(spec):
    assert set(SAMPLE) == {s.slug for s in greenlight.SPECS}, "SAMPLE 漏了某份绿灯 agent"
    first = spec.handler(SAMPLE[spec.slug])
    assert first == spec.handler(SAMPLE[spec.slug]), "同样输入给出了不同输出"
    assert first["deliverable"] == spec.name, "交付物名与展示名对不上"
    assert first["delivered"], "没列出交付了哪几件"


@pytest.mark.parametrize("spec", greenlight.SPECS, ids=[s.slug for s in greenlight.SPECS])
def test_every_spec_refuses_empty_input_instead_of_pretending(spec):
    with pytest.raises(ValueError):
        spec.handler("   ")


# 本机节点（一个身份签四张卡）。用真脚本而不是另写一份夹具 —— 测的就是那个脚本。
spec_local = importlib.util.spec_from_file_location(
    "local_node_under_test", Path(__file__).resolve().parents[1] / "scripts/run_local_node.py")
local = importlib.util.module_from_spec(spec_local)
spec_local.loader.exec_module(local)


def _built_all():
    from a2n_p2p import Identity
    return local.build_all(Identity.generate(), local.ROLES)


def test_local_node_templates_match_the_real_delivery():
    """已开业档偏差必须为 0；草稿档偏差必须非 0（声明了却没交付，硬指标要说出来）。"""
    for built in _built_all():
        skill = next(iter(built["handlers"]))
        out = built["handlers"][skill](SAMPLE[built["slug"]])
        template = built["card"]["x-a2n"].get("acceptance_template")
        if built["role"] == "free":
            assert template is None, "免费档不声明模板（未声明就如实说，别伪造 0 偏差）"
            continue
        d = deviation(template, out)
        if built["role"] == "trial":
            assert d["D"] > 0, "草稿模板声明了 ingredient_breakdown，偏差不该是 0"
            assert any("缺必需字段" in r for r in d["reasons"])
        else:
            assert d["D"] == 0 and d["quality"] == 100.0, (built["role"], d["reasons"])


def test_local_node_cards_are_industry_experts_not_ocr():
    banned = ("专业版", "免费版", "公益版", "极速版", "入门版", "试用版")
    for built in _built_all():
        card = built["card"]
        assert "OCR" not in card["name"], f"{built['role']} 档还在摆 OCR"
        skill = card["skills"][0]["id"]
        assert not skill.startswith("ocr"), f"{built['role']} 档技能还是 {skill}"
        for word in banned:
            assert word not in card["name"], (built["role"], card["name"])
        assert "非模型推理" in card["description"]
        assert "永久" not in card["description"]


def test_local_node_and_market_pull_from_the_same_specs():
    """本机节点与容器的 handler 都来自 greenlight —— 分叉两份就迟早对不上。"""
    spec_market = importlib.util.spec_from_file_location(
        "market_under_test",
        Path(__file__).resolve().parents[1] / "scripts/run_market_demo_agents.py")
    market = importlib.util.module_from_spec(spec_market)
    spec_market.loader.exec_module(market)

    all_handlers = {greenlight.BY_SLUG[s].handler for s in greenlight.BY_SLUG}
    built = _built_all()
    local_handlers = {h for b in built for h in b["handlers"].values()}
    market_handlers = {p[7] for p in market.MARKET}
    assert local_handlers <= all_handlers
    assert market_handlers <= all_handlers
    # 且两边挑的技能不重叠（同一份成品在两个节点重复上架，发现页会出现两张"同价"卡）
    assert not ({p[2] for p in market.MARKET}
                & {b["card"]["skills"][0]["id"] for b in built})


def test_serve_http_returns_the_real_deliverable():
    server, endpoint = greenlight.serve_http(["game-design"])
    try:
        body = json.dumps({"skill": "game-design", "payload": "塔防"}).encode()
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            out = json.load(resp)
        assert out["deliverable"] == "游戏策划案" and len(out["levels"]) == 5

        # 参数错就如实说参数错（400），不是把它糊成"上游坏了"
        bad = urllib.request.Request(endpoint, data=json.dumps(
            {"skill": "game-design", "payload": "   "}).encode(), method="POST")
        try:
            urllib.request.urlopen(bad, timeout=5)
            raise AssertionError("空输入居然通过了")
        except urllib.error.HTTPError as e:
            assert e.code == 400

        # 没有的技能如实 404，并说清这台上游会什么
        miss = urllib.request.Request(endpoint, data=json.dumps(
            {"skill": "nope", "payload": "x"}).encode(), method="POST")
        try:
            urllib.request.urlopen(miss, timeout=5)
            raise AssertionError("不认识的技能居然通过了")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
