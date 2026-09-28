"""绿灯 agent —— 一组**确定性**的「行业专家 / 成品交付型」样例 agent（纯标准库）。

为什么放进 SDK
--------------
节点要证明「发现 → 调用 → 交付 → 验收」这条链路真的通，需要一份**必然成功**的供给：
同样输入必得同样输出、不联网、不调模型。它和身份 / 签名 / 定价无关 —— 那些由调用方
注入（SDK 保持零依赖，见 `tests/test_architecture.py::test_sdk_is_dependency_free`），
这里只回答一件事：**交付物长什么样**。

三个用途：

* 「每个节点都上架一份」的现成素材（本机节点与容器都从这里取，不各写一份）；
* 独立跑成一个 HTTP 上游（`serve_http`），给 a2n-node 的供给当**真实上游**；
* 单测里当夹具。

纪律（贴着 VISION 第一承重墙「卖成品工作流不是算力」）
------------------------------------------------------
* 每份都**真的拼出一件成品**，并报出「交付了哪几件」（`deliverable` / `delivered`）；
* 输入不足就**如实报错**、或如实标「待补充」，不猜、不编、不假装成功；
* 是**本地确定性测试服务，非模型推理** —— 卡面必须如实写明（谁也不能把它当真产能）。

措辞纪律：说「不收费」（当下事实）可以，说「对未来的永久承诺」不行（VISION §5.1 / §7）。
"""
from __future__ import annotations

import json
import re
import threading
from decimal import ROUND_HALF_UP, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, NamedTuple


# ---------------------------------------------------------------- 输入适配
def text_of(payload: Any) -> str:
    """把「文本 / {\"text\": ...}」两种输入形态统一成一段文本。

    标准 A2A 的 text part 进来是字符串，data part 进来是 dict —— 两种都得接住。
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("text", ""))
    raise ValueError('传入文本，或 {"text": "..."}')


def _lines(payload: Any) -> list[str]:
    """把输入拆成要点：按行 / 分号切，去掉项目符号与空白，去重后保留顺序。"""
    out: list[str] = []
    for part in re.split(r"[\n\r;；]+", text_of(payload)):
        item = part.strip().lstrip("-•*·、").strip()
        if item and item not in out:
            out.append(item)
    return out


# ---------------------------------------------------------------- 成品交付件
# 每个 handler 把输入拼成**一份确定的成品**（同样输入必得同样输出）。
# 输入不合法、或不足以支撑结论，就如实报错、或如实标「待补充」——
# 不猜、不编、不假装成功。这条纪律和证据面是一致的：不给结论就给"为什么给不了"。

_AMOUNT_LINE = re.compile(r"^(.+?)[\s,，:：]*(-?\d+(?:\.\d+)?)$")
_SHOT_SECONDS = 5
_CONTRACT_SLOTS = ["标的与范围", "价款与支付", "交付与验收", "违约责任", "争议解决", "保密"]


def video_short(payload: Any) -> dict:
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


def video_script(payload: Any) -> dict:
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


def finance_report(payload: Any) -> dict:
    """经营报表：把「科目 金额」明细汇成损益 + 口径 + 关键比率。"""
    rows: list[tuple[str, Decimal]] = []
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


def legal_contract(payload: Any) -> dict:
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


def game_design(payload: Any) -> dict:
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


def ecom_listing(payload: Any) -> dict:
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


def resume_polish(payload: Any) -> dict:
    """简历优化包：把经历要点重排成「动作 + 量化结果」，附求职信与面试问答。

    不替人编造经历 —— 缺量化结果的地方如实标出来让求职者自己补，
    这与"输入不足就如实说"是同一条纪律。
    """
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给求职目标与经历要点（第一行目标岗位，其余每行一条经历）")
    target, points = lines[0], (lines[1:] or [lines[0]])
    points = points[:8]
    highlights = [{
        "no": i + 1,
        "original": point,
        "suggestion": f"{point} —— 补成「做了什么 + 量化结果」（例：主导 X，使 Y 提升 N%）",
    } for i, point in enumerate(points)]
    cover_letter = "\n".join([
        "尊敬的招聘团队：",
        f"我应聘「{target}」，以下 {len(highlights)} 段经历与岗位最相关"
        "（已按「动作 + 量化结果」重排）：",
        *[f"  {h['no']}. {h['suggestion']}" for h in highlights[:3]],
        "期待进一步沟通。",
    ])
    interview_qa = [
        {"q": "为什么想来这个岗位？", "a": f"围绕「{target}」，用 1–2 段可量化的经历说明匹配度。"},
        {"q": "你最有代表性的成果是什么？",
         "a": f"见经历要点第 1 条：{highlights[0]['original']}（请补上量化结果）。"},
        {"q": "你的短板是什么？",
         "a": "如实说明一项与岗位相关度较低的能力，并给出正在补的具体动作。"},
    ]
    return {
        "deliverable": "简历优化包",
        "target": target,
        "highlights": highlights,
        "cover_letter": cover_letter,
        "interview_qa": interview_qa,
        "delivered": ["经历要点重排", "求职信草稿", "面试问答"],
    }


_DAY_SLOTS = ["上午", "中午", "下午", "晚上"]
_TRIP_HEAD = re.compile(r"^(.+?)[\s,，:：]*(\d+)\s*天?$")


def trip_plan(payload: Any) -> dict:
    """行程规划单：逐日安排 + 预算估算 + 行前清单（一份可直接照着走的行程）。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给目的地与天数（第一行「目的地 天数」，其余每行一个想去的地方）")
    head = lines[0]
    m = _TRIP_HEAD.match(head)
    if m:
        destination, days_n = m.group(1).strip() or head, int(m.group(2))
    else:
        # 没写天数就不替人猜 —— 用两天的默认值，并在口径里说明这是默认
        destination, days_n = head, 2
    days_n = max(1, min(days_n, 14))        # 超过两周的行程不是一份"能照着走"的单子
    spots = lines[1:] or [f"{destination}的地标与街区"]
    days = [{
        "day": d,
        "plan": [{"slot": slot, "activity": spots[(d - 1 + i) % len(spots)]}
                 for i, slot in enumerate(_DAY_SLOTS)],
    } for d in range(1, days_n + 1)]
    budget = {
        "currency": "CNY",
        "per_day": "600",
        "total": str(600 * days_n),
        "basis": "按每天 600 元粗估（餐饮 + 门票 + 市内交通），不含往返大交通；"
                 "只给量级，实际以订票为准。",
    }
    return {
        "deliverable": "行程规划单",
        "destination": destination,
        "days": days,
        "budget": budget,
        "checklist": ["证件与票据", "常用药与充电", f"{destination}的天气与衣物", "住宿与交通确认单"],
        "delivered": ["逐日行程", "预算估算", "行前清单"],
    }


