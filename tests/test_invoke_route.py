"""程序化调用入口 `POST /v1/invoke`：与 A2A 同一条治理链，门禁按**能力**判定。

这一层锁的是"极简上手"的产品承诺：
  - 免费 agent → 零配置直接可调；
  - 收费 agent + 已绑定它接受的直付渠道 → 直接可调（**不需要对等账户配对**）★；
  - 对等账户是**默认**：agent 没声明 accepts 时按 peer_account 处理；
  - 接受 x402 但没带凭证 → 402 挑战（不是 403）；
  - 收费且无任何可用方式 → 403，且带 hint（差哪样、补哪样）。

★ 就是修复前那条 P0：SDK/控制台走裸中继（收费语义只有对等账户），
  导致绑了直付渠道的使用方被 403 卡死；现在统一走治理链，能力说了算。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a2n_account import accounts, paymethods, peers
from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_transport import hub


def _client() -> TestClient:
    from a2n_server.app import app
    return TestClient(app)


def _uid(tag: str) -> str:
    return f"acct:{tag}-{new_id('')[2:8]}"


def _card(name: str, skill: str, *, accepts: list | None = None,
          price: int = 3, free: bool = False) -> dict:
    ext: dict = {"deployment": {"region": "cn-east-2"},
                 "metering": {"dimensions": [{"key": "call_count", "verifiable": True}]}}
    if not free:
        ext["price_hint"] = {skill: {"amount": price, "unit": "fen_per_call"}}
    card: dict = {"name": name, "version": "1.0.0", "url": None,
                  "skills": [{"id": skill, "name": skill}], "x-a2n": ext}
    if accepts is not None:
        card["accepts"] = accepts
    return card


@pytest.fixture
def stub_forward(monkeypatch):
    """通道转发就地成功（invoke 是 inline 交付，不用真起节点）。"""
    monkeypatch.setattr(hub, "forward", lambda *a, **k: {
        "status": 200, "body": {"body": {"answer": "42"}}})


def test_free_agent_callable_with_zero_setup(stub_forward):
    """免费 = 零配置：陌生主体、没绑定任何东西，直接可调。"""
    c = _client()
    skill = "free-" + new_id("")[2:8]
    aid = registry.register(_uid("free"), _card("inv-free", skill, free=True))["agent_id"]
    r = c.post("/v1/invoke",
               json={"agent_id": aid, "skill": skill, "payload": {"x": 1}},
               headers={"X-Principal": _uid("stranger")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["state"] == "ACCEPTED"
    assert d["capability"] == "none"           # 免费：无结算方式、不产生账
    assert d["result"] == {"answer": "42"}


def test_bound_direct_channel_is_enough_to_call(stub_forward):
    """★ P0：绑了对方接受的直付渠道即可直接调——**不需要对等账户配对**。"""
    c = _client()
    skill = "ocr-" + new_id("")[2:8]
    aid = registry.register(_uid("dp-prov"),
                            _card("inv-dp", skill, accepts=["direct_pay:alipay"]))["agent_id"]
    user = _uid("dp-user")
    paymethods.register(user, "alipay", "138****0001")     # 只绑渠道，零配对
    r = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
               headers={"X-Principal": user})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["capability"] == "direct_pay:alipay"
    # 直付成交进 pay_charges（与对等式 deals 分开，一张表一种语义）
    assert d["settle"]["kind"] == "charge"


def test_peer_account_is_default_when_undeclared(stub_forward):
    """对等账户是默认匹配：agent 没声明 accepts → 按 peer_account 处理。"""
    c = _client()
    skill = "ocr-" + new_id("")[2:8]
    aid = registry.register(_uid("peer-prov"), _card("inv-peer", skill))["agent_id"]
    user = _uid("peer-user")

    # 既没配对也没渠道 → 403，hint 指向 peer_account（不是把 agent 判成"不可用"）
    r = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
               headers={"X-Principal": user})
    assert r.status_code == 403, r.text
    hint = r.json()["detail"]["hint"]
    assert hint["accepted"] == ["peer_account"]
    assert hint["missing"] == ["peer_account"]

    # 配一个对等账户 → 直接可调（这就是默认方式）
    acc = accounts.create(user, "对公")
    peers.propose(acc["account_id"], aid, auto_accept=True)
    r2 = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
                headers={"X-Principal": user})
    assert r2.status_code == 200, r2.text
    assert r2.json()["capability"] == "peer_account"


def test_x402_agent_returns_402_challenge(stub_forward):
    """接受 x402 但没带凭证 → 402 挑战（是"还没付"，不是"没资格"）。"""
    c = _client()
    skill = "ocr-" + new_id("")[2:8]
    aid = registry.register(_uid("x402-prov"),
                            _card("inv-x402", skill, accepts=["x402"]))["agent_id"]
    r = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
               headers={"X-Principal": _uid("x402-user")})
    assert r.status_code == 402, r.text
    d = r.json()["detail"]
    assert d["error"] == "payment required"
    assert d["requirement"]["accepts"], "402 必须带挑战体，客户端据此去付款"


def test_403_hint_says_exactly_what_to_add(stub_forward):
    """收费且没能力可调 → 403 一次说清：对方收什么、你有什么、还差什么。"""
    c = _client()
    skill = "ocr-" + new_id("")[2:8]
    aid = registry.register(_uid("dp2-prov"),
                            _card("inv-dp2", skill, accepts=["direct_pay:alipay"]))["agent_id"]
    r = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
               headers={"X-Principal": _uid("dp2-user")})
    assert r.status_code == 403, r.text
    hint = r.json()["detail"]["hint"]
    assert "direct_pay:alipay" in hint["accepted"]
    assert hint["missing"] == ["direct_pay:alipay"]
    assert hint["mine_channels"] == []


def test_invoke_requires_identity_and_existing_agent():
    c = _client()
    r = c.post("/v1/invoke", json={"agent_id": "ag_nope", "skill": "s"})   # 无身份
    assert r.status_code == 400
    r2 = c.post("/v1/invoke", json={"agent_id": "ag_nope", "skill": "s"},
                headers={"X-Principal": _uid("someone")})
    assert r2.status_code == 404
