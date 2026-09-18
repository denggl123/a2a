"""传输层路由：反向长连接（隧道）、中继转发、通道协商。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from a2n_store import conn
from a2n_registry import CARD_REJECTED, card_verdict, registry, seats
from a2n_kernel.hashing import now_iso
from a2n_registry.reachability import PROBE_WINDOW_S, reachable
from a2n_settlement import is_free
from a2n_transport import hub, negotiate
from a2n_transport.call_token import issue as issue_call_token
from a2n_transport.call_token import subject as token_subject
from a2n_transport.call_token import verify as verify_call_token

router = APIRouter(prefix="/v1", tags=["transport"])


def network_summary(agent: dict) -> dict:
    """Public connection facts; no IP, private URL, tunnel ID or provider metadata."""
    connection = agent.get("connection") or {}
    mode = connection.get("mode", "pull")
    # Snapshot rendering must not trigger outbound probes or DB writes.
    if mode == "direct":
        try:
            probe_at = datetime.fromisoformat(connection.get("inbound_ok_at", "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - probe_at).total_seconds()
            online = 0 <= age < PROBE_WINDOW_S
        except (ValueError, TypeError, AttributeError):
            online = False
        why = "入站探测有效" if online else "暂无有效入站探测"
    else:
        online, why = reachable(agent)
    measured = hub.network_status(agent["agent_id"]) or {}
    rtt = measured.get("rtt_ms")
    checked = measured.get("checked_at")
    source = "平台→Agent 通道往返"
    if not measured and mode == "direct":
        rtt = connection.get("rtt_ms")
        checked = connection.get("inbound_ok_at")
        source = "平台 HTTP 入站探测"
    # Missing is not zero. Offline channels must not display an old green RTT.
    if not online:
        rtt = None
    state = measured.get("state", "measured" if rtt is not None else "unmeasured") if online else "offline"
    return {"reachable": online, "mode": mode, "rtt_ms": rtt,
            "checked_at": checked, "source": source, "state": state,
            "reason": measured.get("reason", why) if online else why}


@router.post("/registry/agents/{agent_id}/network/ping")
async def network_ping(agent_id: str, principal: str = Header(alias="X-Principal")):
    """Probe only a visible/owned Agent's control channel; never invoke a skill."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    owner = agent.get("principal_id") == principal
    if not owner:
        if not seats.expose(agent, viewer=principal)[0]:
            raise HTTPException(404, "Agent 不可见")
        if card_verdict(json.loads(agent.get("card_json") or "{}"), agent.get("card_hash"))["selfproof"] in CARD_REJECTED:
            raise HTTPException(404, "Agent 不可见")
    if (agent.get("connection") or {}).get("mode") not in ("tunnel", "relay"):
        return dict(network_summary(agent), state="unsupported",
                    reason="此通道不支持主动测速；隧道/中继节点使用新版 SDK 后可测速")
    result = await run_in_threadpool(hub.ping, agent_id)
    summary = network_summary(registry.get(agent_id))
    return dict(summary, **result) if summary["reachable"] else summary


class TunnelOpenIn(BaseModel):
    meta: dict = {}
    mode: str = "tunnel"          # tunnel 或 relay（relay = 隧道 + 平台公网入口）


class TunnelUpIn(BaseModel):
    req_id: str
    status: int = 200
    body: Any = None
    # 节点在交付边界上连署的计量（见 a2n_sdk.TunnelClient.attest_fn）。
    # 它必须原样过这一层 —— 上行体是手写 dict 的形状白名单，
    # 少一个字段不会报错，只会把签名**静默吃掉**：活库上表现为
    # "永远未签名"，而所有单测照样全绿（这正是它难被发现的原因）。
    attest: Any = None


def _touch_heartbeat(agent_id: str) -> None:
    """隧道下行轮询本身就是"我在线"的证据，顺带刷心跳。

    写 agents 表一律走 registry（表 owner）：路由层只做 HTTP 编排。
    """
    registry.heartbeat(agent_id)


@router.post("/nodes/{node_id}/tunnel")
def tunnel_open(node_id: str, body: TunnelOpenIn,
                x_node_id: str = "") -> dict:
    """节点出站建立反向通道（生产为 WSS；v1 用长轮询下行，接口同构）。"""
    if not registry.get(node_id):
        raise HTTPException(404, "agent 不存在")
    r = hub.open(node_id, dict(body.meta or {}, declared_mode=body.mode))
    # 连接声明（relay 模式要参与可达性与发现）走 registry 落库：
    # 由它做 normalize + NAT 判定，路由层不自己拼 connection JSON。
    registry.heartbeat(node_id, connection={"mode": body.mode})
    return r