def course_outline(payload: Any) -> dict:
    """课程大纲：教学目标 + 课时表 + 作业设计（一份能直接开课的纲）。"""
    lines = _lines(payload)
    if not lines:
        raise ValueError("请给课程主题与目标（第一行课程名，其余每行一个教学目标）")
    course, rest = lines[0], (lines[1:] or [lines[0]])
    objectives = [f"学完能：{r}" for r in rest[:5]]
    sessions = [{
        "no": i,
        "title": f"第 {i} 讲：围绕「{course}」的第 {i} 个主题",
        "focus": rest[(i - 1) % len(rest)],
        "minutes": 45,
    } for i in range(1, 7)]
    assignments = [
        {"no": 1, "task": f"结合「{course}」写一页小结", "type": "书面"},
        {"no": 2, "task": "用本讲方法解一道实际问题，并写明取舍", "type": "实操"},
    ]
    return {
        "deliverable": "课程大纲",
        "course": course,
        "objectives": objectives,
        "sessions": sessions,
        "assignments": assignments,
        "delivered": ["教学目标", "课时表", "作业设计"],
    }


_PRICE_LINE = re.compile(r"^(.+?)[\s,，:：]*(\d+(?:\.\d+)?)$")
_MARKUP = Decimal("2.8")                    # 成本 → 建议售价的倍率（毛利率约 64%）


def menu_pricing(payload: Any) -> dict:
    """菜单定价表：按「成本 × 倍率」给建议售价 + 毛利 + 套餐建议。"""
    items = []
    for line in _lines(payload):
        m = _PRICE_LINE.match(line)
        if not m:
            raise ValueError(f"行「{line}」要写成「菜品 成本」，例如：招牌牛肉面 8.5")
        cost = Decimal(m.group(2))
        price = (cost * _MARKUP).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        margin = ((price - cost) / price * 100) if price else None
        items.append({
            "dish": m.group(1).strip(),
            "cost": str(cost),
            "suggested_price": str(price),
            "margin_pct": None if margin is None else f"{margin:.1f}",
        })
    if not items:
        raise ValueError("请给菜品与成本（每行「菜品 成本」）")
    return {
        "deliverable": "菜单定价表",
        "items": items,
        "combo": {"name": "招牌双人餐", "contains": [i["dish"] for i in items[:3]],
                  "note": "套餐价按单品建议售价合计后再让利，折扣由门店决定。"},
        "rule": f"建议售价 = 成本 × {_MARKUP}（毛利率约 64%），四舍五入到分 —— "
                f"只按规则算，不替门店定策略。",
        "delivered": ["建议售价", "毛利测算", "套餐建议"],
    }


