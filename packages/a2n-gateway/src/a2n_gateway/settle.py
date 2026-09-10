"""结算分派：一次调用结束，按结算方式决定记什么账。

这里是"结算是竖轴、不是层"在代码上的落点：
调用本身不知道自己会被怎么记账，验收通过之后才由这里按方式分派——

    peer_account    → deals（双边记账、按账期对账；一期不碰钱）
    direct_pay:渠道 → pay_charges（直付凭证，等渠道回执才 CAPTURED）
    x402            → pay_charges（请求即付，凭据校验通过即记账）
    prepaid_points  → 走 A2N 积分结算（二期，由调用方指定 settle_points=True）

**一张表管一种语义**：对等式记 deals，即付式记 pay_charges，
不混用、不互相塞字段——混用之后每一处读代码都要先猜"这行是哪个语义"。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_account import DIRECT_PAY, PEER_ACCOUNT, PREPAID_POINTS, X402
from a2n_ap2 import CART, INTENT, PAYMENT, Mandate, validate_chain
from a2n_custodian import get_custodian
from a2n_custodian.media import DEFAULT_CURRENCY
from a2n_deal import deals
from a2n_kernel.hashing import canonical_json, new_id, now_iso, sha256
from a2n_store import conn

STATE_AUTHORIZED = "AUTHORIZED"
STATE_CAPTURED = "CAPTURED"
STATE_FAILED = "FAILED"


class Capability:
    """门禁结论：这次调用用哪种结算方式、凭什么、用什么币种。"""

    def __init__(self, mode: str | None, channel: str | None = None,
                 link: dict | None = None, pm: dict | None = None,
                 payment: dict | None = None, currency: str | None = None) -> None:
        self.mode = mode
        self.channel = channel
        self.link = link
        self.pm = pm
        self.payment = payment
        self.currency = currency   # 结算币种（计价币种=结算币种）；免费为 None

    @property
    def token(self) -> str:
        if self.mode == DIRECT_PAY:
            return f"direct_pay:{self.channel}"
        return self.mode or "none"


def mandate_chain(subject: str, agent_id: str, skill: str, settle_token: str,
                  unit_fen: int, qty: int, currency: str = "CNY") -> tuple[dict, dict]:
    """一次调用的 AP2 授权链：intent（授权上限）→ cart（买了什么）→ payment（扣款授权）。

    一期主体身份是 X-Principal 字符串，凭证由平台代签发（sig 留空、如实标注）；
    二期接用户 DID 后换成用户私钥签名，validate_chain 的结构校验不变。
    scope 里的 currency 是授权币种快照：金额在哪个币种下被授权，就只能在
    哪个币种下被扣 —— 换币重放授权链在这里就过不去。
    """
    total = unit_fen * qty
    intent = Mandate(kind=INTENT, subject=subject, agent=agent_id, issuer=subject,
                     scope={"max_amount_fen": total, "skill": skill, "currency": currency},
                     x_a2n={"issuer_note": "platform-issued (no user key yet)"})
    cart = Mandate(kind=CART, subject=subject, agent=agent_id, issuer=agent_id,
                   parent=intent.id,
                   scope={"items": [{"skill": skill, "qty": qty, "unit_price_fen": unit_fen,
                                     "amount_fen": total, "currency": currency}],
                          "total_fen": total, "currency": currency})
    payment = Mandate(kind=PAYMENT, subject=subject, agent=agent_id, issuer=subject,
                      parent=cart.id,
                      scope={"amount_fen": total, "currency": currency, "settle": settle_token,
                             "pay_channel": settle_token})
    report = validate_chain(payment, cart, intent)   # 无验签器：结构/时效/金额校验
    pack = {"intent": intent.to_dict(), "cart": cart.to_dict(),
            "payment": payment.to_dict()}
    for k, m in (("intent", intent), ("cart", cart), ("payment", payment)):
        pack[f"{k}_digest"] = sha256(canonical_json(m.to_dict()))
    return pack, report


def record_peer(task_id: str, skill: str, link: dict, currency: str | None = None) -> dict:
    """对等账户：开交易 → 双方各自上报 → 对账。不碰钱。

    币种取条款快照里的 terms.currency（配对时谈定，缺省 CNY）——
    对等账户是双边记账，币种属于条款的一部分，不该在成交时偷换。
    """
    currency = currency or (link.get("terms") or {}).get("currency") or "CNY"
    d = deals.open(link["link_id"], skill, task_id=task_id, currency=currency)
    deals.report(d["deal_id"], "provider", {"call_count": 1})
    deals.report(d["deal_id"], "requester", {"call_count": 1})
    rec = deals.reconcile(d["deal_id"])
    conn().commit()
    return {"kind": "deal", "id": d["deal_id"], "state": rec.get("state") or d.get("state"),
            "currency": currency}


def record_charge(task_id: str, agent_id: str, principal: str, skill: str,
                  method: str, channel: str, pm: dict | None, unit_fen: int,
                  qty: int, chain: dict, currency: str = "CNY",
                  medium: str | None = None) -> dict:
    """直付 / x402：记一张成交凭证。

    初始状态只能是 AUTHORIZED —— **回执没到就不许说钱到了**。
    x402 校验通过的当场就可以 settle（见 capture_after_payment）。
    币种与金额在成交时点快照（currency + amount_minor + unit_price_minor），
    旧 _fen 列继续双写 —— 老读取方不断供，新读取方永远读整数最小单位。
    """
    charge_id = f"pc_{new_id('')}"
    ts = now_iso()
    conn().execute(
        "INSERT INTO pay_charges (charge_id, agent_id, principal_id, method, channel,"
        " pm_id, skill, unit_price_fen, call_count, amount_fen, state, mandate_chain,"
        " task_id, authorized_at, created_at, currency, medium, unit_price_minor,"
        " amount_minor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (charge_id, agent_id, principal, method, channel,
         (pm or {}).get("pm_id"), skill, unit_fen, qty, unit_fen * qty,
         STATE_AUTHORIZED, json.dumps(chain, ensure_ascii=False), task_id, ts, ts,
         currency.upper(), medium, unit_fen, unit_fen * qty))
    # 注：A2A 协议视图（a2a_tasks）由协议适配层负责写，结算层不认识它——
    # 一旦结算层知道"有个 A2A 表"，每加一种协议就得回来改一次记账代码。
    conn().commit()
    return {"kind": "charge", "id": charge_id, "state": STATE_AUTHORIZED}


def capture(charge_id: str, channel_ref: str | None = None) -> dict | None:
    """回执确认：AUTHORIZED → CAPTURED。只有真回执能调它。"""
    row = conn().execute("SELECT * FROM pay_charges WHERE charge_id=?", (charge_id,)).fetchone()
    if not row:
        return None
    if row["state"] == STATE_CAPTURED:
        return dict(row)
    conn().execute(
        "UPDATE pay_charges SET state=?, channel_ref=COALESCE(?, channel_ref), captured_at=?"
        " WHERE charge_id=?", (STATE_CAPTURED, channel_ref, now_iso(), charge_id))
    conn().commit()
    return dict(conn().execute("SELECT * FROM pay_charges WHERE charge_id=?",
                               (charge_id,)).fetchone())


def fail_charge(charge_id: str, why: str) -> dict | None:
    row = conn().execute("SELECT * FROM pay_charges WHERE charge_id=?", (charge_id,)).fetchone()
    if not row:
        return None
    conn().execute("UPDATE pay_charges SET state=?, channel_ref=? WHERE charge_id=?",
                   (STATE_FAILED, why[:120], charge_id))
    conn().commit()
    return dict(conn().execute("SELECT * FROM pay_charges WHERE charge_id=?",
                               (charge_id,)).fetchone())


def capture_after_payment(charge_id: str, payment: dict) -> dict:
    """x402：凭证已校验 → 调持牌层真扣款 → 成功才 CAPTURED。

    扣款动作在持牌层，这里只负责把结果如实落到凭证状态上。
    """
    row = conn().execute("SELECT * FROM pay_charges WHERE charge_id=?", (charge_id,)).fetchone()
    if not row:
        return {"error": "凭证不存在"}
    # 金额取最小单位（amount_minor），缺则回退旧列：x402 是多币种通道，
    # 拿 CNY 的"分"列去扣 USDC，扣出来的数根本不是同一个量纲。
    amount = row["amount_minor"] if row["amount_minor"] is not None else row["amount_fen"]
    ok, detail = get_custodian().settle_payment(
        payment, int(amount), ref=charge_id,
        currency=(row["currency"] or DEFAULT_CURRENCY))
    if not ok:
        fail_charge(charge_id, f"settle 失败：{detail}")
        return {"ok": False, "why": detail}
    return {"ok": True, "charge": capture(charge_id, channel_ref=detail)}


def _settle_peer(cap: Capability, task: dict, agent_id: str, principal: str,
                 skill: str, unit_fen: int, qty: int, currency: str,
                 medium: str | None) -> dict:
    """对等账户：双边记账，按账期对账（一期不碰钱）。"""
    return record_peer(task["id"], skill, cap.link, currency=currency)


def _settle_charge(cap: Capability, task: dict, agent_id: str, principal: str,
                   skill: str, unit_fen: int, qty: int, currency: str,
                   medium: str | None) -> dict:
    """即付式（直付 / x402）：记 pay_charges 凭证；x402 凭证已带就当场 capture。"""
    chain, _ = mandate_chain(principal, agent_id, skill, cap.token, unit_fen, qty,
                             currency=currency)
    # 媒介归属：登记过的支付方式带自己的 medium；x402 即付没有登记，
    # 媒介由持牌清算方声明（业务层只转记，不猜）。
    med = medium or (cap.pm or {}).get("medium")
    if not med and cap.mode == X402:
        med = get_custodian().payment_medium()
    rec = record_charge(task["id"], agent_id, principal, skill, cap.mode,
                        cap.channel or cap.mode, cap.pm, unit_fen, qty, chain,
                        currency=currency, medium=med)
    if cap.mode == X402 and cap.payment:
        rec["capture"] = capture_after_payment(rec["id"], cap.payment)
    return rec


def _settle_points(cap: Capability, task: dict, agent_id: str, principal: str,
                   skill: str, unit_fen: int, qty: int, currency: str,
                   medium: str | None) -> dict:
    """积分结算由任务域自己完成（调用方传 settle_points=True），这里只认账。"""
    return {"kind": "points", "id": task["id"], "state": "SETTLED"}


# 结算方式注册表：加一种方式 = register_settler() 注册一个函数，
# 分派主体不用再改 —— 方式会越来越多，if 分支撑不住。
SETTLERS: dict[str, Any] = {
    PEER_ACCOUNT: _settle_peer,
    DIRECT_PAY: _settle_charge,
    X402: _settle_charge,
    PREPAID_POINTS: _settle_points,
}


def register_settler(mode: str, fn) -> None:
    """注册一种结算方式的记账实现（幂等：同方式覆盖）。"""
    SETTLERS[mode] = fn


def dispatch(cap: Capability, task: dict, agent_id: str, principal: str,
             skill: str, unit_fen: int, qty: int = 1,
             currency: str = DEFAULT_CURRENCY, medium: str | None = None) -> dict:
    """按结算方式分派记账。返回 {"kind","id","state"}。

    currency 是这笔成交的结算币种（计价币种=结算币种，见设计 D2）：
    直付/x402 记进凭证快照；对等账户交易沿条款币种走。
    """
    fn = SETTLERS.get(cap.mode)
    if not fn:
        return {"kind": "none", "id": None, "state": None}
    return fn(cap=cap, task=task, agent_id=agent_id, principal=principal, skill=skill,
              unit_fen=unit_fen, qty=qty, currency=currency, medium=medium)
