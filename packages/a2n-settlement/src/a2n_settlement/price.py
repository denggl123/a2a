"""价目表 v2 与多币种计价：agent 按币种分开标价，平台只算不定价。

**平台在这里唯一做的事是算术** —— 把 agent 自己声明的单价，套到双方都认的
计量上，算出这一笔的金额。它不做三件事：不决定单价（agent 自报）、不做
汇率换算（汇率即价格）、不因为"看起来太便宜/太贵"而调整结果。

价目表形状（Card `x-a2n.price_book`）：

    "price_book": {
      "ocr-pro": {
        "CNY":  {"dimensions": [{"key": "call_count", "amount": 3, "per": 1},
                                {"key": "output_tokens", "amount": 1, "per": 1000}]},
        "USDC": {"dimensions": [{"key": "call_count", "amount": 5, "per": 1}]}
      }
    }

    key    计量维度，必须已注册且可计费（a2n_settlement.dims）
    amount 该维度单价，单位是该币种的**最小单位整数**（CNY 用分，USDC 用 10⁻⁶）
    per    每多少用量收一次钱（token 类按千收就写 1000），默认 1

三条算账纪律：
  1. **多维度叠加**：声明了几个维度就按几个维度分别算，再求和。
  2. **先精确累加，最后一次性取整**：逐项取整会产生累积误差，
     两笔一模一样的调用可能算出不同的钱 —— 那是审计事故。
  3. **不得超过成交时的预算上限**：成交后节点改价无效（快照 + 封顶）。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from a2n_custodian.media import DEFAULT_CURRENCY   # 币种默认口径的唯一来源

from .dims import is_billable

LEGACY_DIM = "call_count"


def _card_ext(card: dict) -> dict:
    return (card or {}).get("x-a2n") or {}


def price_book(card: dict) -> dict[str, dict[str, list[dict]]]:
    """读规范化价目表：{skill: {currency: [条目]}}。

    v2（price_book）优先；没有则把 v1 的 price_hint 折算成 v2 形状，
    保证老 card 不改一行就能继续跑（口径沿用 v1 的"分/次"）。
    """
    ext = _card_ext(card)
    book = ext.get("price_book") or (card or {}).get("price_book") or {}
    if book:
        return _normalize(book)
    hint = ext.get("price_hint") or (card or {}).get("price_hint") or {}
    return _from_v1(hint)


def _normalize(book: dict) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {}
    for skill, by_cur in (book or {}).items():
        if not isinstance(by_cur, dict):
            continue
        per_cur: dict[str, list[dict]] = {}
        for currency, spec in by_cur.items():
            entries = (spec or {}).get("dimensions") if isinstance(spec, dict) else spec
            if not entries:
                continue
            cleaned = []
            for e in entries:
                if not isinstance(e, dict) or not e.get("key"):
                    continue
                cleaned.append({"key": e["key"],
                                "amount": int(e.get("amount") or 0),
                                "per": int(e.get("per") or 1) or 1})
            if cleaned:
                per_cur[str(currency).upper()] = cleaned
        if per_cur:
            out[skill] = per_cur
    return out


def _from_v1(hint: dict) -> dict[str, dict[str, list[dict]]]:
    """v1: {"ocr-pro": {"amount": 1, "unit": "point_per_call"}} → 按次、CNY。"""
    out: dict[str, dict[str, list[dict]]] = {}
    for skill, spec in (hint or {}).items():
        if not isinstance(spec, dict):
            continue
        amount = int(spec.get("amount") or 0)
        out[skill] = {DEFAULT_CURRENCY: [{"key": LEGACY_DIM, "amount": amount, "per": 1}]}
    return out


def supported_currencies(card: dict, skill: str = "") -> list[str]:
    """这个 agent（或这个技能）接受哪些币种。没标价 = 免费，不列币种。"""
    book = price_book(card)
    if skill:
        return sorted((book.get(skill) or {}).keys())
    curs: set[str] = set()
    for by_cur in book.values():
        curs.update(by_cur.keys())
    return sorted(curs)


def is_free(card: dict, skill: str = "") -> bool:
    """没价目表 = 免费。平台绝不替没标价的供给方编一个价（铁律一）。"""
    return not supported_currencies(card, skill)


def entries_of(card: dict, skill: str, currency: str) -> list[dict]:
    """取某技能某币价的条目；不支持该币种返回空列表（由门禁去拒绝）。"""
    book = price_book(card)
    return (book.get(skill) or {}).get(str(currency or "").upper()) or []


def snapshot(card: dict, skill: str, currency: str) -> dict:
    """成交时点的单价快照：币种 + 计量维度 + 单价 + per。

    事后 agent 改价无效 —— 判定一律以这份快照为准（配合 card_hash 双重锁死）。
    """
    return {"skill": skill, "currency": str(currency or "").upper(),
            "entries": list(entries_of(card, skill, currency))}


def quote(entries: list[dict], dims: dict, budget_minor: int | None = None) -> dict:
    """按价目条目与计量算出金额（最小单位整数）。

    返回明细 lines，让"这笔钱是怎么来的"可逐项复核 —— 争议时双方看的
    是同一张算式，而不是各自的一句"应该是这个数"。
    """
    total = Decimal(0)
    lines = []
    for e in entries or []:
        key = e["key"]
        if not is_billable(key):
            continue                      # 不可计费维度不进金额（纪律，不静默放过）
        qty = Decimal(str(float((dims or {}).get(key, 0) or 0)))
        if qty <= 0:
            continue
        per = Decimal(int(e.get("per") or 1) or 1)
        unit = Decimal(int(e.get("amount") or 0))
        amount = (qty / per) * unit
        total += amount
        lines.append({"key": key, "qty": float(qty), "per": int(per),
                      "unit_price_minor": int(unit),
                      "amount_exact": str(amount)})
    amount_minor = int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    capped = False
    if budget_minor is not None and budget_minor >= 0 and amount_minor > budget_minor:
        amount_minor = int(budget_minor)
        capped = True
    return {"amount_minor": amount_minor, "lines": lines, "capped": capped}


def quote_card(card: dict, skill: str, currency: str, dims: dict,
               budget_minor: int | None = None) -> dict:
    """一步到位：从 card 取条目再算钱。"""
    out = quote(entries_of(card, skill, currency), dims, budget_minor)
    out["currency"] = str(currency or "").upper()
    out["entries"] = list(entries_of(card, skill, currency))
    return out


def unit_price_of(card: dict, skill: str, currency: str) -> int:
    """该币种下第一个维度的单价（预算上限与展示用）。"""
    for e in entries_of(card, skill, currency):
        return int(e.get("amount") or 0)
    # 未指定币种时退回第一个有价的币种（展示用，不用于成交）
    for cur in supported_currencies(card, skill):
        for e in entries_of(card, skill, cur):
            return int(e.get("amount") or 0)
    return 0


def amount_of(dims: dict, unit_prices: dict) -> int:
    """兼容旧签名：{维度: 单价} × 计量 → 最小单位整数（v1 路径沿用）。"""
    from .dims import is_billable as _billable
    total = Decimal(0)
    for dim, price in (unit_prices or {}).items():
        if _billable(dim):
            total += Decimal(str(float((dims or {}).get(dim, 0) or 0))) * Decimal(str(float(price)))
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