# ---------------------------------------------------------------- 登记表
# 一条 = 一份可交付的成品。展示名**只写交付什么**，不带「· 专业版 / · 免费版」这类
# 版本词：收不收费是**数据**（价格列/状态列已经如实说了），写进名字等于把可变事实
# 刻成标识，同一件事两处各说一遍迟早对不上（2026-09-19 用户提的）。
#
# 各节点从这张表里**挑**自己上架的那几份（本机节点 / 容器各挑一部分），
# 不另写一套 handler —— 分叉两份就迟早对不上。
class Spec(NamedTuple):
    slug: str
    name: str                # 展示名 = 交付物名
    skill: str               # 技能 id
    skill_name: str
    description: str         # 卡面描述（买家可见；收费/免费后缀由调用方按事实补）
    tags: tuple[str, ...]
    handler: Callable[[Any], dict]


SPECS: tuple[Spec, ...] = (
    # —— 容器节点（三台"外面机器"）挑的六份 ——
    Spec("video-short", "短视频成片包", "video-short", "短视频成片包",
         "交付一份「短视频成片包」：逐镜画面、口播、屏显与时长，附封面文案和话题标签，"
         "输入主题与卖点即可。成品是脚本包 —— 不出片、不剪辑。",
         ("视频", "分镜", "口播稿"), video_short),
    Spec("video-script", "短视频口播稿", "video-script", "短视频口播稿",
         "交付一份「短视频口播稿」：逐句可念的稿子，附字数统计。只要词、不要分镜的场合用它。",
         ("视频", "口播稿", "文案"), video_script),
    Spec("finance-report", "经营报表", "finance-report", "经营报表",
         "交付一份「经营报表」：收入、成本、利润与利润率，并写明口径。"
         "只做加总，不外推、不做预测；收入为零时不硬给利润率。",
         ("财务", "报表", "经营"), finance_report),
    Spec("legal-contract", "合同草案", "legal-contract", "合同草案",
         "交付一份「合同草案」骨架：标的、价款与支付、交付与验收、违约、争议解决、保密。"
         "没提到的条款如实标「待补充」—— 这是草案、不是法律意见，签署前请律师复核。",
         ("法务", "合同", "草案"), legal_contract),
    Spec("game-design", "游戏策划案", "game-design", "游戏策划案",
         "交付一份「游戏策划案」：核心循环、五关关卡表、数值初值，"
         "并说明初值只保证可跑通、不等于平衡。",
         ("游戏", "策划", "数值"), game_design),
    Spec("ecom-listing", "商品详情页", "ecom-listing", "商品详情页",
         "交付一份「商品详情页」骨架：标题、主图文案、卖点块，"
         "规格与售后留占位（占位不代填）。",
         ("电商", "详情页", "文案"), ecom_listing),
    # —— 本机节点挑的四份（覆盖四条结算通道：收费 / 免费 / x402 / 试用中）——
    Spec("resume-polish", "简历优化包", "resume-polish", "简历优化包",
         "交付一份「简历优化包」：把经历要点重排成「动作 + 量化结果」，附求职信草稿"
         "与常见面试问答。不替你编造经历 —— 缺量化结果的地方如实标出来让你补。",
         ("招聘", "简历", "求职"), resume_polish),
    Spec("trip-plan", "行程规划单", "trip-plan", "行程规划单",
         "交付一份「行程规划单」：逐日安排、预算量级与行前清单。"
         "预算只给量级（按每天 600 元粗估），实际以订票为准。",
         ("旅行", "行程", "规划"), trip_plan),
    Spec("course-outline", "课程大纲", "course-outline", "课程大纲",
         "交付一份「课程大纲」：教学目标、六讲课时表与作业设计，可直接拿去开课。",
         ("教育", "课程", "培训"), course_outline),
    Spec("menu-pricing", "菜单定价表", "menu-pricing", "菜单定价表",
         "交付一份「菜单定价表」：按「成本 × 倍率」给建议售价与毛利，附套餐建议。"
         "只按规则算，不替门店定策略。",
         ("餐饮", "定价", "菜单"), menu_pricing),
)

