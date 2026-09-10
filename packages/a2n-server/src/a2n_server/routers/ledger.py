"""账户与流水路由：主体视角的钱账流水 + 媒介/币种清单。

流水**不建新表**（那是第二份事实，第二份事实迟早和第一份打架），
只读聚合视图 v_account_ledger：来源是 pay_charges（直付/x402 成交）
与 statements（对等账单），每笔拆付/收两行，币种如实呈现。
金额一律给整数最小单位（amount_minor）+ 币种 + 指数，展示交给前端。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from a2n_custodian import list_media, money
from a2n_custodian.media import UnknownMedium
from a2n_store import conn

router = APIRouter(prefix="/v1", tags=["ledger"])


@router.get("/ledger")
def account_ledger(principal: str = Header(alias="X-Principal"),
                   currency: str | None = Query(None, description="按币种过滤，如 CNY/USDC"),
                   direction: str | None = Query(None, description="in 收款 / out 付款"),
                   limit: int = Query(100, le=500)) -> dict:
    """我的流水（只读聚合视图）。老库没有视图时给出明确指引，不静默造假。"""
    q = ("SELECT direction, counterparty, channel, kind, ref_id, currency,"
         " amount_minor, amount_fen, state, note, happened_at"
         " FROM v_account_ledger WHERE owner_id=?")
    args: list = [principal]
    if currency:
        q += " AND currency=?"
        args.append(currency.upper())
    if direction:
        if direction not in {"in", "out"}:
            raise HTTPException(400, "direction 只能是 in / out")
        q += " AND direction=?"
        args.append(direction)
    q += " ORDER BY happened_at DESC LIMIT ?"
    args.append(limit)
    try:
        rows = conn().execute(q, args).fetchall()
    except Exception as e:  # noqa: BLE001 - 视图缺失等结构性问题如实上报
        raise HTTPException(500, f"流水视图不可用（需重新 init_db 建视图）：{e}")

    items = []
    for r in rows:
        d = dict(r)
        cur = d["currency"]
        minor = int(d["amount_minor"] if d["amount_minor"] is not None else d["amount_fen"])
        try:
            m = money(minor, {"CNY": "channel_pay", "USDC": "stablecoin",
                              "POINT": "points"}.get(cur, "channel_pay"))
            display = m["display"]
        except UnknownMedium:   # 只兜"媒介没注册"这一种：别的错不掩盖
            display = f"{minor} {cur}"
        items.append({**d, "amount_minor": minor, "display": display})

    # 按币种小计（只统计到账/出账生效的行，状态如实随行返回）
    by_cur: dict[str, dict] = {}
    for it in items:
        b = by_cur.setdefault(it["currency"], {"in": 0, "out": 0})
        b[it["direction"]] += it["amount_minor"]
    return {"owner_id": principal, "items": items, "totals_by_currency": by_cur}


@router.get("/media")
def media_catalogue() -> dict:
    """当前系统支持的全部媒介与币种（控制台下拉框数据源）。

    加一种支付方式 = 持牌层注册一个媒介 —— 这个接口立刻能看到，业务代码零改动。
    """
    return {"media": list_media()}
