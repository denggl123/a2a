"""注册、发现、账户。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from a2n_dispatch import discovery
from a2n_dispatch.service import RELAYABLE_MODES
from a2n_kernel.errors import A2NError
from a2n_registry import registry, rosters
from a2n_ledger import Ledger, ensure_account, list_accounts

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


def _project_agent(a: dict, principal: str | None) -> dict:
    """对外投影：节点真实地址只归它自己，别人一律只拿 A2N 中继门牌号。

    防的就是"拿到 card 里的地址绕开 A2N 直连节点"——那等于绕过门禁、
    计量、对账与刻章：供给方白干，使用方手里没有凭证（纠纷时 A2N 不认）。
    地址投影后，直连打到的仍是平台入口，绕不开；真要直连必须节点自己
    声明 direct 并自己守门（零信任：公网地址一旦存在，谁都可能打）。

    card_hash 不动：它背书的是原始卡，不是投影卡。
    """
    if not a or not isinstance(a, dict):
        return a
    if principal and a.get("principal_id") == principal:
        return a                       # owner 看自己的：真实地址（上架回填要用）
    out = dict(a)
    entry = f"/v1/relay/{a['agent_id']}"
    try:
        card = json.loads(a.get("card_json") or "{}")
        if isinstance(card, dict) and card.get("url"):
            card["url"] = entry
            out["card_json"] = json.dumps(card, ensure_ascii=False)
    except ValueError:
        pass
    conn = a.get("connection")
    if isinstance(conn, dict) and conn:
        conn = dict(conn)
        conn["url"] = entry if conn.get("mode") in RELAYABLE_MODES else None
        out["connection"] = conn
    if out.get("card_url"):                 # 独立列也存着真实地址，一并投影
        out["card_url"] = entry
    out["entry"] = entry                # 显式告诉使用方：经这里调用
    out["projected"] = True
    return out


@router.get("/registry/agents")
def list_agents(principal: str | None = Header(default=None, alias="X-Principal")):
    rows = registry.list_by_principal(principal) if principal else registry.list_all()
    return [_project_agent(a, principal) for a in rows]


@router.get("/registry/agents/{agent_id}")
def get_agent(agent_id: str,
              principal: str | None = Header(default=None, alias="X-Principal")):
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    return _project_agent(a, principal)


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


class ObservationIn(BaseModel):
    """使用端实测回传：端到端往返只有调用方测得到（P2P 平台测不了）。"""
    task_id: str | None = None
    rtt_ms: int | None = None
    total_ms: int | None = None
    ok: bool | None = None


@router.post("/registry/agents/{agent_id}/observations")
def observe_agent(agent_id: str, body: ObservationIn,
                  principal: str = Header(alias="X-Principal")):
    """使用端把实测（端到端耗时）回传给平台聚合，进发现页「使用端」口径。"""
    try:
        return registry.observe(agent_id, principal,
                                body.model_dump(exclude_none=True))
    except A2NError as e:
        raise HTTPException(e.http_status, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/discovery/query")
def query(body: DiscoveryIn):
    return discovery.query(body.require, body.filter, body.sort, body.limit, body.include_unlisted)


@router.get("/roster")
def roster(principal: str = Header(alias="X-Principal")):
    """我的市场列表（服务端镜像，便于管理台展示；生产建议只存本地）。

    表 owner 是 a2n-registry（rosters 模块），路由只做 HTTP 编排。
    """
    return rosters.get(principal)


@router.post("/roster/{agent_id}")
def roster_add(agent_id: str, principal: str = Header(alias="X-Principal")):
    if not registry.get(agent_id):
        raise HTTPException(404, "agent 不存在")
    return rosters.add(principal, agent_id)


@router.delete("/roster/{agent_id}")
def roster_del(agent_id: str, principal: str = Header(alias="X-Principal")):
    return rosters.remove(principal, agent_id)
