"""直付结算路由：支付方式登记 + 成交合约核验。

两件事，一一对应"集成标准支付协议"的两半：
  1. **我加个支付渠道**：POST /v1/pay-methods 登记支付渠道（A2N 不碰钱不验
     真伪、不认识品牌——channel 是自由字符串，品牌知识归持牌托管层）。
  2. **调用后合约信息对不对**：GET /v1/pay-charges/{charge_id} —— 一张直付
     成交凭证 = 单价快照（成交时点冻结）+ 计量 + 金额 + AP2 授权链
     （intent→cart→payment）+ 任务关联。这个端点把全部字段重新机器核验一遍，
     任何一处对不上都如实报告，不做"看起来对"的美化。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from a2n_account import (DIRECT_PAY, PEER_ACCOUNT, accepts_of_card, direct_channels,
                         guide_of, masked_ref, paymethods, ref_detail, ref_of,
                         validate_binding, all_guides)
from a2n_gateway import STATE_AUTHORIZED, STATE_CAPTURED
from a2n_ap2 import Mandate, validate_chain
from a2n_kernel.hashing import canonical_json, sha256
from a2n_registry import registry
from a2n_settlement.price import DEFAULT_CURRENCY, unit_price_of
from a2n_store import conn

router = APIRouter(prefix="/v1", tags=["payments"])


class PayMethodIn(BaseModel):
    channel: str                     # 渠道限定符（自由字符串，品牌由持牌层定义）
    ref: str | None = None           # 老式单凭据（兼容）；新式用 fields 走渠道引导
    fields: dict | None = None       # 渠道引导字段（按引导 schema 校验）
    currency: str | None = None      # 缺省取引导默认币种，再回退 CNY


@router.get("/pay-methods/channels")
def channels():
    """可绑定渠道清单 + 各自的引导页 schema（选择绑定账户的入口）。

    有引导的渠道展示分渠道引导页（支付宝有支付宝引导，安全币有安全币引导）；
    没引导的渠道仍可绑（协议不设限，引导只是产品层知识）。
    """
    return {"channels": all_guides(),
            "note": "绑定 ACTIVE 即自动结算：调用时门禁自动选用 compatible 渠道，无需后续动作；"
                    "A2N 只记录绑定声明，不碰钱、不验真伪"}


@router.post("/pay-methods")
def register(body: PayMethodIn, principal: str = Header(alias="X-Principal")):
    """绑定一个直付渠道。优先走渠道引导（fields 按引导 schema 校验），
    兼容老式单凭据（ref 原样存）；绑定后调用即自动结算。"""
    g = guide_of(body.channel)
    norm: dict | None = None
    if body.fields is not None:
        try:
            norm = validate_binding(body.channel, body.fields)   # ValueError → 400
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not norm:
            raise HTTPException(400, f"{(g or {}).get('label', body.channel)}绑定缺少必填项")
        ref = ref_of(norm)
    elif body.ref is not None:
        ref = body.ref
    elif g:   # 有引导但什么都没给 → 按引导报清楚缺哪几样
        missing = [f["label"] for f in g["fields"] if f.get("required")]
        raise HTTPException(400, f"{g['label']}绑定缺少必填项：{', '.join(missing)}")
    else:
        raise HTTPException(400, "请提供绑定凭据（fields 或 ref）")
    try:
        pm = paymethods.register(principal, body.channel, ref,
                                 currency=body.currency or (g or {}).get("currency") or "CNY")
    except ValueError as e:      # 含 UnknownMedium
        raise HTTPException(400, str(e))
    pm["ref_detail"] = norm if norm is not None else ref_detail(pm.get("ref"))
    pm["masked_ref"] = masked_ref(pm.get("ref"))
    pm["auto_settle"] = {"enabled": True,
                         "how": "调用时门禁自动选用该渠道（agent accepts ∩ 已绑定），无需后续动作"}
    return pm


@router.get("/pay-methods")
def list_mine(channel: str | None = None,
              principal: str = Header(alias="X-Principal")):
    rows = paymethods.list_by_owner(principal, channel, only_active=False)
    for r in rows:
        r["ref_detail"] = ref_detail(r.get("ref"))
        r["masked_ref"] = masked_ref(r.get("ref"))
        r["auto_settle"] = r.get("status") == "ACTIVE"
    return rows


@router.get("/pay-methods/compatible")
def compatible(agent_id: str, principal: str = Header(alias="X-Principal")):
    """我与某个 agent 的支付能力交集：差哪样、补哪样，一次说清。"""
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    card = json.loads(a["card_json"] or "{}")
    accepted = accepts_of_card(card)
    mine = paymethods.channels(principal)

    can: list[str] = []
    if PEER_ACCOUNT in accepted:
        row = conn().execute(
            "SELECT 1 FROM peer_links pl JOIN party_accounts pa"
            " ON pa.account_id = pl.account_id"
            " WHERE pl.agent_id=? AND pa.owner_id=? AND pl.state='ACTIVE'",
            (agent_id, principal)).fetchone()
        if row:
            can.append(PEER_ACCOUNT)
    chans = direct_channels(accepted)
    if chans:
        hit = mine if "*" in chans else (mine & chans)
        for ch in sorted(hit):
            can.append(f"direct_pay:{ch}")

    missing = [t for t in accepted
               if t not in can and not (t.startswith("direct_pay")
                                        and any(c.startswith("direct_pay:") for c in can))]
    return {"agent_id": agent_id, "accepted": accepted, "mine_channels": sorted(mine),
            "can_call_with": can, "missing": missing}


@router.post("/pay-methods/{pm_id}/close")
def close(pm_id: str, principal: str = Header(alias="X-Principal")):
    try:
        return paymethods.close(pm_id, principal)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/pay-charges/{charge_id}")
def charge_detail(charge_id: str):
    """直付成交凭证全文 + 重新机器核验（金额/授权链/任务一致性/单价漂移）。"""
    row = conn().execute("SELECT * FROM pay_charges WHERE charge_id=?",
                         (charge_id,)).fetchone()
    if not row:
        raise HTTPException(404, "成交凭证不存在")
    c = dict(row)
    chain = json.loads(c["mandate_chain"])

    # 1. 授权链重验：引用闭合 / 主体一致 / 金额不越权（intent≥cart≥payment）
    try:
        payment = Mandate(**chain["payment"])
        cart = Mandate(**chain["cart"])
        intent = Mandate(**chain["intent"])
        report = validate_chain(payment, cart, intent)
        chain_ok, chain_why = True, None
    except Exception as e:  # noqa: BLE001 - 任何链异常都如实报告
        report = {"ok": False, "error": str(e)}
        chain_ok, chain_why = False, str(e)

    # 2. 凭证自洽：总金额必须等于 单价快照 × 数量，渠道与凭证一致。
    #    期望的结算凭据由方式决定：直付 = direct_pay:渠道；x402 = x402 本身。
    #    多币种（S1 双写）：amount_minor 存在时以它为准（整数最小单位口径），
    #    老数据回退 _fen 列。
    amount = int(c["amount_minor"] if c["amount_minor"] is not None else c["amount_fen"])
    unit_price = int(c["unit_price_minor"] if c["unit_price_minor"] is not None
                     else c["unit_price_fen"])
    amount_ok = amount == unit_price * int(c["call_count"])
    expected_token = f"{c['method']}:{c['channel']}" if c["method"] == DIRECT_PAY else c["method"]
    pay_channel = (chain["payment"]["scope"] or {}).get("pay_channel", "")
    channel_ok = pay_channel == expected_token

    # 3. 与任务一致：凭证存在 = 任务当时已通过验收（失败/打回的调用不产生凭证）
    task = conn().execute("SELECT state FROM tasks WHERE id=?", (c["task_id"],)).fetchone()
    task_ok = bool(task) and task["state"] in ("ACCEPTED", "SETTLED") \
        and c["state"] in (STATE_AUTHORIZED, STATE_CAPTURED)
    captured = c["state"] == STATE_CAPTURED

    # 4. 单价漂移提示：快照价 vs agent 现在的报价（信息项，不影响本单）
    #    价目读 card（v1/v2 由 price_book 统一回退），比的是同一币种的单价。
    a = registry.get(c["agent_id"])
    drift = None
    if a:
        card = json.loads(a["card_json"] or "{}")
        cur = c.get("currency") or DEFAULT_CURRENCY
        now_price = unit_price_of(card, c["skill"], cur)
        snapshot_price = c["unit_price_minor"] if c["unit_price_minor"] is not None \
            else c["unit_price_fen"]
        drift = {"currency": cur,
                 "price_at_deal_minor": snapshot_price, "price_at_deal_fen": snapshot_price,
                 "price_now_minor": now_price, "price_now_fen": now_price,
                 "drifted": now_price != snapshot_price}

    # 5. 授权链摘要自洽：存档的 digest 必须能由凭证全文复算出来
    digest_ok = True
    for k in ("intent", "cart", "payment"):
        saved = chain.get(f"{k}_digest")
        if saved and saved != sha256(canonical_json(chain[k])):
            digest_ok = False

    ok = chain_ok and amount_ok and task_ok and digest_ok and channel_ok
    return {
        "charge": {k: c[k] for k in ("charge_id", "agent_id", "principal_id", "method",
                                     "channel", "skill", "unit_price_fen", "call_count",
                                     "amount_fen", "state", "task_id", "created_at")
                   if k in c},
        "money": {"currency": c.get("currency") or "CNY",
                  "unit_price_minor": unit_price, "amount_minor": amount,
                  "call_count": int(c["call_count"])},
        "mandate_chain": chain,
        "verify": {
            "ok": ok,
            "chain_ok": chain_ok, "chain_why": chain_why, "chain_report": report,
            "amount_ok": amount_ok,
            "channel_ok": channel_ok,
            "task_ok": task_ok,
            "digest_ok": digest_ok,
            "captured": captured,          # 是否已到账（AUTHORIZED ≠ 到账）
            "price_drift": drift,
        },
    }