BY_SLUG: dict[str, Spec] = {s.slug: s for s in SPECS}

# 卡面描述的后缀：说清这是**本地确定性测试服务**，别被当成真产能。
# 两个上架入口（本机节点 / 容器）共用同一句 —— 不各抄一份，免得漂。
FREE_SUFFIX = " 供给方自愿公益 · 不收费 · 本地确定性测试服务，非模型推理。"
PAID_SUFFIX = " 供给方自主定价 · 本地确定性测试服务，非模型推理。"


def get(slug: str) -> Spec:
    """按 slug 取一份绿灯 agent；不认识就**响亮报错**（不静默给个默认）。"""
    spec = BY_SLUG.get(slug)
    if spec is None:
        raise KeyError(f"没有这个绿灯 agent：{slug!r}（现有：{sorted(BY_SLUG)}）")
    return spec


def run(slug: str, payload: Any) -> dict:
    """跑一份绿灯 agent，返回它交付的那件成品。"""
    return get(slug).handler(payload)


# ---------------------------------------------------------------- 接入两种跑法
def make_local_api(skill: str, handler: Callable[[Any], dict],
                   in_agent: bool = True) -> Callable[[str, dict], dict]:
    """把一份绿灯 agent 接成 SDK `Node.serve(local_agent=...)` 认的本地服务。

    与 `docker/market_node.py` 的 `local_api` 同形：/invoke → 跑技能；
    `in_agent=False` 时把上游**真的连不上**这件事如实抛出去（给"最后一跳故意不通"
    那张卡用，见 `docker/market_node.py::_agent_behind`）。
    """
    def local_api(path: str, payload: dict) -> dict:
        if path != "/invoke":
            raise ValueError(f"unknown path {path}")
        if payload.get("skill") not in (None, "", skill):
            raise ValueError(f"unknown skill {payload.get('skill')!r}")
        body = payload.get("payload")
        if body is None:
            for part in (payload.get("message") or {}).get("parts", []):
                if "data" in part:
                    body = part["data"]
                    break
                if "text" in part:
                    body = part["text"]
                    break
        if len(text_of(body)) > 100_000:
            raise ValueError("测试服务最多接收 100000 字符")
        return handler(body)
    return local_api


class _GreenlightHandler(BaseHTTPRequestHandler):
    """把绿灯 agent 跑成一个标准 HTTP 上游：POST 任意路径 → 按 skill 分发。

    这一层是给 **a2n-node 的"供给"** 用的 —— 节点把某份供给 mount 到一个上游 URL，
    调用时它会 POST 过来。以前容器里那个上游指向一个没人听的端口（按设计不通），
    于是"绿的卡"也得是死的；现在绿的卡指向这个真的会回成品的服务。
    """

    table: dict[str, Spec] = {}

    def do_POST(self) -> None:                                  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._reply(400, {"error": "请求体不是 JSON"})
            return
        if not isinstance(body, dict):
            self._reply(400, {"error": "请求体必须是 JSON 对象"})
            return
        skill = body.get("skill")
        if not skill and len(self.table) == 1:
            skill = next(iter(self.table))
        spec = self.table.get(str(skill))
        if spec is None:
            self._reply(404, {"error": f"这台上游没有技能 {skill!r}",
                              "serves": sorted(self.table)})
            return
        payload = body.get("payload")
        if payload is None:
            for part in (body.get("message") or {}).get("parts", []):
                if "data" in part:
                    payload = part["data"]
                    break
                if "text" in part:
                    payload = part["text"]
                    break
        try:
            out = spec.handler(payload)
        except ValueError as e:
            # 参数错就**如实说参数错**，不要让调用方把它读成"上游坏了"
            self._reply(400, {"error": str(e), "skill": spec.skill})
            return
        self._reply(200, out)

    def _reply(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:                  # 别把访问日志灌进 stdout
        return


def serve_http(slugs: list[str] | tuple[str, ...], host: str = "127.0.0.1",
               port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """把若干绿灯 agent 起成一个 HTTP 上游，返回 `(server, endpoint_url)`。

    server 已在后台线程 `serve_forever`；调用方负责在收尾时 `server.shutdown()`。
    端口默认 0 = 让内核挑一个空闲端口，避免和别的演示服务抢。
    """
    table = {get(s).skill: get(s) for s in slugs}
    handler_cls = type("_GreenlightHandlerFor", (_GreenlightHandler,), {"table": table})
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="greenlight-http").start()
    return httpd, f"http://{host}:{httpd.server_address[1]}/invoke"
