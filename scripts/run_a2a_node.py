"""A2A 协议层节点：本地服务额外挂 /invoke，承接 JSON-RPC message/send 的中继转发。

与 run_node.py 的区别：run_node 只演示平台→隧道的通用中继（/echo）；
本节点是**标准 A2A 客户端能直接调用**的节点——平台的 /a2a/{agent_id}
(message/send) 会把消息经隧道转发到本机 /invoke，就地跑技能并回包。

本文件现在有**两个入口**：

* ``python scripts/run_a2a_node.py`` —— 起**一个**节点（一把钥匙、一张卡），
  适合单机调试与测试。档位由 A2N_ROLE 选。
* ``scripts/run_local_node.py`` —— 起**本机节点**：一把钥匙、四张卡
  （四个档位一起上架），演示"一个节点就是一个供给方"。

所以建卡逻辑抽成了 ``build(role, identity)``：**身份由调用方给**，
一个进程要摆几张卡就调几次。档位之间只差卡面（价目/结算方式/验收模板）。

档位（2026-09-28 换血：**摆行业专家成品，不再摆 OCR 单点工具**）
--------------------------------------------------------------
四条**结算通道**各配一份"能交付成品"的行业专家 agent —— 通道照旧，
内容换成 VISION 第一承重墙说的那种"一份能用的东西"（不是"一次 OCR 调用"）：

    charging  简历优化包    （CNY+USDC 两条挂牌，对等账户 + 直付渠道）
    free      行程规划单    （不声明 accepts、不标价）
    x402      课程大纲      （只挂 USDC，只收 x402 微支付）
    trial     菜单定价表    （前 10 次完成免费，之后毕业才收费；验收模板还是草稿版）

成品逻辑（十个 handler）在 ``a2n_sdk.greenlight``（纯标准库、单一事实源）——
本文件**只声明"这一档怎么收钱"**，不在这里再写一遍成品怎么拼。

为什么换：用户 2026-09-28 原话「以前好多案例是 ocr。但是我理解的 agent 更多是做产品的
agent 或行业专家 agent」。四档保留是为了继续演示四条结算通道（收费/免费/x402/试用中），
换的只是**交付什么**。

注意：档位内容换过之后**第一次起要清库**（``sim_start.sh fresh``）—— 上架的 uid 由
"节点 did + 档位"派生，内容换了 uid 就换，保留旧库会让发现页上同时留着旧的 OCR 卡。

环境变量：
    A2N_ROLE        预设档位（见上，默认 charging；A2N_FREE=1 等价 free）
    A2N_LOCAL_PORT  本地服务端口（默认 9102）—— 只在单节点入口用
    A2N_PRINCIPAL   上架主体，**默认 = 节点自己的 did**（一个节点一个身份；
                    设了才覆盖 —— 那是给测试造"两个主体"用的，不是常态）
    A2N_SEATS       允许被发现的数量（0/未设 = 不限）：同时最多几个使用者能发现它
    A2N_FREE=1      强制免费（等价 free 档，向后兼容）
    A2N_SKILL       覆盖技能 id（默认 = 本档交付物的技能 id；冒烟靠它找到节点，
                    改前先改冒烟）
    A2N_KEYFILE     身份密钥库（默认 data/keys/a2a_node_<role>.json，不存在就生成）
    A2N_NODE_NAME / A2N_REGION / A2N_LATENCY / A2N_PRICE / A2N_PRICE_CUR
                    逐项覆盖预设（都填 ASCII，中文描述写在预设表里）

关于身份：节点有自己的 ed25519 钥匙（DID = 公钥指纹，不需要谁分配）。它做三件事：
  ① 用这把钥匙**签整张卡**（签名域 = 整卡去掉 sig），把 did/pub/sig 写进
     x-a2n.sovereign —— 这就是"卡自证"：平台发现路与注册路都验它，
     验不过的卡进不了"可直接调用"（P2 卡片自证闸）；
  ② 每次交付用同一把钥匙签一份计量（task_id + node_id + 计费口径），随回包上行；
  ③ 卡上自带 uid（uid 属于签名域，平台补写会让签名失效）。
没有它，计量签名栏永远是"未签名"（宁可如实说未签名，也不塞假签名），
卡也只能是"未自证"。钥匙持久在本地文件里而不是每次进程重建：节点重启不该换一个人。

**身份与"上架主体"是同一件事**（2026-09-20 用户拍板「一个节点就一个身份」）：
节点的 did 就是它对外被辨识的那个 id，不再另造 `acct:bob` 这类账号 ——
一个供给方一个身份，别让"谁在卖"出现两个答案。

启动：
    A2N_DB=data/e2e_a2a.db ./.venv/Scripts/python.exe scripts/run_a2a_node.py
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from a2n_custodian.media import by_currency
from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Node
from a2n_sdk.greenlight import (FREE_SUFFIX, PAID_SUFFIX,
                                get as greenlight_spec, make_local_api)

FREE_FORCED = os.environ.get("A2N_FREE") == "1"


# 精度的事实源在持牌层 ``a2n_custodian.media``（"钱以什么形态存在"只在那里回答），
# 这里**不许**再抄一张 {币种: 小数位} 表 —— 抄一份就多一个会漂移的真相。
def _minor(price: str, cur: str) -> int:
    """主单位 → 最小单位整数。**不是整数倍就报错，绝不四舍五入。**

    挂牌价必须能用该币种的最小单位整数表示：CNY 的最小单位是分，
    所以 0.015 CNY 根本不存在 —— 悄悄 round 成 0.02 等于背着供给方改了价。
    """
    media = by_currency(cur)
    if not media:
        raise ValueError(f"未注册的币种：{cur}（媒介注册表里没有它）")
    exp = int(media[0]["exponent"])
    minor = Decimal(price) * (10 ** exp)
    if minor != minor.to_integral_value():
        raise ValueError(
            f"{price} {cur} 不是该币种最小单位（10^-{exp}）的整数倍 —— "
            f"挂牌价必须能用最小单位整数表示，否则会被悄悄改价")
    return int(minor)


# ---------------------------------------------------------------- 验收模板
# ``required_fields`` 必须与 handler 交付的字段**完全一致**：a2n_acceptance.template
# 把"多出的字段"也记成结构不符（d_struct = (缺 + 多) / 应有）。所以这份清单不是随手
# 写的 —— tests/test_greenlight.py 会拿真实交付去核对它，漂了就红。
_RESUME_FIELDS = ("cover_letter", "deliverable", "delivered", "highlights",
                  "interview_qa", "target")
_COURSE_FIELDS = ("assignments", "course", "deliverable", "delivered",
                  "objectives", "sessions")
_MENU_FIELDS = ("combo", "deliverable", "delivered", "items", "rule")


def _template(version: str, fields: tuple[str, ...], content: list[dict],
              extra_missing: tuple[str, ...] = ()) -> dict:
    """一份验收模板。``extra_missing`` = 草稿版里**声明了但实现没跟上**的字段 ——
    偏差就是这么来的：硬指标的价值恰恰在这，它会如实说出"你声明了、却没交付"。"""
    return {"version": version, "required_fields": sorted(set(fields) | set(extra_missing)),
            "required_content": list(content)}


_RESUME_TEMPLATE = _template("1.0", _RESUME_FIELDS, [
    {"key": "has_highlights", "path": "highlights", "mode": "present"},
    {"key": "cover_letter_present", "path": "cover_letter", "mode": "present"}])
_COURSE_TEMPLATE = _template("1.0", _COURSE_FIELDS, [
    {"key": "has_sessions", "path": "sessions", "mode": "present"},
    {"key": "has_objectives", "path": "objectives", "mode": "present"}])
# 草稿模板：声明了 ingredient_breakdown（实现还没跟上）→ 偏差就是这么来的。
_MENU_TEMPLATE_DRAFT = _template("0.9-draft", _MENU_FIELDS, [
    {"key": "has_items", "path": "items", "mode": "present"}],
    extra_missing=("ingredient_breakdown",))

# ---------------------------------------------------------------- 档位预设
# 展示名**只写交付什么**，不带「· 专业版 / · 免费版」这类版本词：收不收费是**数据**，
# 价格列与状态列已经如实说了，写进名字等于把可变事实刻成标识（2026-09-19 用户提的，
# tests/test_market_demo_agents.py 有命名守卫）。
PRESETS = {
    "charging": {
        "slug": "resume-polish", "name": "简历优化包", "region": "cn-east-2",
        "latency": 5000, "availability": 0.95, "concurrent": 4, "sleep": 0.3,
        "prices": {"CNY": "0.60", "USDC": "0.085"},
        "accepts": ["peer_account", "direct_pay:alipay"],
        "tags": ["招聘", "简历", "求职"],
        "template": _RESUME_TEMPLATE,
    },
    "free": {
        "slug": "trip-plan", "name": "行程规划单", "region": "cn-north-1",
        "latency": 8000, "availability": 0.90, "concurrent": 2, "sleep": 0.2,
        # 允许被发现的数量 = 3：免费档最容易被薅，用它演示"名额间接限制同时使用人数"
        # （满员后新使用者搜不到它，已在用的不受影响；闲置 30 分钟自动释放一个）。
        "seats": 3,
        "prices": {}, "accepts": [], "tags": ["旅行", "行程", "规划"],
        # **不声明**验收模板 → 诚实显示"未声明验收模板"，而不是伪造一个 0 偏差。
        "template": None,
    },
    "x402": {
        "slug": "course-outline", "name": "课程大纲", "region": "ap-southeast-1",
        "latency": 1500, "availability": 0.99, "concurrent": 8, "sleep": 0.05,
        "prices": {"USDC": "0.09"}, "accepts": ["x402"],
        "tags": ["教育", "课程", "即付"],
        "template": _COURSE_TEMPLATE,
    },
    # 第四档：**新上架、还在试用期内**。前三档都刻意退出了试用 —— 没有这一档，
    # 控制台上的"试用中 N/10 · 免费"徽标就没有真身可看，试用/毕业这条链演示不出来。
    "trial": {
        "slug": "menu-pricing", "name": "菜单定价表", "region": "cn-south-1",
        "latency": 3000, "availability": 0.92, "concurrent": 3, "sleep": 0.25,
        "prices": {"CNY": "0.40", "USDC": "0.055"},
        "accepts": ["peer_account", "direct_pay:alipay"],
        "tags": ["餐饮", "定价", "菜单"],
        "template": _MENU_TEMPLATE_DRAFT,
    },
}
ROLES = list(PRESETS)


def load_identity(path: str | Path) -> Identity:
    """节点的钥匙：公钥即身份，私钥只在本机。已有就复用，没有就生成。

    持久化而不是每进程新建 —— 节点重启不该换一个人（DID 的全部意义就在这）。
    一个节点一份钥匙，**不是一张卡一份**：卡可以有很多张，身份只有一个。
    """
    path = Path(path)
    if path.exists():
        return Identity.load(path)
    ident = Identity.generate()
    ident.save(path)
    # 进度走 stderr：stdout 是**数据**（`--print-did` 靠它喂给调用方），
    # 混进一行日志会让调用方读到一个不是 did 的字符串。
    print(f"[a2n] 新身份已生成并落盘 {path}（{ident.did}）", file=sys.stderr)
    return ident


def default_keyfile(role: str) -> Path:
    return Path(os.environ.get("A2N_KEYFILE") or f"data/keys/a2a_node_{role}.json")


def build(role: str, identity: Identity) -> dict:
    """建一个档位的**一张卡 + 处理器 + 本机服务**。身份由调用方传入。

    返回 dict（role / slug / name / card / handlers / local_api / attest_fn /
    sleep / prices / seats），调用方拿去起 ``Node``。一个进程想摆几张卡就调几次 ——
    这正是"一个节点上架多个 agent"的形状。
    """
    if role not in PRESETS:
        raise SystemExit(f"A2N_ROLE 只认 {sorted(PRESETS)}，收到 {role!r}")
    P = dict(PRESETS[role])
    spec = greenlight_spec(P["slug"])

    NAME = os.environ.get("A2N_NODE_NAME") or spec.name
    REGION = os.environ.get("A2N_REGION") or P["region"]
    LATENCY = int(os.environ.get("A2N_LATENCY") or P["latency"])
    SKILL = os.environ.get("A2N_SKILL") or spec.skill
    # 挂牌价：可以同时挂多个币种（"汇率即价格"——两条独立挂牌，平台不换算、不跨币种相加）。
    # A2N_PRICE / A2N_PRICE_CUR 是**单币种覆盖**：一旦给了，整张价目就换成它，
    # 免得出现"改了一个币种、另一个还留着旧价"这种半新半旧的状态。
    PRICES = {cur: val for cur, val in (P.get("prices") or {}).items() if cur and val}
    if os.environ.get("A2N_PRICE") or os.environ.get("A2N_PRICE_CUR"):
        PRICES = {os.environ.get("A2N_PRICE_CUR") or next(iter(PRICES), "CNY"):
                  os.environ.get("A2N_PRICE", "")}
        PRICES = {cur: val for cur, val in PRICES.items() if cur and val}
    # 允许被发现的数量（0 / 未设 = 不限）：上架时声明的**分发策略**，不是能力声明。
    # 由平台执行（按使用者占名额、闲置自动释放），所以走注册请求参数、不写进卡 ——
    # 写进卡会多一个"平台会改"的字段，而平台改写卡会让签名当场失效。
    SEATS = int(os.environ.get("A2N_SEATS") or (P.get("seats") or 0))
    if FREE_FORCED:
        PRICES = {}
        P["accepts"] = []

    X_A2N = {
        "deployment": {"region": REGION},
        # uid 由 **did + 档位** 派生（不是 uuid4）：一个节点可以摆多张卡，
        # 每张卡一个稳定 uid；节点重启沿用同一条上架，而不是又插一条新的
        # —— 随机 uid 会让发现页上"同一份服务"越重启越多。
        "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity.did}/local-node/{role}")),
        # 卡上自证：这个 agent 的钥匙是哪把。平台验计量签名时按**卡上声明的公钥**
        # 来认，所以这一项必须与 attest_fn 用的那把钥匙同源（都来自 identity）。
        "sovereign": {"did": identity.did, "pub": pub_b64(identity.pub_raw)},
        "sla": {"max_latency_ms": LATENCY, "availability_target": P["availability"],
                "max_concurrent": P["concurrent"]},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
    }
    if PRICES:
        # v2 价目表：价目事实只在这一处（v1 price_hint 已不再写入）。
        # 多币种 = 多条独立挂牌，各按自己的币种精度换算成最小单位，互不换算。
        X_A2N["price_book"] = {SKILL: {
            cur: {"dimensions": [{"key": "call_count", "amount": _minor(val, cur), "per": 1}]}
            for cur, val in PRICES.items()}}
    if P.get("template"):
        X_A2N["acceptance_template"] = P["template"]

    CARD = {
        "name": NAME,
        "description": spec.description + (PAID_SUFFIX if PRICES else FREE_SUFFIX),
        "version": "1.0.0",
        "url": None,
        "skills": [{"id": SKILL, "name": spec.skill_name, "description": spec.description,
                    "tags": list(P.get("tags") or spec.tags),
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["application/json"]}],
        "x-a2n": X_A2N,
    }
    ACCEPTS = [s.strip() for s in os.environ.get("A2N_ACCEPTS", "").split(",") if s.strip()]
    if ACCEPTS:
        CARD["accepts"] = ACCEPTS
    elif P["accepts"]:
        CARD["accepts"] = list(P["accepts"])

    # 整卡签一次名（必须在**所有**字段都写完、且此后不再改动之后）：
    # 签名域是整张卡去掉 sig —— 早签一步，后面补的 accepts / price_book 就不在签名里，
    # 那张卡"验签通过"却仍可被人改字段，自证就成了摆设。
    X_A2N["sovereign"]["sig"] = identity.sign(card_body(CARD))

    # 本地服务（平台中继转发进来的所有 POST 都在这里落地）走 SDK 那条共享实现：
    # /invoke → 跑技能，返回体就是 A2A Task 的 artifact data（不包信封）；
    # 处理不了就抛错，平台把 HTTP 500/400 如实映射成 failed Task，而不是假装完成。
    local_api = make_local_api(SKILL, spec.handler)

    def attest_fn(task_id: str, node_id: str, dims: dict):
        """计量连署：用节点自己的钥匙签"任务号 + 节点号 + 计费口径"。"""
        return sign_metering(identity, task_id=task_id, node_id=node_id, dims=dims)

    return {
        "role": role, "slug": spec.slug, "name": NAME, "card": CARD,
        "handlers": {SKILL: spec.handler},
        "local_api": local_api, "attest_fn": attest_fn,
        "sleep": P["sleep"], "prices": PRICES, "seats": SEATS,
    }


def principal_for(identity: Identity, override: str | None = None) -> str:
    """上架主体 = 节点自己的 did（一个节点一个身份）。

    设置 A2N_PRINCIPAL 才覆盖 —— 那是给测试造"两个主体"用的，不是常态。
    """
    return override or identity.did


def start(built: dict, identity: Identity, *, local_port: int,
          base_url: str = "http://127.0.0.1:18787",
          principal: str | None = None) -> Node:
    """把 build() 的产物起成一个常驻节点（起完返回，由调用方决定阻塞与否）。"""
    node = Node(built["card"], built["handlers"],
                principal=principal_for(identity, principal),
                base_url=base_url,
                discover_limit=built["seats"] or None,
                attest_fn=built["attest_fn"])
    price_txt = " / ".join(f"{v} {c}/次" for c, v in built["prices"].items()) or "免费"
    seat_txt = f" · 名额 {built['seats']}" if built["seats"] else ""
    print(f"[a2n] 上架 {built['name']}（{built['role']} · {price_txt}{seat_txt} · "
          f"本地服务 {local_port} · 主体 {node.client.principal}）")
    return node


if __name__ == "__main__":
    ROLE = os.environ.get("A2N_ROLE", "free" if FREE_FORCED else "charging")
    IDENT = load_identity(default_keyfile(ROLE))
    BUILT = build(ROLE, IDENT)
    LOCAL_PORT = int(os.environ.get("A2N_LOCAL_PORT", "9102"))
    NODE = start(BUILT, IDENT, local_port=LOCAL_PORT,
                 base_url=os.environ.get("A2N_BASE", "http://127.0.0.1:18787"),
                 principal=os.environ.get("A2N_PRINCIPAL"))
    print(f"[a2n] A2A 节点启动：{BUILT['name']}（{ROLE} · 本地服务 {LOCAL_PORT} · Ctrl+C 退出）")
    NODE.serve(console=False, local_agent=(LOCAL_PORT, BUILT["local_api"]))


