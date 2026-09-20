"""市场演示档：**成品交付物**必须真的拼得出来，挂牌价必须真的是两条独立挂牌。

配套 tests/test_free_demo_agents.py：那边管"自愿免费"的夹具，这边管"可售卖"的。

三件最容易一起坏的事：

* **交付物形状**：既然卖的是成品工作流（VISION 第一承重墙），每份成品都要报出
  "交付了哪几件"，且输入不够时**如实说给不了**，不许编；
* **措辞**：对外文案不许承诺"永久"（说"不收费"是事实，说"永久"是承诺）；
* **精度**：最小单位换算的事实源只能有持牌层那一处，且不许四舍五入。
"""
import importlib.util
import re
from decimal import Decimal
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

# 本机节点（一个身份签四张卡）。用真脚本而不是另写一份夹具 —— 测的就是那个脚本。
spec_local = importlib.util.spec_from_file_location(
    "local_node", Path(__file__).resolve().parents[1] / "scripts/run_local_node.py")
local = importlib.util.module_from_spec(spec_local)
spec_local.loader.exec_module(local)


# --------------------------------------------------------------- 成品交付物
# 每个 handler 都是一份"能交出去的东西"。测的是**成品的形状与诚实度**：
# 该交付的件齐不齐、算得对不对、给不了的时候有没有如实说给不了。


def test_video_short_delivers_a_shot_list():
    # 第一行是主题（进封面与标题），其余每行一镜
    out = market.video_short("新品咖啡机\n一键出咖啡\n三分钟清洗")
    assert out["deliverable"] == "短视频成片包"
    assert [s["sec"] for s in out["shots"]] == ["0-5", "5-10"]
    assert out["duration_sec"] == 10
    assert out["aspect"] == "9:16"
    assert "分镜表" in out["delivered"] and "封面文案" in out["delivered"]
    # 只给主题也不许空手：成片包至少有一镜
    assert len(market.video_short("只有主题")["shots"]) == 1


def test_video_script_is_a_ready_to_read_script():
    out = market.video_script("演示主题\n要点一\n要点二")
    assert out["deliverable"] == "短视频口播稿"
    # 字数统计必须与正文一致，不能是另算的一个数
    assert out["word_count"] == sum(len(s["line"]) for s in out["script"])
    assert [s["no"] for s in out["script"]] == list(range(1, len(out["script"]) + 1))


def test_finance_report_sums_and_states_its_basis():
    out = market.finance_report("销售回款 120000\n采购支出 -70000")
    assert out["income"] == "120000"
    assert out["cost"] == "70000"
    assert out["profit"] == "50000"
    assert out["margin_pct"] == "41.7"
    assert "只做加总" in out["basis"], "口径必须写出来，否则那三个数没法被复核"


def test_finance_report_gives_no_margin_when_there_is_no_income():
    """收入为零时不给利润率 —— 给不了就说给不了，别编个 0% 或 100% 出来。"""
    out = market.finance_report("退款 -100")
    assert out["margin_pct"] is None
    assert out["profit"] == "-100"


def test_finance_report_refuses_a_line_it_cannot_read():
    with pytest.raises(ValueError, match="科目 金额"):
        market.finance_report("销售回款一百二十万")


def test_legal_contract_marks_missing_clauses_instead_of_inventing_them():
    out = market.legal_contract("标的 软件定制开发")
    assert out["deliverable"] == "合同草案"
    assert out["clauses"][0]["source"] == "供给方输入的要点"
    assert out["clauses"][0]["body"] == "标的 软件定制开发"
    # 没提到的一律"待补充"，绝不代拟条款 —— 空白会被读成"已谈妥"
    assert out["unfilled"] == len(out["clauses"]) - 1
    assert out["clauses"][1]["source"] == "未提供"
    assert "待补充" in out["clauses"][1]["body"]
    assert "法律意见" in out["note"], "必须说清这是草案、不是法律意见"


def test_game_design_delivers_loop_levels_and_admits_numbers_are_not_balance():
    out = market.game_design("卡牌对战")
    assert out["deliverable"] == "游戏策划案"
    assert len(out["levels"]) == 5
    assert len(out["core_loop"]) >= 4
    assert "真平衡要实测迭代" in out["balance"]["note"], "初值不许说成结论"


def test_ecom_listing_leaves_spec_and_after_sale_as_placeholders():
    out = market.ecom_listing("便携咖啡机\n冷萃只要三分钟")
    assert out["deliverable"] == "商品详情页"
    assert "便携咖啡机" in out["title"]
    blocks = {b["type"]: b for b in out["blocks"]}
    assert "占位" in blocks["规格表"]["rows"][0]["value"]
    assert "不代填" in blocks["售后"]["text"], "售后承诺不许替商家编"