@router.get("/nodes/{node_id}/tunnel/next")
def tunnel_next(node_id: str, tid: str, wait: float = 25.0):
    """下行长轮询：挂住等任务/转发请求。有则立即返回。"""
    _touch_heartbeat(node_id)
    msg = hub.poll(tid, wait=wait)
    return msg if msg is not None else {"type": "idle"}


@router.post("/nodes/{node_id}/tunnel/up")
def tunnel_up(node_id: str, body: TunnelUpIn):
    """上行回包：中继转发请求的响应从这里回去。

    `attest`（节点连署的计量）随回包一起交回编排层 —— 它不属于"响应体"，
    而是关于"这份计量是节点签的"的凭据，所以放在 body 旁边而不是 body 里面
    （混进 body 会污染结果与 result_hash）。
    """
    up: dict = {"status": body.status, "body": body.body}
    if body.attest is not None:
        up["attest"] = body.attest
    if not hub.reply(node_id, body.req_id, up):
        raise HTTPException(404, "转发请求不存在或已超时")
    return {"ok": True}


class VerifyTokenIn(BaseModel):
    """节点自守门的验票请求：direct 节点公网可被直连，必须自己验凭据。"""
    agent_id: str
    token: str


@router.post("/transport/verify-token")
def verify_token(body: VerifyTokenIn):
    """给节点用的验票接口（零信任）。

    中继模式下"能从隧道进来 = 已过门"，节点不用再验；但 direct 节点
    自己暴露公网地址，谁都能直接打过来 —— 平台替它守不住，只能它自己验：
    收到 X-A2N-Call 就调这里问一句"这票是不是真的、是不是给我的"。
    平台不替节点做决定，只回答真伪。
    """
    ok, why = verify_call_token(body.token, body.agent_id)
    return {"ok": ok, "reason": why, "subject": token_subject(body.token) if ok else None}


@router.post("/relay/{agent_id}/{path:path}")
@router.post("/relay/{agent_id}")
async def relay(agent_id: str, request: Request, response: Response, path: str = "",
                x_a2n_call: str = Header(default="", alias="X-A2N-Call")):
    """中继转发：平台公网入口 → 隧道 → 节点本地 HTTP 服务。

    这就是"个人电脑提供的服务在公网被发现和调用"的最终形态：
    外部只认平台的公网地址，节点侧零暴露、零端口映射。

    ⚠️ **这不是第二个调用入口**（P2 §4.2 收口）：它是**只给对等账户的底层原语**
    —— 不经门禁、不建任务、不验收、不公证，收费语义只有对等账户。
    面向"用别人的 agent"的调用一律走 `POST /v1/invoke`（治理链：门禁 → 建任务
    → 执行 → 验收 → 记账）。使用端界面上也不再暴露它（控制台只把它当"门牌"显示）。
    留它的唯一理由是：让"点对点打节点自定义路由"这类底层动作有路可走，
    且这条路也在平台的凭据与计量之内，不是公网裸奔。

    **门禁**：必须出示调用凭据（X-A2N-Call）。没有凭据的请求一律 401 ——
    否则中继就是谁都能蹭的免费公网跳板，还能绕过计量与对账直接打节点。
    节点侧无需再验：请求能从隧道进来，必然已经过了这道门。

    **记账**：转发成功后，收费 agent（有价目表）的调用按一次对等成交
    进治理链（deals + 事件公证）——能拿到收费节点的凭据=已 ACTIVE 配对，
    所以 relay 的收费语义就是对等账户；**免费不产生账**（is_free）。
    没有这一步，"有凭据就能调"就等于"有凭据就能不记账"，供给方白干。

    注意：forward 是阻塞等待（最长 15s），必须丢进线程池执行——
    在事件循环上直接等会把整个服务冻住，所有长轮询一起超时。
    """
    if not registry.get(agent_id):
        raise HTTPException(404, "agent 不存在")
    ok, why = verify_call_token(x_a2n_call, agent_id)
    if not ok:
        raise HTTPException(401, f"调用凭据无效：{why}（先 POST /v1/transport/call-token 获取）")
    raw = await request.body()
    try:
        body = json.loads(raw.decode()) if raw else None
    except ValueError:
        body = raw.decode(errors="replace")
    caller = token_subject(x_a2n_call)
    payload = await run_in_threadpool(hub.forward, agent_id, "POST", "/" + path, body,
                                      caller=caller)
    if payload.get("error") == "no_tunnel":
        raise HTTPException(409, payload["message"])
    if payload.get("error") == "timeout":
        raise HTTPException(504, payload["message"])
    # 转发成功 → 收费调用记账（失败不记账：没干成就不该有钱上账）
    # 计费结果走响应头，不污染 A2A 响应体（中继两端约定：body 原样透传）
    if not payload.get("error") and int(payload.get("status", 200)) < 400:
        try:
            billed = _bill_relay_call(agent_id, caller, path, body)
        except Exception as e:  # noqa: BLE001 - 记账失败如实报告，不吞
            billed = {"error": f"{type(e).__name__}: {e}"[:200]}
        if billed:
            response.headers["X-A2N-Billing"] = json.dumps(billed, ensure_ascii=False)
    return payload.get("body", payload)


