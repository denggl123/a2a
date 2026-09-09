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
        "x-a2n": {"compute": {"gpu": "4090", "vram_gb": 24, "region": "cn-east-2"},
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


def test_pull_node_gets_no_url_at_all():
    """pull（NAT 后）节点连门牌号都不用给：它只出站拉单，本来就不该被直连。"""
    suffix = new_id("")[2:6]
    agent = registry.register(f"acct:n{suffix}", _card("n-" + suffix))
    row = next(g for g in discovery.query({"skill": "ocr-pro"}, limit=50)
               if g["agent_id"] == agent["agent_id"])
    assert row["connection"]["url"] is None
