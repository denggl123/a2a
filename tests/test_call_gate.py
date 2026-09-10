"""调用门禁：凭据签发/校验、连接投影不泄露真实地址。

守住两条：
1. 中继入口不是公共跳板 —— 没凭据打不进去，没配对拿不到凭据；
2. 发现结果里永远只有 A2N 的门牌号，节点真实地址与出口 IP 不出注册表。
"""
import json

import pytest

from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_transport import call_token
from a2n_dispatch import discovery


def _card(name: str, skill: str = "ocr-pro", connection: dict | None = None) -> dict:
    return {
        "name": name, "version": "1.0.0", "url": "http://localhost/a2a",
        "accepts": ["peer_account"],
        "skills": [{"id": skill, "name": skill, "tags": [], "inputModes": ["application/json"]}],
        "x-a2n": {"deployment": {"region": "cn-east-2"},
                  "connection": connection or {}},
    }


def test_token_verify_roundtrip_and_tamper():
    t = call_token.issue("ag_x", "acct:alice")
    ok, why = call_token.verify(t, "ag_x")
    assert ok and call_token.subject(t) == "acct:alice"

    # 篡改 payload 一个字节 → 签名立刻对不上
    p, s = t.split(".")
    raw = bytearray(call_token._unb64(p))
    raw[-1] ^= 1
    ok, why = call_token.verify(call_token._b64(bytes(raw)) + "." + s, "ag_x")
    assert not ok and "签名" in why

    # 绑定错误：给 ag_x 签的凭据打 ag_y 进不去
    ok, why = call_token.verify(t, "ag_y")
    assert not ok and "绑定" in why

    # 过期：ttl=-1 即签发即过期
    ok, why = call_token.verify(call_token.issue("ag_x", "acct:alice", ttl=-1), "ag_x")
    assert not ok and "过期" in why

    # 垃圾输入不炸，只拒绝
    ok, why = call_token.verify("not-a-token", "ag_x")
    assert not ok


def test_discovery_never_leaks_real_url_or_peer_ip():
    """direct 节点的真实 url 与出口 IP 绝不进入发现结果 —— 只发 A2N 门牌号。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:d{suffix}", _card("d-" + suffix, connection={
        "mode": "direct", "url": "http://93.184.216.34:9000/a2a"}))
    aid = agent["agent_id"]

    row = next(g for g in discovery.query({"skill": "ocr-pro"}, limit=50)
               if g["agent_id"] == aid)
    assert row["connection"]["url"] == f"/v1/relay/{aid}"
    assert "93.184.216.34" not in json.dumps(row)
    assert "peer_ip" not in row["connection"]


def test_call_token_free_agent_needs_no_pairing():
    """免费 agent（没价目表）零配对可取调用凭据。

    免费本就不产生账，中继调用照样过平台（计量/公证照走），不是绕门禁。
    判定出自服务端 settlement.is_free，使用方说了不算。
    """
    from fastapi import HTTPException
    from a2n_server.routers.transport import CallTokenIn, call_token as issue_token

    free_skill = "free-" + new_id("")[2:8]
    free_card = {"name": "free-relay", "url": None,   # url 可空：relay 模式走门牌号
                 "skills": [{"id": free_skill, "name": free_skill}],
                 "x-a2n": {"deployment": {"region": "cn-east-2"}}}
    a = registry.register("acct:freep", free_card)

    # 陌生主体、零配对：免费 → 放行
    out = issue_token(CallTokenIn(agent_id=a["agent_id"]), principal="acct:stranger")
    assert out["token"] and out["agent_id"] == a["agent_id"]

    # 收费 agent：没配对仍然 403
    paid_card = {"name": "paid-relay", "url": None,
                 "skills": [{"id": "ocr-pro", "name": "ocr-pro"}],
                 "x-a2n": {"price_book": {"ocr-pro": {"CNY": {"dimensions": [
                     {"key": "call_count", "amount": 3, "per": 1}]}}}}}
    b = registry.register("acct:paidp", paid_card)
    with pytest.raises(HTTPException) as ei:
        issue_token(CallTokenIn(agent_id=b["agent_id"]), principal="acct:stranger")
    assert ei.value.status_code == 403


def test_pull_node_gets_no_url_at_all():
    """pull（NAT 后）节点连门牌号都不用给：它只出站拉单，本来就不该被直连。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:n{suffix}", _card("n-" + suffix))
    row = next(g for g in discovery.query({"skill": "ocr-pro"}, limit=50)
               if g["agent_id"] == agent["agent_id"])
    assert row["connection"]["url"] is None
