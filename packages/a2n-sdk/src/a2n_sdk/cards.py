"""Node Agent Card construction and structural rules; no registry or storage."""
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
            elif isinstance(v, int) and not isinstance(v, bool) and v >= 0:
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
    validate_card(card)
    return card



def validate_card(card: dict) -> None:
    """卡结构校验：不管从管理台、SDK 还是协议入口上架，卡都是同一种形状。

    只拒绝"形状性违规"（缺字段/格式错/明显不合逻辑），不做业务判断——
    技能是否有人要、价格是否合理，是市场的视线，不是注册表的。
    """
    if not isinstance(card, dict):
        raise ValueError("Agent Card 必须是 JSON 对象")
    if not isinstance(card.get("name"), str) or not card["name"].strip():
        raise ValueError("name 必须是非空字符串")
    for field_name in ("name", "skills"):
        if field_name not in card:
            raise ValueError(f"Agent Card 缺少必填字段: {field_name}")
    # url 可空：relay/pull 模式节点没有自有公网入口（全程出站连接），
    # 对外调用一律走 A2N 门牌号 /v1/relay/{id}（地址投影）。给了值就必须是 http(s)。
    url = card.get("url")
    if url and not str(url).startswith(("http://", "https://")):
        raise ValueError(f"url 必须是 http(s) 地址或留空（由本节点生成公共投影）：{url!r}")
    skills = card.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("skills 必须是非空数组")
    for s in skills:
        if not isinstance(s, dict) or not str(s.get("id") or "").strip():
            raise ValueError(f"每条技能都要有非空 id：{s!r}")
    ext = card.get("x-a2n") or {}
    if not isinstance(ext, dict):
        raise ValueError("x-a2n 必须是对象")
    # 卡上不许声明"我不走免费期"：**任何想被发现的 agent，前 10 次完成调用不计费**
    # （目的在使用方 —— 谁都能先看真实案例再决定付不付钱）。这里**直接拒**而不是
    # 静默忽略：忽略等于让供给方以为自己退出了，等到被计费才发现，那是欺骗。
    # 部署级开关 A2N_TRIAL_DEFAULT 是测试/演示用的，不是供给方的出口。
    if ext.get("trial") is False:
        raise ValueError(
            "不允许在卡上退出免费期（x-a2n.trial=false）：想被网络发现，"
            "前 10 次完成的调用必须免费服务，额度用尽毕业之后才可收费")
    # 卡里可以声明自己的上架名额（这份是供给方自己签的，签名域包含它）。
    # 平台**不会**替供给方往卡里写这一项 —— 改写卡会让自签当场失效，
    # 所以上架名额的落库位置是 agents 表那一列，不是 card_json。
    dl = ext.get("discover_limit")
    if dl is not None and (isinstance(dl, bool) or not isinstance(dl, int) or dl < 0):
        raise ValueError(f"x-a2n.discover_limit 必须是非负整数（0=不限）：{dl!r}")
    if ext.get("uid"):
        try:
            uuid.UUID(str(ext["uid"]))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"uid 必须是 UUID 格式：{ext['uid']!r}")
    book = ext.get("price_book") or card.get("price_book")
    if book:
        if not isinstance(book, dict):
            raise ValueError("price_book 必须是 {技能: {币种: 条目}} 结构")
        for skill, by_cur in book.items():
            if not isinstance(by_cur, dict):
                raise ValueError(f"price_book[{skill!r}] 必须是 {{币种: 条目}}")
            for cur, spec in by_cur.items():
                dims = (spec or {}).get("dimensions") if isinstance(spec, dict) else spec
                if not isinstance(dims, list) or not dims:
                    raise ValueError(f"price_book[{skill!r}][{cur}] 缺 dimensions")
                for e in dims:
                    if not isinstance(e, dict) or not e.get("key"):
                        raise ValueError(f"价目条目缺 key：{e!r}")
                    amount = e.get("amount")
                    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                        raise ValueError(f"价目条目 amount 必须为非负整数（0 为免费）：{e!r}")
                    per = e.get("per", 1)
                    if isinstance(per, bool) or not isinstance(per, int) or per <= 0:
                        raise ValueError(f"价目条目 per 必须为正整数：{e!r}")
    metering = (ext.get("metering") or {}).get("dimensions")
    if metering:
        for d in metering:
            if not isinstance(d, dict) or not str(d.get("key") or "").strip():
                raise ValueError(f"计量维度缺 key：{d!r}")
