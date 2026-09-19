"""市场演示：几个**可售卖**的本地 agent，让发现页有多个分类、且一格价格里出现多种币种。

货架上摆的是**行业专家型、能交付成品**的服务，不是文本清理这类单点工具 ——
VISION 第一承重墙就是「卖成品工作流不是算力」：买家要的是"一份能用的东西"，
不是"一次 API 调用"。所以每张卡的 description 都写明**交付什么成品**，
handler 也真的把那份成品拼出来（分镜表 / 经营报表 / 合同条款 / 关卡表 / 详情页骨架）。

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

本地跑：``python scripts/run_market_demo_agents.py --base http://127.0.0.1:8000``

钥匙存在 ``--state-dir`` 下，上架的 ``uid`` 由钥匙派生 —— 重启**复用同一条上架**，
不会像随机 uid 那样每次都多注册一条。
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import uuid
from decimal import Decimal
from pathlib import Path

from a2n_custodian.media import by_currency
from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Client, Node

# 同目录脚本：复用已经测过的公共件（幂等注册客户端 + 取文本），不另写一套
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_free_demo_agents import ReusableDemoClient, text_of  # noqa: E402

FREE_SUFFIX = " 供给方自愿公益 · 不收费 · 本地确定性测试服务，非模型推理。"
PAID_SUFFIX = " 供给方自主定价 · 本地确定性测试服务，非模型推理。"


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


# ---------------------------------------------------------------- 成品交付件
# 每个 handler 把输入拼成**一份确定的成品**（同样输入必得同样输出）。
# 输入不合法、或不足以支撑结论，就如实报错、或如实标「待补充」——
# 不猜、不编、不假装成功。这条纪律和证据面是一致的：不给结论就给"为什么给不了"。


def _lines(payload):
    """把输入拆成要点：按行 / 分号切，去掉项目符号与空白，去重后保留顺序。"""
    out = []
    for part in re.split(r"[\n\r;；]+", text_of(payload)):
        item = part.strip().lstrip("-•*·、").strip()
        if item and item not in out:
            out.append(item)
    return out


_SHOT_SECONDS = 5


def video_short(payload):
    """短视频成片包：分镜表 + 口播稿 + 封面文案 + 话题标签（一份可直接开拍的成品）。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给主题与卖点（第一行主题，其余每行一个卖点）")
    topic, points = lines[0], (lines[1:] or [lines[0]])
    points = points[:6]                     # 成片控制在 30 秒内，别做成无限长
    shots = [{
        "no": i + 1,
        "sec": f"{i * _SHOT_SECONDS}-{(i + 1) * _SHOT_SECONDS}",
        "visual": f"画面：{point}",
        "voiceover": f"{point}。",
        "on_screen": point[:12],
    } for i, point in enumerate(points)]
    return {
        "deliverable": "短视频成片包",
        "topic": topic,
        "aspect": "9:16",
        "duration_sec": _SHOT_SECONDS * len(shots),
        "shots": shots,
        "cover_text": topic[:10],
        "hashtags": [f"#{w}" for w in re.split(r"[\s，,、]+", topic) if w][:5],
        "delivered": ["分镜表", "口播稿", "封面文案", "话题标签"],
    }


