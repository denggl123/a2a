"""中继（relay）调用必须过治理链：收费 agent 的 relay 调用 = 一次对等成交。

这是第四轮评估发现的 P0：relay 端点曾只做转发、不记账 ——
"有凭据就能调"变成"有凭据就能不记账"，供给方白干。
本测试锁定修复后的契约：
  - 收费 + ACTIVE 配对 → 转发成功即产出一笔 RECONCILED 的对等成交，
    计费结果经 X-A2N-Billing 响应头返回（不污染 A2A 响应体）；
  - 免费 agent → 不产生任何账（与 /a2a 口径一致）；
  - 转发失败（节点侧 4xx/5xx）→ 不记账。
"""
from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from a2n_account import accounts, peers
from a2n_deal import deals
from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_store import conn
from a2n_transport import hub

from .test_flow import demo_card


def _client():
    from a2n_server.app import app
    return TestClient(app)


def _paid_card(skill: str) -> dict:
    card = demo_card("rb-" + new_id("")[2:], skill=skill)
    card["url"] = None
    card["accepts"] = ["peer_account"]
    return card


def _tunnel_node(aid: str) -> None:
    hub.open(aid, {"local_base": "http://127.0.0.1:9101"})


def _start_echo_node(aid: str, body: dict | None = None, status: int = 200) -> None:
    """模拟节点侧隧道客户端：取转发 → 就地"执行" → 回包。"""
    def loop():
        tid = hub.status(aid)["tunnel_id"]
        while True:
            msg = hub.poll(tid, wait=2.0)
            if not msg or msg.get("type") == "tunnel.closed":
                return
            if msg.get("type") == "forward":
                hub.reply(aid, msg["req_id"],
                          {"status": status, "body": body if body is not None
                           else {"echo": msg.get("body")}})
    threading.Thread(target=loop, daemon=True).start()


def _call_token(client: TestClient, aid: str, principal: str) -> str:
    r = client.post("/v1/transport/call-token", json={"agent_id": aid, "ttl": 600},
                    headers={"X-Principal": principal})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_relay_paid_call_is_billed_as_peer_deal():
    client = _client()
    skill = "ocr-pro"
    agent = registry.register("acct:rb-prov", _paid_card(skill))
    aid = agent["agent_id"]
    _tunnel_node(aid)
    _start_echo_node(aid)

    principal = "acct:rb-user"
    acc = accounts.create(principal, "对公")
    peers.propose(acc["account_id"], aid, auto_accept=True)

    token = _call_token(client, aid, principal)
    r = client.post(f"/v1/relay/{aid}/invoke", json={"text": "hi"},
                    headers={"X-A2N-Call": token})
    assert r.status_code == 200, r.text
    # 响应体原样透传：内容与 A2A 客户端拿到的完全一致，计费不在 body 里
    assert r.json() == {"echo": {"text": "hi"}}
    billing = r.headers.get("X-A2N-Billing")
    assert billing, "收费调用必须产出计费结果（响应头）"
    b = json.loads(billing)
    assert b["kind"] == "deal" and b["state"] == "RECONCILED", b

    # 库里确实有一笔成交，且没有任务单（relay 成交不挂任务）
    row = conn().execute("SELECT * FROM deals WHERE deal_id=?", (b["id"],)).fetchone()
    assert row and row["state"] == "RECONCILED" and row["task_id"] is None
    recon = conn().execute("SELECT * FROM deal_recons WHERE deal_id=?", (b["id"],)).fetchone()
    assert recon and recon["currency"] == "CNY"


def test_relay_free_call_records_nothing():
    client = _client()
    skill = "ocr-pro"
    card = demo_card("rb-free-" + new_id("")[2:], skill=skill)
    card["url"] = None
    card["x-a2n"].pop("price_hint", None)      # 没价目表 = 免费（is_free 判据）
    agent = registry.register("acct:rb-free-prov", card)
    aid = agent["agent_id"]
    _tunnel_node(aid)
    _start_echo_node(aid)

    token = _call_token(client, aid, "acct:rb-free-user")   # 免费：无需配对
    before = conn().execute("SELECT COUNT(*) n FROM deals").fetchone()["n"]
    r = client.post(f"/v1/relay/{aid}/invoke", json={"text": "hi"},
                    headers={"X-A2N-Call": token})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-A2N-Billing") is None, "免费不产生账"
    after = conn().execute("SELECT COUNT(*) n FROM deals").fetchone()["n"]
    assert after == before


def test_relay_failed_forward_is_not_billed():
    client = _client()
    skill = "ocr-pro"
    agent = registry.register("acct:rb-fail-prov", _paid_card(skill))
    aid = agent["agent_id"]
    _tunnel_node(aid)
    _start_echo_node(aid, body={"error": "boom"}, status=500)

    principal = "acct:rb-user2"
    acc = accounts.create(principal, "对公")
    peers.propose(acc["account_id"], aid, auto_accept=True)

    token = _call_token(client, aid, principal)
    before = conn().execute("SELECT COUNT(*) n FROM deals").fetchone()["n"]
    r = client.post(f"/v1/relay/{aid}/invoke", json={"text": "hi"},
                    headers={"X-A2N-Call": token})
    assert r.status_code == 200            # 上游 500 经隧道透传为 body
    assert r.headers.get("X-A2N-Billing") is None, "没干成不该有钱上账"
    after = conn().execute("SELECT COUNT(*) n FROM deals").fetchone()["n"]
    assert after == before
