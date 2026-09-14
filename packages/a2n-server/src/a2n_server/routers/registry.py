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
def accounts(principal: str | None = Header(default=None, alias="X-Principal")):
    # 主体清单不对外：业务面"看不到供应商"，连别人是谁都不能枚举。
    # 自查自身余额走 /v1/accounts/{id}/balance；这里只给鉴权用户回自己的那一行。
    if not principal:
        raise HTTPException(401, "缺少 X-Principal")
    own = [dict(r) for r in list_accounts() if r["id"] == principal]
    return own


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

    身份/设施字段一律不对外（业务面"看不到供应商"）：
      - principal_id：只 owner 拿到真实值，别人剔除；
      - peer_ip：平台侧 NAT 观测，能反推到供给方；
      - connection 内 local_ips / observed / metrics / rtt_ms / inbound_ok_at
        全是供给方设施或内网拓扑细节，一并剔除。
    只对外保留业务面该有的：身份/可达/质量/行情/契约。

    card_hash 不动：它背书的是原始卡，不是投影卡。
    """
    if not a:
        return a                       # None / 空：调用方自己判 404，别在这里造形状
    if not isinstance(a, dict):
        # 非 dict 一律当场报错，不静默原样返回。静默放行是这里最坏的一种"容错"：
        # 原始 sqlite3.Row 会被当成"已投影的卡"继续往上传，直到某个 .get() / 键访问
        # 才炸 —— 而且只在"这条路径恰好有数据"时才炸（空列表不进循环就一直是绿的，
        # 实际已经踩过：console/managed 的空收藏掩盖了一个 500）。
        # 行解码是 a2n-registry 的事（registry.get / _row_to_agent），
        # 路由层不要拿 conn().execute(...) 的裸行来做业务面投影。
        raise TypeError(
            f"_project_agent 只接受 dict（用 registry.get 取已解码的 agent），"
            f"收到 {type(a).__name__}")
    if principal and a.get("principal_id") == principal:
        return a                       # owner 看自己的：真实地址（上架回填要用）
    out = dict(a)
    entry = f"/v1/relay/{a['agent_id']}"
    # 地址投影：card_json.url / card_url / connection.url 三处一并改成中继门牌号
    try:
        card = json.loads(out.get("card_json") or "{}")
        if isinstance(card, dict) and card.get("url"):
            card["url"] = entry
            out["card_json"] = json.dumps(card, ensure_ascii=False)
    except ValueError:
        pass
    conn = a.get("connection")
    if isinstance(conn, dict) and conn:
        # 连接信息只留"可达 + 怎么连"，身份与设施细节全剔
        out["connection"] = {
            "mode": conn.get("mode", "pull"),
            "url": entry if conn.get("mode") in RELAYABLE_MODES else None,
            "nat": conn.get("nat", "unknown"),
        }
    if out.get("card_url"):                 # 独立列也存着真实地址，一并投影
        out["card_url"] = entry
    out["entry"] = entry                # 显式告诉使用方：经这里调用
    out["projected"] = True
    # 业务面不该看见的供应方身份与基础设施细节
    out.pop("principal_id", None)
    out.pop("peer_ip", None)
    return out


def _with_evidence(d: dict) -> dict:
    """给投影卡挂上"证据摘要"（试用进度 / 评分 / 质量偏差）。

    位置刻意放在**路由层**：a2n-dispatch 只产出发现事实（能力/行情/可达），
    "谁被评了多少分、模板偏差异多大"是业务面呈现，属于编排层的活。
    三段分开挂，**不合成一个综合分** —— 界面本身就在说"这三件事不是一回事"。
    """
    if isinstance(d, dict) and d.get("agent_id"):
        from a2n_server.routers.quality import evidence_summary
        try:
            d["evidence"] = evidence_summary(d["agent_id"])
        except Exception:          # noqa: BLE001 - 摘要挂了不该让发现整体 500
            d["evidence"] = None
    return d


@router.get("/registry/agents")
def list_agents(principal: str | None = Header(default=None, alias="X-Principal")):
    rows = registry.list_by_principal(principal) if principal else registry.list_all()
    return [_with_evidence(_project_agent(a, principal)) for a in rows]


@router.get("/registry/agents/{agent_id}")
def get_agent(agent_id: str,
              principal: str | None = Header(default=None, alias="X-Principal")):
    a = registry.get(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    return _with_evidence(_project_agent(a, principal))


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
    rows = discovery.query(body.require, body.filter, body.sort, body.limit,
                           body.include_unlisted)
    # 发现结果同样挂证据摘要：找 Agent 那一页要一眼看出"试用中 · 免费 / 毕业 · 收费"，
    # 以及"有没有可看的案例与评分"。判断"能不能调"仍在服务端门禁，不在这里。
    return [_with_evidence(r) for r in rows]


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