def video_script(payload):
    """短视频口播稿：只要词、不要分镜的场合 —— 交付一版能直接念的逐句稿。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给主题或几个要点（每行一条）")
    topic, points = lines[0], (lines[1:] or [lines[0]])
    script = [{"no": 1, "line": f"先说清楚这是什么：{topic}。"}]
    for point in points[:8]:
        script.append({"no": len(script) + 1, "line": f"第 {len(script)} 个点，{point}。"})
    script.append({"no": len(script) + 1, "line": "就这几件事，需要的话点下方联系。"})
    return {
        "deliverable": "短视频口播稿",
        "topic": topic,
        "word_count": sum(len(s["line"]) for s in script),
        "script": script,
        "delivered": ["逐句口播稿", "字数统计"],
    }


_AMOUNT_LINE = re.compile(r"^(.+?)[\s,，:：]*(-?\d+(?:\.\d+)?)$")


def finance_report(payload):
    """经营报表：把「科目 金额」明细汇成损益 + 口径 + 关键比率。"""
    rows = []
    for line in _lines(payload):
        m = _AMOUNT_LINE.match(line)
        if not m:
            raise ValueError(f"行「{line}」要写成「科目 金额」，例如：销售回款 120000")
        rows.append((m.group(1).strip(), Decimal(m.group(2))))
    if not rows:
        raise ValueError("请给收支明细（每行「科目 金额」，收入为正、支出为负）")
    income = sum(a for _, a in rows if a > 0)
    cost = sum(-a for _, a in rows if a < 0)
    profit = income - cost
    margin = (profit / income * 100) if income else None
    return {
        "deliverable": "经营报表",
        "lines": [{"subject": s, "amount": str(a)} for s, a in rows],
        "income": str(income),
        "cost": str(cost),
        "profit": str(profit),
        # 收入为零时不硬编一个利润率出来 —— 给不了就给 None，并说明为什么
        "margin_pct": None if margin is None else f"{margin:.1f}",
        "basis": "收入 = 正数科目合计，成本 = 负数科目取正后合计。只做加总，不外推、不做预测。",
        "delivered": ["损益汇总", "现金流口径", "关键比率"],
    }


# 合同的标准条款骨架。输入只填前面几条、后面缺着，就如实标「待补充」。
_CONTRACT_SLOTS = ["标的与范围", "价款与支付", "交付与验收", "违约责任", "争议解决", "保密"]


def legal_contract(payload):
    """合同草案：把交易要点填进标准条款骨架；没提到的条款如实标「待补充」。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给交易要点（每行一条），例如：标的 软件定制开发")
    clauses = []
    for i, slot in enumerate(_CONTRACT_SLOTS):
        given = lines[i] if i < len(lines) else None
        clauses.append({
            "no": i + 1,
            "title": slot,
            "body": given or "待补充（输入的要点没提到，不替当事方拟）",
            "source": "供给方输入的要点" if given else "未提供",
        })
    unfilled = sum(1 for c in clauses if c["source"] == "未提供")
    return {
        "deliverable": "合同草案",
        "clauses": clauses,
        "unfilled": unfilled,
        # 措辞要点：说清这是草案、不是法律意见；空缺如实标出，不留白让人以为已谈妥
        "note": "这是**草案骨架**，不是法律意见。没提供的条款一律标待补充，"
                "不留空白让人误以为已谈妥；签署前请交执业律师复核。",
        "delivered": ["条款草案", "待补充清单", "签署前提示"],
    }


def game_design(payload):
    """游戏策划案：核心循环 + 关卡表 + 数值初值（一份能交给人做的策划案）。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给玩法概念（一句话也行）")
    concept, focus = lines[0], (lines[1:] or [lines[0]])
    loop = ["挑关（看难度与奖励）", "配资源（从已解锁里选）", "打一局并结算",
            "按结果解锁新东西", "回到挑关"]
    levels = [{
        "level": i,
        "goal": f"第 {i} 关：围绕「{concept}」搭第 {i} 级难度台阶",
        "focus": focus[(i - 1) % len(focus)],
        "time_limit_sec": 60 + 15 * i,
    } for i in range(1, 6)]
    return {
        "deliverable": "游戏策划案",
        "concept": concept,
        "core_loop": loop,
        "levels": levels,
        # 数值只给"能跑通"的初值，并说清它不是平衡 —— 别把初值说成结论
        "balance": {"initial_lives": 3, "difficulty_step": 1.15, "reward_growth": 1.2,
                    "note": "初值只保证能跑通，真平衡要实测迭代"},
        "delivered": ["核心循环", "关卡表", "数值初值"],
    }


def ecom_listing(payload):
    """商品详情页：标题 + 主图文案 + 卖点块 + 规格/售后占位（一份详情页骨架）。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给商品名与卖点（第一行商品名，其余每行一个卖点）")
    product, points = lines[0], (lines[1:] or [lines[0]])
    return {
        "deliverable": "商品详情页",
        "title": f"{product}｜{points[0][:16]}",
        "blocks": [
            {"no": 1, "type": "主图文案", "text": points[0][:14]},
            {"no": 2, "type": "卖点", "items": points[:5]},
            # 规格与售后是**占位**：本服务不替商家编规格、也不代填售后承诺
            {"no": 3, "type": "规格表", "rows": [{"name": "规格", "value": "占位，按实际填写"}]},
            {"no": 4, "type": "售后", "text": "占位 —— 按实际条款填写，本服务不代填"},
        ],
        "delivered": ["标题与主图文案", "卖点块", "详情页骨架"],
    }


