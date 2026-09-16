"""一期路由：多账户、对等账户配对、交易达成、双向对账、出账。

这一组接口全程不碰钱：没有余额、没有冻结、没有划转。
它只回答"谁和谁、按什么条款、做了多少活、双方各报了多少、对不对得上"。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from a2n_account import accounts, peers
from a2n_deal import deals, statements
from a2n_dispatch import discovery

router = APIRouter(prefix="/v1", tags=["deals"])


def _owner_ok(account_id: str, principal: str) -> dict:
    """越权检查：账户只能被它的主体操作。多账户是核心能力，这一步不能省。"""
    acc = accounts.get(account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    if acc["owner_id"] != principal:
        raise HTTPException(403, "该账户不属于当前主体")
    return acc


# ---------------- 账户 ----------------
class AccountIn(BaseModel):
    label: str
    ref: str | None = None
    currency: str = "CNY"            # 结算币种（须有已注册媒介，如 CNY/USDC/POINT）
    medium: str | None = None        # 媒介 code；同币种多媒介时必填
    network: str | None = None       # 链网络（稳定币账户用，如 ethereum/polygon）
    address: str | None = None       # 外部地址，A2N 不校验只标注
    direction: str = "both"          # pay | receive | both
    is_default: bool = False


@router.post("/party-accounts")
def create_account(body: AccountIn, principal: str = Header(alias="X-Principal")):
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    try:
        return accounts.create(principal, body.label, body.ref, currency=body.currency,
                               medium=body.medium, network=body.network,
                               address=body.address, direction=body.direction,
                               is_default=body.is_default)
    except ValueError as e:      # 含 UnknownMedium（未注册的媒介/币种）
        raise HTTPException(400, str(e))


@router.get("/party-accounts")
def list_accounts(principal: str = Header(alias="X-Principal")):
    return accounts.list_by_owner(principal)


@router.post("/party-accounts/{account_id}/status")
def set_account_status(account_id: str, status: str, principal: str = Header(alias="X-Principal")):
    _owner_ok(account_id, principal)
    try:
        return accounts.set_status(account_id, status)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 对等账户 ----------------
class PeerIn(BaseModel):
    account_id: str
    agent_id: str
    peer_ref: str | None = None
    terms: dict | None = None
    auto_accept: bool = False


@router.post("/peers")
def propose_peer(body: PeerIn, principal: str = Header(alias="X-Principal")):
    _owner_ok(body.account_id, principal)
    try:
        return peers.propose(body.account_id, body.agent_id, body.peer_ref,
                             body.terms, body.auto_accept)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/peers")
def list_peers(account_id: str | None = None, principal: str = Header(alias="X-Principal")):
    if account_id:
        _owner_ok(account_id, principal)
        return peers.list_by_account(account_id)
    out = []
    for a in accounts.list_by_owner(principal):
        for lk in peers.list_by_account(a["account_id"]):
            out.append(lk | {"label": a["label"]})
    return out


@router.get("/peers/{link_id}")
def get_peer(link_id: str, principal: str = Header(alias="X-Principal")):
    lk = peers.get(link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    return lk


@router.post("/peers/{link_id}/accept")
def accept_peer(link_id: str, principal: str = Header(alias="X-Principal")):
    lk = peers.get(link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    try:
        return peers.accept(link_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/peers/{link_id}/suspend")
def suspend_peer(link_id: str, principal: str = Header(alias="X-Principal")):
    lk = peers.get(link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    try:
        return peers.suspend(link_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/peers/{link_id}/close")
def close_peer(link_id: str, principal: str = Header(alias="X-Principal")):
    lk = peers.get(link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    try:
        return peers.close(link_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/peers/{link_id}/usable")
def peer_usable(link_id: str):
    ok, why = peers.usable(link_id)
    return {"usable": ok, "reason": why}


# ---------------- 发现：只看声明了结算方式的（可选便利筛选，不是资格） ----------------
@router.get("/discover/peer-ready")
def discover_peer_ready(skill: str, limit: int = 20,
                        principal: str | None = Header(default=None, alias="X-Principal")):
    """筛出"声明了任一结算方式"的 agent —— 即"要先解决支付才能调"的对象。

    收费方式可以是 peer_account（双边记账），也可以是 direct_pay:渠道（直付，
    渠道限定符在 token 里，不能用集合相交筛，所以这里后置过滤）。
    免费 agent 不在此列——它不需要结算方式就能调。发现永远开放，
    这里只是帮使用方把"要补支付方式"的对象筛出来。
    viewer=principal：名额满了的 agent 对未持有的新使用者不再出现（同发现层判据）。
    """
    out = discovery.query({"skill": skill}, limit=limit, viewer=principal)
    return [a for a in out if a.get("accepts")]


# ---------------- 交易 ----------------
class DealIn(BaseModel):
    link_id: str
    skill: str
    task_id: str | None = None


class ReportIn(BaseModel):
    party: str = Field(pattern="^(requester|provider)$")
    dims: dict
    amount_fen: int | None = None
    evidence: dict | None = None


@router.post("/deals")
def open_deal(body: DealIn, principal: str = Header(alias="X-Principal")):
    lk = peers.get(body.link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    try:
        return deals.open(body.link_id, body.skill, body.task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/deals/{deal_id}")
def get_deal(deal_id: str, principal: str = Header(alias="X-Principal")):
    d = deals.get(deal_id)
    if not d:
        raise HTTPException(404, "交易不存在")
    _owner_ok(d["account_id"], principal)
    return d


@router.post("/deals/{deal_id}/report")
def report_deal(deal_id: str, body: ReportIn):
    try:
        return deals.report(deal_id, body.party, body.dims, body.amount_fen, body.evidence)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/deals/{deal_id}/reconcile")
def reconcile_deal(deal_id: str):
    try:
        return deals.reconcile(deal_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/deals/{deal_id}/cancel")
def cancel_deal(deal_id: str, principal: str = Header(alias="X-Principal")):
    d = deals.get(deal_id)
    if not d:
        raise HTTPException(404, "交易不存在")
    _owner_ok(d["account_id"], principal)
    try:
        return deals.cancel(deal_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/peers/{link_id}/deals")
def list_link_deals(link_id: str, principal: str = Header(alias="X-Principal")):
    lk = peers.get(link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    return deals.list_by_link(link_id)


# ---------------- 账单 ----------------
class StatementIn(BaseModel):
    link_id: str
    period: str | None = None


@router.post("/statements")
def issue_statement(body: StatementIn, principal: str = Header(alias="X-Principal")):
    lk = peers.get(body.link_id)
    if not lk:
        raise HTTPException(404, "配对不存在")
    _owner_ok(lk["account_id"], principal)
    try:
        return statements.issue(body.link_id, body.period)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/statements")
def list_statements(link_id: str | None = None, principal: str = Header(alias="X-Principal")):
    if link_id:
        lk = peers.get(link_id)
        if not lk:
            raise HTTPException(404, "配对不存在")
        _owner_ok(lk["account_id"], principal)
        return statements.list_by_link(link_id)
    out = []
    for a in accounts.list_by_owner(principal):
        for lk in peers.list_by_account(a["account_id"]):
            for s in statements.list_by_link(lk["link_id"]):
                out.append(s | {"label": a["label"], "agent_id": lk["agent_id"]})
    return out
