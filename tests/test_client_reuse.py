"""重启幂等上架：同 uid 第二次上架必须复用那一条，不是 409、也不是新增。

守的是一句话：**节点重启不该死，也不该在发现页上多出一份重复供给。**

真实背景（2026-09-25 实测）：保留平台数据重启本机节点时，
`Node.serve` 走的是 `Client.register`，平台对重复 uid 直接 409，
于是本机节点整个起不来；而三个演示容器因为各自在脚本里写了一份
"查我的上架 → 命中 uid 就 update"的兜底，才没踩到。这说明幂等逻辑
本该在 SDK 层，而不是散在演示脚本里。
"""
from __future__ import annotations

import json
import uuid

import pytest

from a2n_kernel.hashing import new_id
from a2n_p2p import Identity
from a2n_p2p.attest import card_body, pub_b64
from a2n_sdk.client import Client


def _signed_card(ident: Identity, skill: str, uid: str) -> dict:
    card = {
        "name": f"local-{skill}",
        "version": "1.0.0",
        "url": f"http://127.0.0.1:{9100 + hash(skill) % 80}",
        "skills": [{"id": skill, "name": skill, "tags": [], "inputModes": ["application/json"]}],
        "x-a2n": {
            "uid": uid,
            "node_id": ident.node_id,
            "deployment": {"region": "cn-east-2"},
            "connection": {"mode": "pull"},
            "sovereign": {"did": ident.did, "pub": pub_b64(ident.pub_raw)},
        },
    }
    card["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(card))
    return card


def _client(principal: str) -> Client:
    """一个真打平台的 Client —— 走 TestClient 进真实 app，不走假桩。"""
    from fastapi.testclient import TestClient
    from a2n_server.app import app

    transport = TestClient(app)
    cli = Client("http://platform.invalid", principal=principal)

    def _req(method, path, body=None, headers=None):
        r = transport.request(method, path, json=body,
                             headers={"X-Principal": principal})
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    cli._req = _req
    return cli


def _count_uid(agent_id_holder: dict, principal: str) -> int:
    cli = _client(principal)
    uid = agent_id_holder["uid"]
    hits = 0
    for row in cli.my_agents():
        if (json.loads(row["card_json"]).get("x-a2n") or {}).get("uid") == uid:
            hits += 1
    return hits


def test_second_register_of_same_uid_is_rejected():
    """先把"残酷事实"钉住：裸 register 第二次必 409（平台设计如此）。"""
    principal = f"acct:{new_id('')[:6]}"
    cli = _client(principal)
    uid = str(uuid.uuid4())
    card = _signed_card(Identity.generate(), "reuse-a", uid)
    cli.register(card)
    with pytest.raises(RuntimeError) as err:
        cli.register(card)
    assert "409" in str(err.value)


def test_register_or_update_reuses_the_same_listing():
    principal = f"acct:{new_id('')[:6]}"
    cli = _client(principal)
    uid = str(uuid.uuid4())
    ident = Identity.generate()
    card = _signed_card(ident, "reuse-b", uid)

    first = cli.register_or_update(card)
    again = cli.register_or_update(card)

    assert again["agent_id"] == first["agent_id"], "重启后必须是同一条上架"
    assert _count_uid({"uid": uid}, principal) == 1, "不许出现重复供给"


def test_register_or_update_carries_a_changed_card_through_update():
    """改内容再上架（同 uid）走 update 分支：卡是新卡，条目还是原来那条。"""
    principal = f"acct:{new_id('')[:6]}"
    cli = _client(principal)
    uid = str(uuid.uuid4())
    ident = Identity.generate()
    card = _signed_card(ident, "reuse-c", uid)
    first = cli.register_or_update(card)

    card["name"] = "改过名字的同一档"
    card["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(card))
    again = cli.register_or_update(card)

    assert again["agent_id"] == first["agent_id"]
    assert json.loads(again["card_json"])["name"] == "改过名字的同一档"


def test_register_or_update_still_registers_a_new_uid():
    principal = f"acct:{new_id('')[:6]}"
    cli = _client(principal)
    a = cli.register_or_update(_signed_card(Identity.generate(), "reuse-d1", str(uuid.uuid4())))
    b = cli.register_or_update(_signed_card(Identity.generate(), "reuse-d2", str(uuid.uuid4())))
    assert a["agent_id"] != b["agent_id"]


def test_register_or_update_does_not_claim_someone_elses_uid():
    """别人的 uid 撞上了不许被"顺手改成我的" —— 仍然交给平台拒绝。"""
    owner = f"acct:{new_id('')[:6]}"
    other = f"acct:{new_id('')[:6]}"
    uid = str(uuid.uuid4())
    _client(owner).register(_signed_card(Identity.generate(), "reuse-e", uid))
    with pytest.raises(RuntimeError) as err:
        _client(other).register_or_update(_signed_card(Identity.generate(), "reuse-e", uid))
    assert "409" in str(err.value)