# slug, 展示名, 技能 id, 技能展示名, 挂牌价 {币种: 主单位单价}（None = 免费）, 描述, 标签, 处理函数
#
# 摆的是行业专家、且都写明**交付什么成品**；收费档挂两个币种（两条独立挂牌）。
MARKET = [
    ("video-short", "短视频成片包 · 专业版", "video-short", "短视频成片包",
     {"CNY": "0.30", "USDC": "0.04"},
     "交付一份「短视频成片包」：逐镜画面、口播、屏显与时长，附封面文案和话题标签，"
     "输入主题与卖点即可。成品是脚本包 —— 不出片、不剪辑。",
     ["视频", "分镜", "口播稿"], video_short),
    ("video-script", "短视频口播稿 · 免费版", "video-script", "短视频口播稿", None,
     "交付一份「短视频口播稿」：逐句可念的稿子，附字数统计。只要词、不要分镜的场合用它。",
     ["视频", "口播稿", "文案"], video_script),
    ("finance-report", "经营报表 · 专业版", "finance-report", "经营报表",
     {"CNY": "0.50", "USDC": "0.07"},
     "交付一份「经营报表」：收入、成本、利润与利润率，并写明口径。"
     "只做加总，不外推、不做预测；收入为零时不硬给利润率。",
     ["财务", "报表", "经营"], finance_report),
    ("legal-contract", "合同草案 · 专业版", "legal-contract", "合同草案",
     {"CNY": "0.40", "USDC": "0.055"},
     "交付一份「合同草案」骨架：标的、价款与支付、交付与验收、违约、争议解决、保密。"
     "没提到的条款如实标「待补充」—— 这是草案、不是法律意见，签署前请律师复核。",
     ["法务", "合同", "草案"], legal_contract),
    ("game-design", "游戏策划案 · 免费版", "game-design", "游戏策划案", None,
     "交付一份「游戏策划案」：核心循环、五关关卡表、数值初值，"
     "并说明初值只保证可跑通、不等于平衡。",
     ["游戏", "策划", "数值"], game_design),
    ("ecom-listing", "商品详情页 · 免费版", "ecom-listing", "商品详情页", None,
     "交付一份「商品详情页」骨架：标题、主图文案、卖点块，"
     "规格与售后留占位（占位不代填）。",
     ["电商", "详情页", "文案"], ecom_listing),
]

# 收费档要付得起才算"在卖"：走对等账户（先用后结）或直付渠道。
# 不声明的话门禁会退化成"只能建对等账户配对"，演示里没人能直接下单。
PAID_ACCEPTS = ["peer_account", "direct_pay:alipay"]


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


def serve_profile(profile, args, port):
    slug, name, skill, _skill_name, prices, _description, _tags, handler = profile
    key_path = Path(args.state_dir) / f"{slug}.key.json"
    if key_path.exists():
        identity = Identity.load(key_path)
    else:
        identity = Identity.generate()
        identity.save(key_path)

    def local_api(path, payload):
        if path != "/invoke":
            raise ValueError("unknown path")
        if payload.get("skill") not in (None, "", skill):
            raise ValueError("unknown skill")
        body = payload.get("payload")
        if body is None:
            for part in (payload.get("message") or {}).get("parts", []):
                if "data" in part or "text" in part:
                    body = part.get("data") if "data" in part else part["text"]
                    break
        if len(text_of(body)) > 100_000:
            raise ValueError("测试服务最多接收 100000 字符")
        return handler(body)

    def attest(task_id, node_id, dims):
        return sign_metering(identity, task_id=task_id, node_id=node_id, dims=dims)

    node = Node(build_card(profile, identity), {skill: handler},
                principal=args.principal, base_url=args.base, visibility=args.visibility,
                attest_fn=attest)
    node.client = ReusableDemoClient(args.base, principal=args.principal)
    print(f"[market] {name} · {price_text(prices)} · 本地端口 {port}", flush=True)
    node.serve(console=False, local_agent=(port, local_api))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--principal", default="acct:market-demo")
    parser.add_argument("--visibility", choices=("private", "unlisted", "public"),
                        default="public")
    parser.add_argument("--port-base", type=int, default=9250)
    parser.add_argument("--state-dir", default="data/market-demo-agents")
    args = parser.parse_args()
    errors: queue.Queue = queue.Queue()

    def worker(profile, port):
        try:
            serve_profile(profile, args, port)
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