def _bill_relay_call(agent_id: str, caller: str | None, path: str, body) -> dict | None:
    """relay 调用的记账：收费 agent + ACTIVE 配对 → 一次对等成交（call_count=1）。

    - 免费（is_free）→ None（免费不产生账，与 /a2a 口径一致）
    - 没配对 → None（拿不到凭据的路径根本到不了这里；防御性兜底）
    - 被直付/x402 结算覆盖的调用不走这里（那两种方式的凭据在 relay 拿不到）
    技能取 body.skill → 路径第一段 → 卡上第一个技能（都认不出来就用第一个）。
    """
    a = registry.get(agent_id)
    if not a or not caller:
        return None
    try:
        card = json.loads(a["card_json"] or "{}")
    except ValueError:
        return None
    if is_free(card):
        return None
    row = conn().execute(
        "SELECT pl.link_id FROM peer_links pl JOIN party_accounts pa"
        " ON pa.account_id = pl.account_id"
        " WHERE pl.agent_id=? AND pa.owner_id=? AND pl.state='ACTIVE'"
        " ORDER BY pl.rowid LIMIT 1",
        (agent_id, caller)).fetchone()
    if not row:
        return None
    known = [s.get("id") for s in (card.get("skills") or []) if s.get("id")]
    skill = ""
    if isinstance(body, dict):
        skill = str(body.get("skill") or "")
    if not skill and path:
        skill = path.strip("/").split("/")[0]
    if skill not in known:
        skill = known[0] if known else ""
    if not skill:
        return None
    from a2n_account import peers
    from a2n_gateway import record_peer
    link = peers.get(row["link_id"])
    return record_peer(None, skill, link)   # task_id=None：relay 成交不挂任务单


class CallTokenIn(BaseModel):
    agent_id: str
    ttl: int = 600


@router.post("/transport/call-token")
def call_token(body: CallTokenIn,
               principal: str = Header(default="", alias="X-Principal")):
    """签发调用凭据：配对了的，或免费 agent（无价目表）的使用方。

    免费放行不是绕门禁：凭据仍要取，中继调用照样过平台（计量/公证照走），
    只是免费本就不产生账，没有可被"绕过"的结算。免费判定出自服务端
    （settlement.is_free：没价目表 = 免费，铁律一不许平台替人编价）。
    """
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    a = registry.get(body.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    linked = conn().execute(
        "SELECT 1 FROM peer_links pl JOIN party_accounts pa ON pa.account_id = pl.account_id"
        " WHERE pl.agent_id=? AND pa.owner_id=? AND pl.state='ACTIVE'",
        (body.agent_id, principal),
    ).fetchone()
    if not linked:
        try:
            card = json.loads(a.get("card_json") or "{}")
        except ValueError:
            card = {}
        if not is_free(card):
            # 明说"走错门了"：relay 只服务对等账户，使用端面向 agent 的调用请走 /v1/invoke。
            # 不给这句提示的话，开发者会以为"中继能用=这就是调用方式"，从而绕过治理链。
            raise HTTPException(
                403, "没有与该 agent 的 ACTIVE 对等账户配对，无调用资格"
                     "（/v1/relay 是只给对等账户的底层原语；"
                     "面向使用方的调用请走 POST /v1/invoke）")
    return {"token": issue_call_token(body.agent_id, principal, body.ttl),
            "ttl": body.ttl, "agent_id": body.agent_id}


@router.get("/nodes/{node_id}/transport")
def transport_negotiate(node_id: str):
    """通道协商：列出候选（direct/holepunch/tunnel/relay/pull）与当前最优。"""
    a = registry.get(node_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    # 注入权威可达性判定：transport 不反向依赖 registry，由装配层接线
    return negotiate(a, is_reachable=reachable)
