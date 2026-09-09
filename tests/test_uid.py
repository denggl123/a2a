"""网络唯一标识（uid）：UUID 级全网唯一，平台背书。

- 注册不带 uid → 平台兜底生成 UUID 并写回 card（card_hash 覆盖最终卡）
- 注册带自定义 uid → 保留；同 uid 二次注册 → 拒绝
- 整卡更新改 uid → 拒绝（身份不可变）；旧节点未背书时允许首次补录
"""
import json
import uuid

import pytest

from a2n_registry.service import Registry


def _card(name="u", uid=None):
    card = {
        "name": name, "url": "http://localhost:9000/a2a",
        "skills": [{"id": "s1", "name": "技能一", "tags": []}],
    }
    if uid:
        card["x-a2n"] = {"uid": uid}
    return card


def test_register_generates_uuid_when_missing():
    r = Registry().register("p1", _card())
    uid = json.loads(r["card_json"])["x-a2n"]["uid"]
    assert uuid.UUID(uid)  # 合法 UUID


def test_register_keeps_custom_uid():
    my = str(uuid.uuid4())
    r = Registry().register("p1", _card("u1", my))
    assert json.loads(r["card_json"])["x-a2n"]["uid"] == my


def test_duplicate_uid_rejected():
    reg = Registry()
    my = str(uuid.uuid4())
    reg.register("p1", _card("a", my))
    with pytest.raises(ValueError, match="uid 已被其他 agent 占用"):
        reg.register("p2", _card("b", my))


def test_update_card_cannot_change_uid():
    reg = Registry()
    my = str(uuid.uuid4())
    r = reg.register("p1", _card("a", my))
    card = json.loads(r["card_json"])
    card["x-a2n"]["uid"] = str(uuid.uuid4())   # 换身份
    with pytest.raises(ValueError, match="不可修改"):
        reg.update_card(r["agent_id"], card)


def test_update_card_backfills_uid_for_legacy():
    """旧库存量节点（uid 列为 NULL）首次整卡更新时允许补录。"""
    reg = Registry()
    r = reg.register("p1", _card("legacy"))
    # 模拟旧库：uid 列与 card 里的 uid 都清掉
    from a2n_store import conn
    c = conn()
    c.execute("UPDATE agents SET uid=NULL WHERE agent_id=?", (r["agent_id"],))
    card = json.loads(r["card_json"])
    card["x-a2n"].pop("uid")
    c.execute("UPDATE agents SET card_json=? WHERE agent_id=?",
              (json.dumps(card, ensure_ascii=False), r["agent_id"]))
    c.commit()
    # 补录新 uid：允许；旧节点补录时占用他人 uid：拒绝
    mine = str(uuid.uuid4())
    card["x-a2n"]["uid"] = mine
    reg.update_card(r["agent_id"], card)
    assert reg.get(r["agent_id"])["uid"] == mine
    other = reg.register("p2", _card("other"))
    c.execute("UPDATE agents SET uid=NULL WHERE agent_id=?", (other["agent_id"],))
    c.commit()
    card2 = json.loads(other["card_json"])
    card2["x-a2n"].pop("uid")
    card2["x-a2n"]["uid"] = mine
    with pytest.raises(ValueError, match="已被其他 agent 占用"):
        reg.update_card(other["agent_id"], card2)
