"""货架：像上架商品一样把 agent 摆上 A2N —— 给自动化 agent 用。

hermes / codex / claude code 这类编码 agent 的接入口，三种姿势：

    from a2n_sdk import Client, shelf
    c = Client("http://127.0.0.1:8138", principal="my-agent")
    r = shelf(c, skills=["ocr-pro"], deployment={"region": "cn-east-2"},
              price={"ocr-pro": {"CNY": 3}})
    # → {"agent_id": "ag_…", "uid": "…", "card_hash": "…", "card": {…}}

    # 已有一份 card（别人给的/导出的）——整卡贴进来，等价管理台"解析填充"：
    from a2n_sdk import from_card
    r = from_card(c, open("card.json").read())

    # 改价 / 改算力（整卡更新，uid 不可变，card_hash 重算）：
    from a2n_sdk import update_card
    update_card(c, "ag_…", card)

命令行等价（codex / claude code 直接跑 shell）：
    python -m a2n_sdk shelf --platform http://127.0.0.1:8138 --principal me \
        --skill ocr-pro --region cn-east-2 --price CNY:call_count:3 --accept peer_account
    python -m a2n_sdk shelf --platform … --principal me --card card.json
    python -m a2n_sdk update-card --platform … --principal me --agent ag_… --card card.json

原则与管理台表单同源：
  可读名不承诺唯一，唯一性由 uid（UUID v4）承担，平台背书全网唯一；
  描述不填按技能与部署属地自动生成；只发行情不定价——价目是供给方自己愿意接受的价。
  能力是封装好的黑盒：GPU/显存等硬件实现细节不进卡（部署属地除外，数据合规用）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

DEFAULT_URL = "http://localhost:9000/a2a"
DEFAULT_VERSION = "1.0.0"
# 默认计量维度：与管理台上架表单的默认勾选保持一致（两条上架路径必须同形）。
# 平台不替供给方兜底声明 —— 节点自证，没声明就是没声明；
# 这里的默认值是 SDK 代表供给方填的，属于"我愿意按哪些维度计费"。
DEFAULT_METERING = ["call_count", "output_tokens"]


def gen_uid() -> str:
    """网络唯一标识：UUID v4。可自己生成带到多个平台用同一身份。"""
    return str(uuid.uuid4())


def gen_name() -> str:
    """可读名（不承诺唯一；全网唯一靠 uid）。"""
    return "agent-" + uuid.uuid4().hex[:4]


def auto_desc(skills: list[dict], deployment: dict | None = None) -> str:
    """按技能与部署属地自动生成描述（与管理台"自动生成描述"同一逻辑）。

    不写硬件细节：能力是黑盒，GPU 型号这类实现细节不是契约。
    """
    names = [s.get("name") or s["id"] for s in skills if s.get("id")]
    if not names:
        return ""
    region = (deployment or {}).get("region") or ""
    multi = f"{'、'.join(names)}等" if len(names) > 1 else f"{names[0]}（{skills[0]['id']}）"
    return (f"提供{multi}服务"
            + (f" · 部署于 {region}" if region else "")
            + "。按实际计量结算，支持按币种标价；验收通过才记账。")


def _norm_skills(skills: list[Any]) -> list[dict]:
    """["ocr-pro", {...}] → [{"id": "ocr-pro", ...}, {...}]，补默认 inputModes。"""
    out = []
    for s in skills:
        if isinstance(s, str):
            s = {"id": s}
        s = dict(s)
        if not s.get("id"):
            raise ValueError("每条技能都要有 id")
        s.setdefault("name", s["id"])
        s.setdefault("tags", [])
        s.setdefault("inputModes", ["application/json"])
        out.append(s)
    return out


def _norm_price(price: dict, skills: list[dict]) -> dict:
    """价目简写展开：{skill: {CUR: 每次单价}} → v2 完整结构。

    完整 v2 写法原样透传；简写按"每次（call_count）单价"展开。
    """
    first = skills[0]["id"] if skills else ""
    book: dict = {}
    for skill, by_cur in (price or {}).items():
        book[skill] = {}
        for cur, v in by_cur.items():
            if isinstance(v, dict):
                book[skill][cur] = v                     # v2 原样
            elif isinstance(v, (int, float)) and v > 0:  # 简写：每次单价
                book[skill][cur] = {"dimensions": [{"key": "call_count",
                                                    "amount": int(v), "per": 1}]}
            else:
                raise ValueError(f"价目不合法：{skill}/{cur}={v!r}")
    if not book and price:
        book = {first: price}  # {CUR: ...} 直接挂在第一条技能上
    return book


def build_card(*, skills: list[Any], name: str | None = None, desc: str | None = None,
               version: str = DEFAULT_VERSION, url: str = DEFAULT_URL,
               deployment: dict | None = None, sla: dict | None = None,
               accepts: list[str] | None = None, metering: list[str] | None = None,
               price: dict | None = None, uid: str | None = None,
               compute: dict | None = None) -> dict:
    """关键字段 → 完整 Agent Card。uid 必有（缺则自动生成 UUID v4）。

    deployment 是唯一的"运行环境"声明（{"region": ...}，数据驻留/就近调用用）；
    compute 形参仅兼容旧调用，硬件字段不再收进卡。
    """
    sk = _norm_skills(skills)
    if not sk:
        raise ValueError("至少一条技能")
    dep = dict(deployment or {})
    if compute:  # 兼容：老调用传 compute，只收 region
        dep.setdefault("region", compute.get("region"))
    card: dict[str, Any] = {
        "name": name or gen_name(),
        "description": desc or auto_desc(sk, dep),
        "version": version,
        "url": url,
        "skills": sk,
        "x-a2n": {"uid": uid or gen_uid()},
    }
    if dep.get("region"):
        card["x-a2n"]["deployment"] = {"region": dep["region"]}
    if sla:
        card["x-a2n"]["sla"] = sla
    acc = list(accepts or [])
    if acc:
        card["accepts"] = acc   # "peer_account" / "direct_pay:<渠道>" / "x402"
    dims = list(metering if metering is not None else DEFAULT_METERING)
    if dims:
        card["x-a2n"]["metering"] = {"dimensions": [
            {"key": k, "unit": k, "verifiable": k != "gpu_seconds"} for k in dims]}
    book = _norm_price(price, sk)
    if book:
        card["x-a2n"]["price_book"] = book
    return card


def shelf(client: Any, *, skills: list[Any], name: str | None = None,
          desc: str | None = None, version: str = DEFAULT_VERSION,
          url: str = DEFAULT_URL, deployment: dict | None = None,
          sla: dict | None = None, accepts: list[str] | None = None,
          metering: list[str] | None = None, price: dict | None = None,
          uid: str | None = None, visibility: str = "public",
          discover_limit: int | None = None,
          compute: dict | None = None) -> dict:
    """一把上架：关键字段 → 完整卡 → 注册。返回 agent_id / uid / card_hash / card。

    discover_limit：允许被几个使用者发现（None = 不限）。它是分发策略，
    走请求参数而不是写进卡（平台改写卡会让自签失效）；上架后可随时改。
    """
    card = build_card(skills=skills, name=name, desc=desc, version=version, url=url,
                      deployment=deployment, compute=compute, sla=sla, accepts=accepts,
                      metering=metering, price=price, uid=uid)
    r = client.register(card, visibility, discover_limit)
    return {"agent_id": r["agent_id"], "uid": card["x-a2n"]["uid"],
            "card_hash": r.get("card_hash"), "card": card,
            "status": r.get("status"), "kya_grade": r.get("kya_grade"),
            "discover_limit": discover_limit}


def from_card(client: Any, card: dict | str, visibility: str = "public",
              discover_limit: int | None = None) -> dict:
    """整卡上架（等价管理台"解析填充"）：贴一份现成 card，缺 uid 自动补。

    与 ``shelf`` 路径同形：若卡里缺 metering，按 ``DEFAULT_METERING``
    兜底并标 ``sdk_default=true``（让数据归属可追溯）—— 控制台表单
    路径与 SDK 整卡路径，两条上架入口在网络上看着必须一样。
    """
    if isinstance(card, str):
        card = json.loads(card)
    ext = card.setdefault("x-a2n", {})
    if not ext.get("uid"):
        ext["uid"] = gen_uid()
    # metering 兜底：与 build_card 路径同形；标记便于审计"哪些卡用了 SDK 默认"
    metering = ext.get("metering") or {}
    if not metering.get("dimensions"):
        ext["metering"] = {"dimensions": [{"key": k} for k in DEFAULT_METERING],
                           "sdk_default": True}
    r = client.register(card, visibility, discover_limit)
    return {"agent_id": r["agent_id"], "uid": ext["uid"],
            "card_hash": r.get("card_hash"), "card": card,
            "status": r.get("status"), "kya_grade": r.get("kya_grade"),
            "discover_limit": discover_limit}


def update_card(client: Any, agent_id: str, card: dict | str) -> dict:
    """整卡更新：改价 / 改部署属地 / 改结算方式。uid 不可变；card_hash 重算。"""
    if isinstance(card, str):
        card = json.loads(card)
    return client.update_card(agent_id, card)