HANDLERS = {
    market.video_short: "主题\n卖点一",
    market.video_script: "主题\n要点一",
    market.finance_report: "回款 100\n支出 -40",
    market.legal_contract: "标的 站点开发",
    market.game_design: "塔防",
    market.ecom_listing: "商品\n卖点",
}


@pytest.mark.parametrize("handler", list(HANDLERS), ids=[h.__name__ for h in HANDLERS])
def test_handler_is_deterministic_and_declares_what_it_delivers(handler):
    """本地确定性测试服务：同样输入必得同样输出；且必须报出"交付了哪几件"。"""
    sample = HANDLERS[handler]
    first = handler(sample)
    assert first == handler(sample), "同样输入给出了不同输出"
    assert first["deliverable"], "没写清交付的是什么成品"
    assert first["delivered"], "没列出交付了哪几件"


@pytest.mark.parametrize("handler", list(HANDLERS), ids=[h.__name__ for h in HANDLERS])
def test_handler_refuses_empty_input_instead_of_pretending(handler):
    with pytest.raises(ValueError):
        handler("   ")


@pytest.mark.parametrize("currency,major,minor", [
    ("CNY", "0.01", 1),        # 分
    ("CNY", "0.02", 2),
    ("USDC", "0.0015", 1500),  # 6 位小数
    ("USDC", "0.003", 3000),
])
def test_minor_uses_the_licence_layer_exponent(currency, major, minor):
    assert market._minor(major, currency) == minor
    assert market._minor(major, currency) == int(
        Decimal(major) * (10 ** int(by_currency(currency)[0]["exponent"])))


@pytest.mark.parametrize("currency,major", [
    ("CNY", "0.015"),   # 1.5 分 —— CNY 的最小单位是分，这个价不存在
    ("CNY", "0.001"),
    ("USDC", "0.0000001"),  # 比 10^-6 还细
])
def test_price_that_cannot_be_expressed_is_refused_not_rounded(currency, major):
    """**绝不四舍五入**：悄悄把 0.015 变成 0.02 等于背着供给方改了价。"""
    with pytest.raises(ValueError, match="整数倍"):
        market._minor(major, currency)


def test_every_listed_price_is_actually_expressed_in_its_currency():
    """货架上每一档的挂牌价都必须过 `_minor`：别让演示档自己踩精度坑。"""
    for profile in market.MARKET:
        for cur, major in (profile[4] or {}).items():
            assert market._minor(major, cur) > 0, (profile[0], cur, major)


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


def test_market_shelf_is_industry_experts_not_tool_utilities():
    """货架要摆"能交成品的行业专家"，不是"一个函数"。

    每个技能只出现一次（同一个技能摆多档不算覆盖多行业），且至少覆盖 4 个行业 ——
    只覆盖一两类的话，发现页的分类筛选就没东西可筛。
    """
    skills = {p[2] for p in market.MARKET}
    assert len(skills) == len(market.MARKET), "同一个技能重复摆多档"
    assert len(skills) >= 4


@pytest.mark.parametrize("profile", market.MARKET, ids=[p[0] for p in market.MARKET])
def test_every_card_says_what_it_delivers(profile):
    """卡面是买家可见的对外文案，必须点名它**真的交付的那件成品**。

    断言的是"卡面里出现了 handler 实际产出的成品名"，而不是"含『交付』二字" ——
    后者会被「交付与验收」这种词意外满足，等于没测。
    """
    handler = profile[7]
    deliverable = handler(HANDLERS[handler])["deliverable"]
    assert deliverable in profile[5], \
        f"{profile[0]} 的卡面没点名它交付的「{deliverable}」"


CONSOLE = (Path(__file__).resolve().parents[1]
           / "packages/a2n-server/src/a2n_server/web/console.html")


@pytest.mark.parametrize("profile", market.MARKET, ids=[p[0] for p in market.MARKET])
def test_every_market_skill_is_registered_in_a_named_console_category(profile):
    """市场每上一个新技能，控制台的分类表就得有它的归属。

    没登记会**静静掉进「其他」** —— 「其他」是给"暂时归不了类的技能"留的兜底，
    不该变成"忘了登记"的垃圾桶。这条守卫就是拦这个。
    """
    skill = profile[2]
    html = CONSOLE.read_text(encoding="utf-8")
    m = re.search(rf"'{re.escape(skill)}':\s*'([^']+)'", html)
    assert m, f"{skill} 没在 console.html 的 SKILL_CATEGORY 里登记（会静静掉进「其他」）"
    assert m.group(1) != "其他", f"{skill} 被归到了「其他」"


