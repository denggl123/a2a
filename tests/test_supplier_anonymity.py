"""供应商匿名：业务面"看不到供应商"与反向面鉴权回归。

不依赖 HTTP（HTTP 形态留给冒烟测）：直接调投影函数、notary 过滤、
console 聚合路径、ledger 视图，断言身份/设施字段不出现在外部视图里。

1. _project_agent 非 owner：principal_id/peer_ip/connection 设施细节全剔；
   owner 通道保持原样（含真实地址，上架回填要用）。
2. notary.recent()：披露侧 payload 不含 principal_id（历史链不动，验证不破）。
3. registry.register 事件：源头不再带 principal_id。
4. console/managed 收藏回表：走与 A 同一投影（roster 项不含身份字段）。
5. 反向面：/v1/tasks、/v1/usage、/v1/wallet/withdrawals 无身份应当由调用层
   抛 401；accounts 收口无身份同样 401。
"""
from __future__ import annotations

import json
import uuid

import pytest

from a2n_kernel.events import publish
from a2n_kernel.hashing import canonical_json, sha256
from a2n_notary import notary
from a2n_registry import registry
from a2n_server.routers.registry import _project_agent


def _card(name: str = "anon-ocr", url: str | None = "http://node.example/invoke") -> dict:
    return {
        "name": name,
        "url": url,
        "skills": [{"id": "ocr-pro", "name": "OCR", "tags": ["ocr"]}],
        "x-a2n": {"uid": str(uuid.uuid4()),
                  "deployment": {"region": "cn-east-2"},
                  "sla": {"max_latency_ms": 800},
                  "accepts": ["peer_account", "x402"]},
        "accepts": ["peer_account", "x402"],
    }


def _register(principal: str = "acct:erin") -> str:
    agent = registry.register(principal, _card(name="agent-ocr-x402"), "public")
    return agent["agent_id"]


def test_project_agent_strips_identity_for_non_owner():
    agent_id = _register()
    raw = registry.get(agent_id)
    # 非 owner：principal_id/peer_ip/连接设施细节全剔；地址投影成中继门牌号
    proj = _project_agent(raw, principal=None)
    assert "principal_id" not in proj, "principal_id 不应暴露给非 owner"
    assert "peer_ip" not in proj, "peer_ip 不应暴露给非 owner"
    conn = proj.get("connection") or {}
    forbidden = {"local_ips", "observed", "metrics", "rtt_ms", "inbound_ok_at"}
    assert forbidden.isdisjoint(conn.keys()), \
        f"connection 不应暴露供给方设施细节：{forbidden & set(conn.keys())}"
    # 中继门牌号到位
    assert proj["entry"] == f"/v1/relay/{agent_id}"
    assert proj["card_url"] == f"/v1/relay/{agent_id}"
    # 业务字段保留
    for k in ("agent_id", "name", "status", "kya_grade", "reputation",
              "card_json", "card_hash", "compute", "sla", "metering",
              "last_seen_at", "tasks_done", "earned", "uid", "visibility"):
        if k in raw:                              # 列存在就一定保留
            assert k in proj, f"业务字段 {k} 不该被剔"


def test_project_agent_owner_keeps_real_address():
    agent_id = _register("acct:owner")
    raw = registry.get(agent_id)
    own_view = _project_agent(raw, principal="acct:owner")
    # owner 通道原样回（含真实地址）：principal_id/connection/local_ips 全保留
    assert own_view["principal_id"] == "acct:owner"
    assert own_view["card_url"] == raw["card_url"]
    assert "peer_ip" in own_view                                # 主人看自己的也不刻意剔
    assert "local_ips" in (own_view.get("connection") or {})   # 主人看自己设施细节
    assert own_view["connection"] == raw["connection"]
    assert "projected" not in own_view                         # owner 不标 projected


def test_register_event_no_longer_carries_principal_id():
    """源头：register 事件 payload 不再带 principal_id。"""
    # 注册一个新的 agent；从 publish 历史里捞刚发布的事件
    from a2n_kernel import events
    captured: list[dict] = []
    events.subscribe("node.registered", lambda e: captured.append(e))
    agent_id = _register("acct:erin")
    matched = [e for e in captured if e.get("agent_id") == agent_id]
    assert matched, "应至少观察到一条 node.registered 事件"
    for e in matched:
        assert "principal_id" not in e, \
            f"node.registered 事件不应再带 principal_id: {e}"


def test_notary_recent_strips_principal_id_in_history():
    """读侧：notary.recent() 披露时剥掉 principal_id（链不动）。"""
    # 注册一个新 agent，让 notary 落一条新章
    _register("acct:erin")
    rows = notary.recent(limit=50)
    assert rows, "应有近期凭证"
    for r in rows:
        payload_str = r.get("payload") or "{}"
        # 披露侧永远不带 principal_id
        assert "principal_id" not in payload_str, \
            f"披露侧 payload 不该有 principal_id: {r['rid']}"


def test_notary_verify_unchanged_by_read_filter():
    """链完整性：recent() 的披露过滤不能破坏 verify() —— verify 走原始 payload。"""
    # 重算最新一节的 hash 必须 == 表里存的 hash
    ok, msg = notary.verify()
    assert ok, f"凭证链不应被披露过滤破坏：{msg}"


def test_console_managed_roster_uses_projection():
    """console/managed 收藏回表走与 A 同一投影函数 —— roster 项无身份。"""
    # 直接调投影函数（路由层导入 _project_agent）模拟 managed 的等价行为
    agent_id = _register("acct:erin")
    raw = registry.get(agent_id)
    # managed 是给当前 principal 看的："我的收藏" 主语是当前身份，
    # 但收藏项是别人/自己都可能 —— 投影统一走 caller-principal
    proj_for_visitor = _project_agent(raw, principal="acct:alice")
    assert "principal_id" not in proj_for_visitor
    assert "peer_ip" not in proj_for_visitor
    proj_for_owner = _project_agent(raw, principal="acct:erin")
    assert proj_for_owner["principal_id"] == "acct:erin"        # owner 看自己回真实


def test_accounts_endpoint_requires_principal():
    """GET /v1/accounts：无身份应 401（路由层硬收口）。"""
    from fastapi import HTTPException
    from a2n_server.routers.registry import accounts as accounts_handler
    with pytest.raises(HTTPException) as exc:
        accounts_handler(principal=None)
    assert exc.value.status_code == 401


def test_wallet_withdrawals_requires_principal():
    """GET /v1/wallet/withdrawals：无身份应 401。"""
    from fastapi import HTTPException
    from a2n_server.routers.wallet import withdrawals as withdrawals_handler
    with pytest.raises(HTTPException) as exc:
        withdrawals_handler(principal=None)
    assert exc.value.status_code == 401


def test_tasks_list_requires_principal_or_node():
    """GET /v1/tasks：无身份且无 as_node 应 401。"""
    from fastapi import HTTPException
    from a2n_server.routers.tasks import list_tasks as list_tasks_handler
    with pytest.raises(HTTPException) as exc:
        list_tasks_handler(principal=None, as_node=None)
    assert exc.value.status_code == 401


def test_usage_requires_principal_or_node():
    """GET /v1/usage：无身份且无节点 ID 应 401。"""
    from fastapi import HTTPException
    from a2n_server.routers.tasks import usage as usage_handler
    with pytest.raises(HTTPException) as exc:
        usage_handler(principal=None, as_node=None)
    assert exc.value.status_code == 401