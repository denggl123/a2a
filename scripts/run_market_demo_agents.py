"""市场演示：几个**可售卖**的本地 agent，让发现页有多个分类、且一格价格里出现多种币种。

货架上摆的是**行业专家型、能交付成品**的服务，不是文本清理这类单点工具 ——
VISION 第一承重墙就是「卖成品工作流不是算力」：买家要的是"一份能用的东西"，
不是"一次 API 调用"。所以每张卡的 description 都写明**交付什么成品**。

成品怎么拼、算得对不对、给不了时怎么说 —— 那些 handler 已经搬进
``a2n_sdk.greenlight``（纯标准库、可被任何节点复用，本文件只负责"定价 + 上架"）。
搬家的理由：本机节点也要上架同类的行业专家卡，两处各写一份 handler 迟早对不上。

这些都是**本地确定性测试服务，非模型推理**：不联网、不调模型、同样输入必得同样输出，
卡上如实写明。别把它们当成真能出片、出报表的生产服务 —— 演示的是"成品以什么形状
被交付、被计价、被验收"，不是模型能力。

与 ``run_free_demo_agents.py`` 的区别就一条，但很关键：

* 那边的夹具是**自愿免费**的，卡上不写价目表（"不收费"是当下事实）；
* 这里的收费夹具会写 ``price_book``，而且是**两条独立挂牌**（CNY + USDC）。

两个币种不是"一个价按汇率算两遍"，是**两条各自独立的挂牌**：网络不做换算、
也不跨币种相加（金额单一源 `a2n_settlement.price`）。所以同时挂两种币是供给方的
选择，不是平台替它编出来的汇率。控制台价格列把它们并排显示，主价在前、其余小字。

免费那几条沿用免费夹具的措辞纪律：说"不收费"（当下事实）可以，说"永久"（对未来的
承诺）不行（VISION §5.1 / §7）。卡片 description 是**买家可见的对外文案**。

本地跑：``python scripts/run_market_demo_agents.py --base http://127.0.0.1:18787``

上架主体 = **本节点自己的 did**（一个节点一个身份，见 `principal_for`）：
不另造 `acct:alice` 这类账号 —— 卡里自证的身份是谁，货架就挂在谁名下。

钥匙存在 ``--state-dir``/``node.key.json``（**一份进程一把**，不是一张卡一把），
上架的 ``uid`` 由 did + slug 派生 —— 重启**复用同一条上架**，不会像随机 uid 那样
每次都多注册一条。
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import uuid
from decimal import Decimal
from pathlib import Path

from a2n_custodian.media import by_currency
from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Client, Node
# 后缀与 handler 同源（SDK 的 greenlight）—— 两个上架入口共用同一句，不各抄一份。
from a2n_sdk.greenlight import (  # noqa: F401
    FREE_SUFFIX, PAID_SUFFIX, ecom_listing, finance_report, game_design,
    get as greenlight_spec, legal_contract, make_local_api, text_of,
    video_script, video_short)

# 同目录脚本：复用已经测过的公共件（幂等注册客户端），不另写一套
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_free_demo_agents import ReusableDemoClient  # noqa: E402


def _minor(amount: str, currency: str) -> int:
    """主单位 → 最小单位整数。**不是整数倍就报错，绝不四舍五入。**

    精度的事实源在持牌层 ``a2n_custodian.media``（"钱以什么形态存在"只在那里回答）。
    这里**不许**再抄一张 {币种: 小数位} 表 —— 抄一份就多一个会漂移的真相。

    另外：CNY 的最小单位是分，所以 ``0.015`` 这种挂牌价根本不存在。
    悄悄 round 成 ``0.02`` 等于背着供给方改了价，必须当场炸。
    """
    media = by_currency(currency)
    if not media:
        raise ValueError(f"未注册的币种：{currency}（媒介注册表里没有它）")
    exp = int(media[0]["exponent"])
    minor = Decimal(amount) * (10 ** exp)
    if minor != minor.to_integral_value():
        raise ValueError(
            f"{amount} {currency} 不是该币种最小单位（10^-{exp}）的整数倍 —— "
            f"挂牌价必须能用最小单位整数表示，否则会被悄悄改价")
    return int(minor)


# ---------------------------------------------------------------- 货架
# 一档一卡（一个案例 = 一张卡 = 一个技能），展示名**只写它交付什么**，不带
# 「· 专业版 / · 免费版」这类版本词：收不收费/什么档位是**数据**，价格列与状态列
# 已经如实说了，写进名字等于把可变事实刻成标识，同一件事两处各说一遍迟早对不上
# （2026-09-19 用户提的）。
#
# 三台容器各摆两份（见 CONTAINERS）。收费档挂两个币种 = 两条独立挂牌。
CONTAINER_SLUGS = ("video-short", "video-script", "finance-report",
                   "legal-contract", "game-design", "ecom-listing")

# 收费档的挂牌价（主单位）。None = 自愿免费。同一份成品在两个节点上可以是不同价 ——
# 这正是"汇率即价格"：网络只把事实摆出来，不替供给方定。
PRICES = {
    "video-short": {"CNY": "0.30", "USDC": "0.04"},
    "finance-report": {"CNY": "0.50", "USDC": "0.07"},
    "legal-contract": {"CNY": "0.40", "USDC": "0.055"},
}

# slug, 展示名, 技能 id, 技能展示名, 挂牌价, 描述, 标签, 处理函数
MARKET = [
    (s.slug, s.name, s.skill, s.skill_name, PRICES.get(s.slug), s.description,
     list(s.tags), s.handler)
    for s in (greenlight_spec(slug) for slug in CONTAINER_SLUGS)
]

# 三个容器：案例搬进容器后，**一个容器 = 一台机器，跑两份供给**（各一张卡）。
#
# 为什么不是"一张卡挂两档"：卡的 `accepts` 是**卡级**的，价目却是**技能级**的 ——
# 门禁的 `charging(card)` 只看卡级 accepts，于是"免费档 + 收费档"混挂的那张卡，
# 免费那一档也会被判成"收费（None 结价）"直接拒掉，本来该放行的免费调用反而调不通。
# 那是门禁语义的问题（要改就得动钱那一层），不该为了让演示摆得好看去改，
# 更不该反过来把免费档硬说成收费。所以**一档一卡**，与主机上原来的形状完全一致。
#
# 分组不是随手配的：三个容器分别落在不同行业，发现页的分类筛选才有东西可筛
# （六档挤在同一个行业里，筛选器等于摆设 —— 与 SKILL_CATEGORY 的存在理由同一条）。
# 收费档（两条独立币种挂牌）落在 1、2 号，3 号全是免费档，让"一格价格里多种币种"
# 与"不收费是当下事实"两种措辞都有真身可看。
CONTAINERS = [
    ("video-studio", "短视频成片与口播稿", ["video-short", "video-script"]),
    ("finance-legal", "经营报表与合同草案", ["finance-report", "legal-contract"]),
    ("play-ecom", "游戏策划案与商品详情页", ["game-design", "ecom-listing"]),
]

# 容器里哪一张卡**故意不通**（"最后一跳真的连不上"）。
#
# 用户 2026-09-20 的原话：「某个节点上架了一个 agent。我这个节点发现了，看着所有都
# 正常。只有调用时……他去转发调用 agent 时，发现调用不通。返回失败。」这条链要留一张
# 真身：发现通、对方节点活、请求也送到了，断在**它转发给自己那台 agent**。
# 其余卡都接真 handler（真返回成品）—— 演示既看得到"绿"，也看得到"失败要如实说"。
DEAD_SLUG = "video-script"

# 收费档要付得起才算"在卖"：走对等账户（先用后结）或直付渠道。
# 不声明的话门禁会退化成"只能建对等账户配对"，演示里没人能直接下单。
PAID_ACCEPTS = ["peer_account", "direct_pay:alipay"]


# 上架主体 = **节点自己的 did**（2026-09-20 用户拍板「一个节点就一个身份」）。
#
# 以前货架挂在一个人造账号 `acct:alice` 名下 —— 那个名字跟卡里自证的身份
# （`x-a2n.sovereign.did`，节点密钥派生）是两回事，等于同一个供给方有两个答案：
# 卡说"我是 did:a2n:ag_…"，平台列表里却说"这是 alice 的"。用户的原话是
# 「**一个节点生成一个身份id，只是为了对外辨识而已**」—— 那就别再造第二个。
#
# 一个身份本来就既买又卖（A2N 的基本设定，不是两个账号）。
# `principal_for()` 是**唯一**决定"挂谁名下"的地方。
def principal_for(identity, override: str | None = None) -> str:
    """上架主体。默认 = 这个节点自己的 did；override 只给测试造"两个主体"用。"""
    return override or identity.did


def build_card(profile, identity):
    slug, name, skill, skill_name, prices, description, tags, _handler = profile
    x_a2n = {
        # uid 由钥匙派生：重启必须复用同一条上架，而不是再插一条新的
        "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, identity.did + "/market/" + slug)),
        "deployment": {"region": "local-demo"},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
        "sovereign": {"did": identity.did, "pub": pub_b64(identity.pub_raw)},
    }
    if prices:
        # 多币种 = 多条独立挂牌，各按自己的币种精度换算，互不换算
        x_a2n["price_book"] = {skill: {
            cur: {"dimensions": [{"key": "call_count", "amount": _minor(val, cur), "per": 1}]}
            for cur, val in prices.items()}}
    card = {
        "name": name,
        "description": description + (PAID_SUFFIX if prices else FREE_SUFFIX),
        "version": "1.0.0",
        "url": None,
        "skills": [{"id": skill, "name": skill_name, "description": description,
                    "tags": tags, "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["application/json"]}],
        "x-a2n": x_a2n,
    }
    if prices:
        card["accepts"] = list(PAID_ACCEPTS)
    card["x-a2n"]["sovereign"]["sig"] = identity.sign(card_body(card))
    return card


def price_text(prices) -> str:
    return " / ".join(f"{val} {cur}/次" for cur, val in (prices or {}).items()) or "免费"


def serve_profile(profile, identity, args, port):
    slug, name, skill, _skill_name, prices, _description, _tags, handler = profile
    principal = principal_for(identity, args.principal)

    # 本地服务（relay 中继转发进来时落地的地方）走 SDK 里那条共享实现，
    # 不在本文件再抄一遍"从 message parts 里取 payload"的逻辑。
    local_api = make_local_api(skill, handler)

    def attest(task_id, node_id, dims):
        return sign_metering(identity, task_id=task_id, node_id=node_id, dims=dims)

    node = Node(build_card(profile, identity), {skill: handler},
                principal=principal, base_url=args.base, visibility=args.visibility,
                attest_fn=attest)
    node.client = ReusableDemoClient(args.base, principal=principal)
    print(f"[market] {name} · {price_text(prices)} · 本地端口 {port} · "
          f"主体 {principal}", flush=True)
    node.serve(console=False, local_agent=(port, local_api))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18787")
    parser.add_argument("--principal", default=None,
                        help="覆盖上架主体（默认 = 本节点自己的 did）")
    parser.add_argument("--visibility", choices=("private", "unlisted", "public"),
                        default="public")
    parser.add_argument("--port-base", type=int, default=9250)
    parser.add_argument("--state-dir", default="data/market-demo-agents")
    args = parser.parse_args()
    # 一份进程 = 一个节点 = 一把钥匙（不是一张卡一把）：六张卡同签、同主体。
    state_dir = Path(args.state_dir)
    key_path = state_dir / "node.key.json"
    if key_path.exists():
        identity = Identity.load(key_path)
    else:
        state_dir.mkdir(parents=True, exist_ok=True)
        identity = Identity.generate()
        identity.save(key_path)
    print(f"[market] 节点身份 {identity.did} · 上架 {len(MARKET)} 张卡", flush=True)
    errors: queue.Queue = queue.Queue()

    def worker(profile, port):
        try:
            serve_profile(profile, identity, args, port)
        except Exception as exc:
            errors.put((profile[0], exc))

    for index, profile in enumerate(MARKET):
        threading.Thread(target=worker, args=(profile, args.port_base + index),
                         name="market-" + profile[0], daemon=True).start()
    try:
        slug, exc = errors.get()
        raise RuntimeError(f"{slug} 市场节点启动或运行失败") from exc
    except KeyboardInterrupt:
        print("\n市场测试节点已停止；上架信息与本地身份保留，重启可复用。")


if __name__ == "__main__":
    main()