def test_console_opens_as_the_local_node_identity(monkeypatch):
    """控制台开箱身份 = **本机节点的 did**（一个节点一个身份）。

    2026-09-20 用户的口径：「**以本机节点开箱**」「一个节点生成一个身份id，
    只是为了对外辨识而已」。所以"我是谁"由**部署**决定：平台按
    `A2N_CONSOLE_PRINCIPAL` 把本机节点的 did 注入 `<body data-principal>`。
    没有配置文件里那个写死的账号了 —— 页头也没有切换控件（只有一个身份）。

    两条都要真验，否则是假绿：
      ① 配了 `A2N_CONSOLE_PRINCIPAL` → **服务端发出来的**那份页面里就是它；
      ② 没配 → 落回页面上那个中性兜底（不代表任何真实主体）。

    这个错真发生过：货架挂在 `acct:market-demo`、控制台开箱是 `acct:alice`，
    而自动化检查因为**自己先切了身份**再去断言，完全测不出人眼看到的一片空。
    """
    from fastapi.testclient import TestClient

    from a2n_server.app import app

    client = TestClient(app)
    monkeypatch.setenv("A2N_CONSOLE_PRINCIPAL", "did:a2n:ag_localnode001")
    html = client.get("/console").text
    m = re.search(r'<body[^>]*data-principal="([^"]+)"', html)
    assert m, "console.html 的 <body> 上找不到 data-principal 锚点，注入会静默失效"
    assert m.group(1) == "did:a2n:ag_localnode001", (
        f"控制台没按部署身份开箱，发出来的是 {m.group(1)} —— "
        f"打开「卖 Agent → 自售列表」会是一片空")

    monkeypatch.delenv("A2N_CONSOLE_PRINCIPAL")
    m = re.search(r'<body[^>]*data-principal="([^"]+)"', client.get("/console").text)
    assert m and m.group(1) == "acct:local", \
        "没配部署身份时应当落回中性兜底（acct:local），而不是某个真实主体"


def test_a_node_shelves_under_its_own_identity():
    """一个节点一个身份：「谁在卖」只能有一个答案。

    卡里自证的 `x-a2n.sovereign.did`（节点密钥派生）**就是**上架主体 ——
    以前货架挂在 `acct:alice` 这个人造账号名下，而卡里写的是节点自己的 did，
    同一个供给方两个答案。现在本机节点与容器节点都必须两处同源。

    覆盖口子（`override`）留着给测试造"两个主体"，但它不是默认路径。
    """
    ident = Identity.generate()
    # 走本机节点真正用的那条路（build_all），而不是另写一遍建卡
    for built in local.build_all(ident, local.ROLES):
        assert built["card"]["x-a2n"]["sovereign"]["did"] == ident.did, \
            f"本机节点 {built['role']} 档的卡没用自己的身份签"
        assert local.principal_for(ident) == ident.did, \
            f"本机节点 {built['role']} 档的上架主体不是自己的 did"
    for profile in market.MARKET:
        card = market.build_card(profile, ident)
        assert card["x-a2n"]["sovereign"]["did"] == ident.did, \
            f"{profile[0]} 的卡没用自己的身份签"
    assert market.principal_for(ident) == ident.did
    # 覆盖口子只在显式给的时候生效（测试造两个主体用）
    assert local.principal_for(ident, "acct:someone") == "acct:someone"


def test_console_header_has_no_identity_switcher():
    """控制台**只有一个身份**，页头不再提供切换（2026-09-19 用户提的）。

    守的是"别再长回来"：多一个身份控件，就等于把"一个身份既买又卖"的基本设定
    又掰回"两个账号"；而它一旦回来，上面那条"开箱就有货架"的守卫会**再次变成
    假绿** —— 脚本一 fill('#principal', ...) 就又测不到人眼看到的样子了。
    """
    html = CONSOLE.read_text(encoding="utf-8")
    assert not re.search(r'<input[^>]*id="principal"', html), \
        "页头又冒出了身份输入框 —— 控制台不需要多个身份"
    assert "新建主体" not in html, "「新建主体」按钮又回来了"
    assert "当前身份" not in html, "界面又在拿「当前身份」说事，而身份已经不在页头了"


def test_market_names_say_what_they_deliver_not_which_edition():
    """展示名只写**交付什么**，不带「· 专业版 / · 免费版」这类版本词。

    收不收费、什么档位是**数据** —— 价格列与状态列已经如实说了。把档位写进名字
    等于把可变事实刻成标识：同一件事在两个地方各说一遍迟早对不上；而且「专业版」
    到底指"收费的"还是"更高级的"，光看名字也猜不出来（2026-09-19 用户提的）。

    守的是**命名纪律**，不是某几个字：以后新加档位也别再往名字里塞版本词。
    """
    banned = ("专业版", "免费版", "公益版", "极速版", "入门版", "基础版", "旗舰版", "试用版")
    for profile in market.MARKET:
        slug, name = profile[0], profile[1]
        for word in banned:
            assert word not in name, (
                f"{slug} 的展示名 {name!r} 带了版本词「{word}」—— "
                f"名字只说交付什么，收不收费交给价格列")
