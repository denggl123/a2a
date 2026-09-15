"""发现路的卡片自证闸（P2，docs/OPTIMIZATION-PLAN.md §4.1）。

守的是一句话：**发现和可用必须是同一件事——"在你列表里"就等于"验过了"**。
平台路原先没有这道闸（注册完就直接派单，没人取卡验签），等于"按自报信任"。

三态必须诚实区分，不能把"没验"说成"验过"：
  - signed      卡自带身份且验签通过        → 可直接调用
  - unattested  卡没声明身份                → 能发现，但标"未自证"
  - invalid     自称了身份却验不过（冒名）    → 硬排除
  - tampered    卡哈希与注册时不一致（被改）  → 硬排除

另一条纪律：**签名域是整张卡** —— 所以自签卡必须自带 uid（平台补写会让签名失效）。
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from a2n_kernel.hashing import new_id
from a2n_p2p import Identity
from a2n_p2p.attest import card_body, pub_b64
from a2n_registry import (CARD_INVALID, CARD_TAMPERED, SELF_SIGNED,
                          UNATTESTED, card_hash, card_verdict, registry,
                          verify_card)
from a2n_registry.service import Registry
from a2n_store import conn


def _signed_card(ident: Identity, skill: str) -> dict:
    """一张自证的卡（与 a2n-node.build_card 同形状，但只依赖 a2n-p2p）。"""
    card = {
        "name": f"signed-{ident.node_id[:8]}",
        "version": "1.0.0",
        "url": f"http://127.0.0.1:{9100 + hash(skill) % 80}",
        "skills": [{"id": skill, "name": skill, "tags": [], "inputModes": ["application/json"]}],
        "x-a2n": {
            "uid": str(uuid.uuid4()),
            "node_id": ident.node_id,
            "deployment": {"region": "cn-east-2"},
            "connection": {"mode": "pull"},
            "sovereign": {"did": ident.did, "pub": pub_b64(ident.pub_raw)},
        },
    }
    card["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(card))
    return card


def _plain_card(skill: str) -> dict:
    """没声明身份的普通卡（老路径）：放行，但只是 unattested。"""
    return {
        "name": "plain-" + new_id("")[2:6],
        "version": "1.0.0",
        "url": "http://localhost/a2a",
        "skills": [{"id": skill, "name": skill, "tags": []}],
        "x-a2n": {"deployment": {"region": "cn-north-1"}},
    }


# ---------------------------------------------------------------- 判据本身

def test_verdict_three_states_are_honest():
    skill = "gate-" + new_id("")[2:6]
    ident = Identity.generate()
    signed = _signed_card(ident, skill)
    plain = _plain_card(skill)

    v = card_verdict(signed, card_hash(signed))
    assert v["verified"] is True and v["selfproof"] == SELF_SIGNED

    v = card_verdict(plain, card_hash(plain))
    assert v["verified"] is False and v["selfproof"] == UNATTESTED
    assert "未自证" in v["reason"]

    # 卡被改过（哈希对不上）→ tampered，哪怕签名本身还"看着像真的"
    v = card_verdict(signed, "deadbeef")
    assert v["verified"] is False and v["selfproof"] == CARD_TAMPERED


def test_impersonation_verdict_is_invalid_not_merely_unattested():
    """拿自己的钥匙签一张写着**别人 did** 的卡 —— 最典型的冒名。"""
    victim, attacker = Identity.generate(), Identity.generate()
    skill = "gate-" + new_id("")[2:6]
    fake = _signed_card(attacker, skill)
    # 把 did 换成受害者的（公钥还是攻击者的）→ 身份自洽那一步必须拦住
    fake["x-a2n"]["sovereign"]["did"] = victim.did
    fake["x-a2n"]["sovereign"]["sig"] = attacker.sign(card_body(fake))

    ok, why = verify_card(fake)
    assert not ok and "did 与公钥指纹不符" in why
    assert card_verdict(fake)["selfproof"] == CARD_INVALID


# ---------------------------------------------------------------- 注册路

def test_register_rejects_impersonating_card():
    victim, attacker = Identity.generate(), Identity.generate()
    skill = "gate-" + new_id("")[2:6]
    fake = _signed_card(attacker, skill)
    fake["x-a2n"]["sovereign"]["did"] = victim.did
    fake["x-a2n"]["sovereign"]["sig"] = attacker.sign(card_body(fake))
    with pytest.raises(ValueError, match="自证不通过"):
        Registry().register("acct:attacker", fake)


def test_register_rejects_signed_card_without_uid():
    """uid 属于签名域：平台补写会让签名失效，所以自签卡必须自己带。"""
    ident = Identity.generate()
    skill = "gate-" + new_id("")[2:6]
    card = _signed_card(ident, skill)
    card["x-a2n"].pop("uid")
    card["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(card))
    with pytest.raises(ValueError, match="必须自带 x-a2n.uid"):
        Registry().register("acct:who", card)


def test_signed_card_registers_and_stays_verifiable():
    """整卡签名必须经得起"注册时平台没改卡"这一条：注册后哈希与签名都对得上。"""
    skill = "gate-" + new_id("")[2:6]
    ident = Identity.generate()
    card = _signed_card(ident, skill)
    agent = registry.register(f"acct:{new_id('')[:6]}", card)

    stored = json.loads(agent["card_json"])
    assert agent["card_hash"] == card_hash(stored)
    v = card_verdict(stored, agent["card_hash"])
    assert v["verified"] is True and v["selfproof"] == SELF_SIGNED


def test_plain_card_registers_as_unattested():
    skill = "gate-" + new_id("")[2:6]
    agent = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))
    v = card_verdict(json.loads(agent["card_json"]), agent["card_hash"])
    assert v["selfproof"] == UNATTESTED and v["verified"] is False


# ---------------------------------------------------------------- 发现路

def test_discovery_excludes_tampered_card_but_keeps_others():
    """直接改库里的 card_json（不重算哈希）= 入库后被改 → 不许出现在列表里。"""
    from a2n_dispatch import discovery

    skill = "gate-" + new_id("")[2:6]
    good = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))
    bad = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))

    tampered = copy.deepcopy(json.loads(bad["card_json"]))
    tampered["name"] = "偷偷改弱的能力"          # 改了内容，卡哈希没跟着重算
    conn().execute("UPDATE agents SET card_json=? WHERE agent_id=?",
                   (json.dumps(tampered, ensure_ascii=False), bad["agent_id"]))
    conn().commit()

    hits = {r["agent_id"] for r in discovery.query({"skill": skill}, limit=50)}
    assert good["agent_id"] in hits
    assert bad["agent_id"] not in hits           # 被改过的卡：硬排除


def test_discovery_excludes_card_claiming_someone_elses_identity():
    """把冒名卡硬塞进库（连哈希一起改）→ 发现路仍然凭签名拦下。"""
    from a2n_dispatch import discovery

    skill = "gate-" + new_id("")[2:6]
    victim, attacker = Identity.generate(), Identity.generate()
    fake = _signed_card(attacker, skill)
    fake["x-a2n"]["sovereign"]["did"] = victim.did
    fake["x-a2n"]["sovereign"]["sig"] = attacker.sign(card_body(fake))

    aid = new_id("ag")
    conn().execute(
        "INSERT INTO agents (agent_id, principal_id, type, status, kya_grade, visibility,"
        " card_hash, card_json, name, reputation, tasks_done, earned, registered_at,"
        " compute, sla, metering, connection)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "acct:x", "node", "ACTIVE", "B", "public", card_hash(fake),
         json.dumps(fake, ensure_ascii=False), fake["name"], 0.5, 0, 0, "2026-01-01T00:00:00Z",
         "{}", "{}", "{}", "{}"))
    conn().execute("INSERT INTO skills (agent_id, skill_id, skill_version, tags, input_modes)"
                   " VALUES (?,?,?,?,?)", (aid, skill, "1.0.0", "[]", "[]"))
    conn().commit()

    assert aid not in {r["agent_id"] for r in discovery.query({"skill": skill}, limit=50)}
    # 但"直接问它"仍回结论（不是静默消失），并写明是冒名
    ok, why = verify_card(fake)
    assert not ok and "指纹不符" in why


def test_discovery_marks_verified_and_unattested_side_by_side():
    from a2n_dispatch import discovery

    skill = "gate-" + new_id("")[2:6]
    ident = Identity.generate()
    signed = registry.register(f"acct:{new_id('')[:6]}", _signed_card(ident, skill))
    plain = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))

    rows = {r["agent_id"]: r for r in discovery.query({"skill": skill}, limit=50)}
    assert rows[signed["agent_id"]]["card_verified"] is True
    assert rows[signed["agent_id"]]["selfproof"] == SELF_SIGNED
    assert rows[plain["agent_id"]]["card_verified"] is False
    assert rows[plain["agent_id"]]["selfproof"] == UNATTESTED


def test_require_selfproof_flag_filters_unattested():
    from a2n_dispatch import discovery

    skill = "gate-" + new_id("")[2:6]
    ident = Identity.generate()
    signed = registry.register(f"acct:{new_id('')[:6]}", _signed_card(ident, skill))
    plain = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))

    strict = {r["agent_id"] for r in
              discovery.query({"skill": skill}, limit=50, require_selfproof=True)}
    assert strict == {signed["agent_id"]}
    assert plain["agent_id"] not in strict


# ---------------------------------------------------------------- 派单路

def test_assignable_rejects_tampered_card():
    from a2n_dispatch import discovery

    skill = "gate-" + new_id("")[2:6]
    a = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))
    ok, _ = discovery.assignable(a["agent_id"])
    assert ok                                     # 未自证的普通卡照旧可派单

    tampered = copy.deepcopy(json.loads(a["card_json"]))
    tampered["name"] = "改过"
    conn().execute("UPDATE agents SET card_json=? WHERE agent_id=?",
                   (json.dumps(tampered, ensure_ascii=False), a["agent_id"]))
    conn().commit()

    ok, why = discovery.assignable(a["agent_id"])
    assert not ok and "自身凭据" in why


# ---------------------------------------------------------------- 同一份实现

def test_node_build_card_is_verified_by_the_same_shared_check():
    """自持模式的 build_card 与平台路的验卡是**同一份实现**（转出口，不是复制）。"""
    import a2n_node.card as nodecard

    assert nodecard.verify_card is verify_card            # 同一个函数对象
    assert nodecard.card_body is card_body                # 同一个函数对象

    ident = Identity.generate()
    card = nodecard.build_card(ident, name="自持节点", skills=["s1"], port=9201)
    ok, why = nodecard.verify_card(card)
    assert ok, why
    # 自持路要直连地址；平台路不看这一条。url 也在签名域里 —— 所以抹 url
    # 必须**重新签名**，否则先炸的是签名（这本身就证明签名域盖住了 url）。
    hollow = copy.deepcopy(card)
    hollow["url"] = ""
    hollow["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(hollow))
    ok, why = verify_card(hollow)
    assert ok, why                                   # 平台路：url 可空（走中继门牌号）
    ok, why = verify_card(hollow, require_endpoint=True)
    assert not ok and "可直连地址" in why             # 自持路：没有中继可退，必须能直连


# ---------------------------------------------------------------- 接口层

def test_registry_endpoint_exposes_verdict_and_hides_rejected():
    from fastapi.testclient import TestClient
    from a2n_server.app import app

    c = TestClient(app)
    skill = "gate-" + new_id("")[2:6]
    ident = Identity.generate()
    signed = registry.register(f"acct:{new_id('')[:6]}", _signed_card(ident, skill))

    row = c.get(f"/v1/registry/agents/{signed['agent_id']}").json()
    assert row["card_verified"] is True and row["selfproof"] == SELF_SIGNED

    # 一张被改过的卡：别人的列表里看不见
    bad = registry.register(f"acct:{new_id('')[:6]}", _plain_card(skill))
    tampered = copy.deepcopy(json.loads(bad["card_json"]))
    tampered["name"] = "改过"
    conn().execute("UPDATE agents SET card_json=? WHERE agent_id=?",
                   (json.dumps(tampered, ensure_ascii=False), bad["agent_id"]))
    conn().commit()
    listed = {r["agent_id"] for r in c.get("/v1/registry/agents").json()}
    assert bad["agent_id"] not in listed
