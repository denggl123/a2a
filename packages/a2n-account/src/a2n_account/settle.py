"""agent 声明自己接受哪些结算方式。

结算方式分三类，门禁语义各不相同：
  peer_account      对等账户：双边记账、按账期对账结清（一期，不碰钱）
  prepaid_points    托管积分：预冻结后按实结算（二期，需托管商发行）
  direct_pay[:渠道] 直付：第三方渠道即时扣款。A2N 不碰资金流——只记
                    "授权链 + 单价快照 + 计量 + 凭证"，钱走渠道，账走 A2N。
                    渠道是限定符（direct_pay:<渠道>）；裸 direct_pay = 任意渠道。
                    A2N 代码不认识任何具体品牌（铁律二），品牌只是数据。

没有声明 = 一期按 peer_account 处理（保守但可用）。
发现层筛 accepts 是便利，不是资格——资格在调用门禁上。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_store import conn

from .accounts import PEER_ACCOUNT, PREPAID_POINTS, SETTLE_MODES


def accepts_of_card(card: dict | None) -> list[str]:
    """从 Agent Card 读 accepts 声明，保留合法的渠道限定符。

    规则：token 冒号前的 base 必须是已知结算方式，否则丢弃（防止声明不存在
    的东西）；base 合法则整个 token 保留（含渠道限定符）。
    """
    if not card:
        return []
    raw = card.get("accepts") or card.get("x-a2n", {}).get("accepts") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for t in raw:
        base = str(t).split(":", 1)[0]
        if base in SETTLE_MODES and t not in out:
            out.append(t)
    return out


def direct_channels(accepts: list[str]) -> set[str] | None:
    """从 accepts 里取直付渠道集合。None = 不接受直付；{"*"} = 任意渠道。"""
    chans: set[str] = set()
    for t in accepts:
        base, _, q = str(t).partition(":")
        if base == "direct_pay":
            chans.add(q.strip() or "*")
    return chans or None


def accepts_of_agent(agent_id: str) -> list[str]:
    r = conn().execute("SELECT card_json FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not r:
        return []
    try:
        card = json.loads(r["card_json"] or "{}")
    except ValueError:
        return []
    return accepts_of_card(card)


def supports(agent_id: str, mode: str = PEER_ACCOUNT) -> bool:
    return mode in accepts_of_agent(agent_id)


def compatible_with(agent_id: str, principal: str) -> dict:
    """我与某个 agent 的支付能力交集：现在能用什么调、还差什么没补。

    这是"发现是便利、资格在门禁"那句话的**唯一一份**实现（门禁 403 的
    补齐指引、`/v1/pay-methods/compatible` 的接口都读它），避免两处各算
    一遍、算出两个口径。

    注意：对等账户是**默认**匹配（agent 未声明 accepts 时按 peer_account
    处理，见 gate.resolve），所以这里 agent 没声明时也按 peer_account 算，
    并如实回答"要配对才能用"——不是把没配对的 agent 筛掉。
    """
    accepted = accepts_of_agent(agent_id) or [PEER_ACCOUNT]
    from .paymethods import paymethods
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


__all__ = ["accepts_of_card", "accepts_of_agent", "supports", "direct_channels",
           "compatible_with", "PEER_ACCOUNT", "PREPAID_POINTS", "SETTLE_MODES"]
