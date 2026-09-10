"""调用门禁：收费才验支付能力，免费直接放行。

与发现层对齐的那条原则在这里落地：
  **发现永远开放；调用只在对方收费时才验支付能力。**

判定结果是一个 `Capability`（用哪种方式、凭什么），判定过程不碰账、不碰钱。
x402 是个例外分支：它不是"预先具备的能力"，而是"这次有没有带凭证"——
没带凭证不叫无资格，而是**发起一次挑战**（402），让对方带钱来。
"""
from __future__ import annotations

from a2n_account import (DIRECT_PAY, PEER_ACCOUNT, PREPAID_POINTS, X402,
                         accepts_of_card, direct_channels, paymethods)
from a2n_custodian import get_custodian
from a2n_settlement import supported_currencies, unit_price_of
from a2n_store import conn

from .settle import Capability

FREE = Capability(None)          # 免费调用：无结算方式，不产生账


class PaymentRequired(Exception):
    """x402 挑战：不是错误，是"请先付钱"。"""

    def __init__(self, requirement: dict) -> None:
        super().__init__("payment required")
        self.requirement = requirement


def charging(card: dict) -> bool:
    """对方是否收费：声明了结算方式，或价目表里有非零单价。"""
    if card.get("accepts") or (card.get("x-a2n") or {}).get("accepts"):
        return True
    from a2n_settlement import price_book
    book = price_book(card)
    return any(int(e.get("amount") or 0) > 0
               for by_cur in book.values()
               for entries in by_cur.values() for e in entries)


def unit_price_fen(card: dict, skill: str) -> int:
    """技能单价（分）。v1 价目沿用分口径；多币价目默认取 CNY（无则首个币种）。"""
    return unit_price_of(card, skill, "CNY")


def choose_currency(card: dict, skill: str, wanted: str | None = None) -> str | None:
    """决定这次调用用什么币种计价（计价币种=结算币种，设计 D2）。

    wanted  使用方指定币种：agent 价目表里没有就拒绝 —— 宁可明说"不支持"，
            也不静默换成别的币种（那等于替双方做了一笔汇率交易）。
    未指定  默认 CNY（v1 口径延续）；价目表里没有 CNY 就取第一个币种。
    免费技能返回 None，不产生币种。
    """
    curs = supported_currencies(card, skill)
    if not curs:
        return None
    if wanted:
        w = str(wanted).upper()
        if w not in curs:
            raise PermissionError(
                f"该 agent 不支持以 {w} 结价（支持：{', '.join(curs)}）")
        return w
    return "CNY" if "CNY" in curs else curs[0]


def _active_link(agent_id: str, principal: str) -> dict | None:
    row = conn().execute(
        "SELECT pl.* FROM peer_links pl JOIN party_accounts pa ON pa.account_id = pl.account_id"
        " WHERE pl.agent_id=? AND pa.owner_id=? AND pl.state='ACTIVE'",
        (agent_id, principal)).fetchone()
    return dict(row) if row else None


def resolve(agent_id: str, principal: str, card: dict,
            payment: dict | None = None, resource: str = "",
            currency: str | None = None, skill: str = "") -> Capability:
    """结算能力判定。免费 → FREE；收费 → 找出一种可用的方式。

    currency：使用方想用什么币种结。先选币（价目表说了算），再选方式
    （登记的渠道必须能结这个币种）—— 渠道是钱路，币种是钱的单位，
    两件事不能互相将就：拿支付宝付美元不在本期讨论范围。

    x402 分支：没带凭证就抛 PaymentRequired（402 挑战），
    带了但校验不过就 PermissionError —— 挑战失败与资格不符是两回事。
    """
    if not charging(card):
        return FREE

    # skill 必须明确：价目是按技能键的，用空串去查恒查不到，
    # 会把 402 挑战额退化成 1（等于白送）。调用方没给就用卡上第一个技能。
    skill = skill or ((card.get("skills") or [{}])[0].get("id") or "")
    cur = choose_currency(card, skill, currency)   # 门禁按 agent 级默认价选币
    modes = accepts_of_card(card) or [PEER_ACCOUNT]   # 未声明方式 = 一期默认双边记账
    price = unit_price_of(card, skill, cur) or 1

    # 1. 已有能力优先：登记过的直付渠道（即时结清，零摩擦）。
    #    渠道必须能结选出的币种：登记时绑定的 currency 与本次币种一致才算命中。
    chans = direct_channels(modes)
    if chans:
        mine = {r["channel"]: r for r in paymethods.list_by_owner(principal)}
        hit = {ch for ch, r in mine.items()
               if (ch in chans or "*" in chans)
               and (r.get("currency") or "CNY").upper() == cur}
        if hit:
            channel = sorted(hit)[0]
            return Capability(DIRECT_PAY, channel=channel,
                              pm=mine[channel], currency=cur)

    # 2. 对等账户：配对过就能用（双边记账、按账期结清；币种随条款）
    if PEER_ACCOUNT in modes:
        link = _active_link(agent_id, principal)
        if link:
            return Capability(PEER_ACCOUNT, link=link, currency=cur)

    # 3. x402：请求即付。没有上述能力、但 agent 接受微支付 → 发起 402 挑战；
    #    带了凭证则校验，通过即放行。挑战失败与资格不符是两回事。
    if X402 in modes:
        if not payment:
            raise PaymentRequired(get_custodian().payment_requirement(
                resource or f"/a2a/{agent_id}", price,
                description=f"A2N 调用 {agent_id}（{cur}）", currency=cur))
        # 判据必须出自服务端：挑战体用服务端行情价构造，绝不能拿 payment 自带
        # 的 maxAmountRequired 当判据 —— 否则"要求多少"由调用方说了算，
        # 校验恒等于通过（付 1 分也能过），门禁形同虚设。
        ok, why = get_custodian().verify_payment(
            get_custodian().payment_requirement(
                resource or f"/a2a/{agent_id}", price,
                description=f"A2N 调用 {agent_id}（{cur}）", currency=cur),
            payment)
        if not ok:
            raise PermissionError(f"x402 支付凭证无效：{why}")
        return Capability(X402, channel=X402, payment=payment, currency=cur)

    # 4. 都不满足：把"对方收什么、你有什么、去补什么"一次说清
    owned = sorted(paymethods.channels(principal))
    if PREPAID_POINTS in modes:
        raise PermissionError(
            f"该 agent 收费（接受：{', '.join(modes)}）。prepaid_points 需二期托管商"
            f"发行后可用；当前可补：登记支付渠道 POST /v1/pay-methods，"
            f"或建对等账户并配对 POST /v1/party-accounts → POST /v1/peers。")
    raise PermissionError(
        f"该 agent 收费（{cur} 结价，接受：{', '.join(modes)}）。你当前没有可用的支付方式"
        f"（已登记渠道：{', '.join(owned) or '无'}）。补上任意一种即可调用："
        f"登记支付渠道 POST /v1/pay-methods；或建对等账户并配对"
        f" POST /v1/party-accounts → POST /v1/peers。")
