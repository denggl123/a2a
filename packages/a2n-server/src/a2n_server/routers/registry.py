"""注册、发现、账户。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from a2n_store import conn
from a2n_dispatch import discovery
from a2n_kernel.errors import A2NError
from a2n_registry import registry
from a2n_ledger import Ledger, ensure_account, list_accounts
from a2n_kernel.hashing import now_iso

router = APIRouter(prefix="/v1", tags=["registry"])


class AccountIn(BaseModel):
    id: str
    kind: str = "user"
    name: str = ""
    kyc_status: str = "verified"


class RegisterIn(BaseModel):
    card: dict
    visibility: str = "public"


class HeartbeatIn(BaseModel):
    """节点自报的连接状态与运行指标。

    pull/wss 只要能出网就能接单（家宽默认）；direct/relay 需要真实公网入口，
    声明 127.0.0.1 / 192.168.x 会被平台强制降级为 pull。
    metrics 是服务端 SDK 自算的服务质量（TTFT 平均等），节点自报 = 内部真相。
    """
    mode: str | None = None
    url: str | None = None
    local_ips: list[str] | None = None
    nat: str | None = None
    metrics: dict | None = None


class DiscoveryIn(BaseModel):
    require: dict
    filter: dict | None = None
    sort: list | None = None
    limit: int = 20
    include_unlisted: bool = False


@router.post("/accounts")
def create_account(body: AccountIn):
    ensure_account(body.id, body.kind, body.name or body.id, body.kyc_status)
    return {"id": body.id, "kind": body.kind}


@router.get("/accounts")
def accounts():
    return [dict(r) for r in list_accounts()]


@router.get("/accounts/{account_id}/balance")
def balance(account_id: str):
    return {"account_id": account_id, "points": Ledger().balance(account_id)}


@router.post("/registry/agents")
def register_agent(body: RegisterIn, principal: str = Header(alias="X-Principal")):
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    ensure_account(principal, "user", principal)
    try:
        return registry.register(principal, body.card, body.visibility)
    except A2NError as e:   # 领域异常自带 HTTP 状态码（冲突 409 / 形状 400）
        raise HTTPException(e.http_status, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/registry/agents")
def list_agents(principal: str | None = Header(default=None, alias="X-Principal")):
    if principal:
        return registry.list_by_principal(principal)
    return registry.list_all()


@router.get("/registry/agents/{agent_id}")
def get_agent(agent_id: str):
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    return a


@router.put("/registry/agents/{agent_id}/card")
def update_agent_card(agent_id: str, body: RegisterIn,
                      principal: str = Header(alias="X-Principal")):
    """供给方整卡更新（改价/改算力/改结算方式）。只有主体自己能改自己的卡。

    card_hash 同步重算：已成交合约的快照不受影响，"下一单"看到新行情。
    """
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    if a.get("principal_id") != principal:
        raise HTTPException(403, "只能更新自己名下的 agent")
    try:
        return registry.update_card(agent_id, body.card)
    except A2NError as e:
        raise HTTPException(e.http_status, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/registry/agents/{agent_id}/heartbeat")
def heartbeat(agent_id: str, body: HeartbeatIn | None = None, request: Request = None):
    """心跳。平台回传它看到的出口 IP 与 NAT 判定 —— 节点自己看不见自己。"""
    peer = request.client.host if request and request.client else None
    body_d = body.model_dump(exclude_none=True) if body else None
    return registry.heartbeat(agent_id, body_d, peer)


@router.post("/discovery/query")
def query(body: DiscoveryIn):
    return discovery.query(body.require, body.filter, body.sort, body.limit, body.include_unlisted)


@router.get("/roster")
def roster(principal: str = Header(alias="X-Principal")):
    """我的市场列表（服务端镜像，便于管理台展示；生产建议只存本地）。"""
    r = conn().execute("SELECT roster FROM rosters WHERE principal_id=?", (principal,)).fetchone()
    return json.loads(r["roster"]) if r else []


@router.post("/roster/{agent_id}")
def roster_add(agent_id: str, principal: str = Header(alias="X-Principal")):
    if not registry.get(agent_id):
        raise HTTPException(404, "agent 不存在")
    current = roster(principal)
    if agent_id not in current:
        current.append(agent_id)
    _save_roster(principal, current)
    return current


@router.delete("/roster/{agent_id}")
def roster_del(agent_id: str, principal: str = Header(alias="X-Principal")):
    current = [a for a in roster(principal) if a != agent_id]
    _save_roster(principal, current)
    return current


def _save_roster(principal: str, items: list) -> None:
    conn().execute(
        "INSERT INTO rosters (principal_id, roster, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(principal_id) DO UPDATE SET roster=excluded.roster, updated_at=excluded.updated_at",
        (principal, json.dumps(items), now_iso()),
    )
    conn().commit()
