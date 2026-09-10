"""四化改造的护栏：扩展点注册、卡结构校验、领域异常的 HTTP 映射。

发展化的意义在测试里体现：注册一个"测试结算方式"、换一个验收策略，
不改任何分派主体 —— 这就是以后加"预付卡/订阅制/按结果付费"的样子。
"""
from __future__ import annotations

import uuid

import pytest

from a2n_acceptance import BaselineSamplePolicy, set_policy
from a2n_gateway import settle as settle_mod
from a2n_kernel.errors import ConflictError, ValidationError
from a2n_registry import registry
from a2n_registry.service import validate_card


def _card(skill: str, **ext_extra) -> dict:
    return {"name": "t-" + skill, "url": "http://localhost:9000/a2a",
            "skills": [{"id": skill, "tags": []}], "x-a2n": {**ext_extra}}


def _uniq(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:8]}"


# ---------- 1. 结算方式：注册即扩展 ----------

def test_register_settler_extends_dispatch():
    seen = {}

    def fake_settler(*, cap, task, **kw):
        seen["task_id"] = task["id"]
        return {"kind": "test_mode", "id": "x", "state": "OK"}

    mode = _uniq("mode")
    settle_mod.register_settler(mode, fake_settler)
    cap = settle_mod.Capability(mode=mode, currency="CNY")
    out = settle_mod.dispatch(cap, {"id": "t1"}, "ag_x", "p", "s", 3)
    assert out == {"kind": "test_mode", "id": "x", "state": "OK"}
    assert seen["task_id"] == "t1"
    del settle_mod.SETTLERS[mode]          # 测试自清理

    unknown = settle_mod.Capability(mode=_uniq("none"), currency="CNY")
    assert settle_mod.dispatch(unknown, {"id": "t2"}, "ag_x", "p", "s", 3) == \
        {"kind": "none", "id": None, "state": None}


# ---------- 2. 验收策略：替换即扩展 ----------

def test_acceptance_policy_replaceable():
    class AlwaysPass:
        key = "test.always_pass"
        version = "0"

        def judge(self, task, usage, result_hash, started_at):
            return {"passed": True, "score": 1.0, "reasons": [], "policy": self.key}

    old = BaselineSamplePolicy()
    set_policy(AlwaysPass())
    from a2n_acceptance import judge
    try:
        out = judge({"sla": "{}"}, {"wall_time_ms": 1}, "hash", None)
        assert out["passed"] is True and out["policy"] == "test.always_pass"
    finally:
        set_policy(old)                    # 恢复默认，不污染其他测试
    assert judge({"sla": "{}"}, None, None, None)["passed"] is False


# ---------- 3. 卡结构校验：三条上架路径同一形状 ----------

def test_validate_card_accepts_minimal():
    validate_card(_card("s"))              # 不抛即过


def test_validate_card_rejects_bad_shapes():
    with pytest.raises(ValidationError, match="缺少必填字段"):
        validate_card({"name": "x"})
    with pytest.raises(ValidationError, match="url"):
        validate_card({"name": "x", "url": "ftp://x", "skills": [{"id": "s"}]})
    with pytest.raises(ValidationError, match="非空数组"):
        validate_card({"name": "x", "url": "http://a", "skills": []})
    with pytest.raises(ValidationError, match="UUID"):
        validate_card(_card("s", uid="not-a-uuid"))
    with pytest.raises(ValidationError, match="amount"):
        validate_card(_card("s", price_book={"s": {"CNY": {"dimensions": [
            {"key": "call_count", "amount": 0, "per": 1}]}}}))
    with pytest.raises(ValidationError, match="per"):
        validate_card(_card("s", price_book={"s": {"CNY": {"dimensions": [
            {"key": "call_count", "amount": 3, "per": 0}]}}}))


def test_register_rejects_bad_uid_with_validation_error():
    with pytest.raises(ValidationError, match="UUID"):
        registry.register("p-bad", _card(_uniq("s"), uid="bad"))


def test_duplicate_uid_is_conflict():
    my = str(uuid.uuid4())
    registry.register("p-c1", _card(_uniq("s1"), uid=my))
    with pytest.raises(ConflictError, match="占用"):
        registry.register("p-c2", _card(_uniq("s2"), uid=my))


# ---------- 4. REST 层映射：冲突 409 而不是 400 ----------

def test_rest_maps_conflict_to_409():
    from fastapi.testclient import TestClient
    from a2n_server.app import app

    client = TestClient(app)
    my = str(uuid.uuid4())
    card = _card(_uniq("s-rest"))
    card["x-a2n"]["uid"] = my
    r1 = client.post("/v1/registry/agents", json={"card": card},
                     headers={"X-Principal": "std-test"})
    assert r1.status_code == 200, r1.text
    r2 = client.post("/v1/registry/agents", json={"card": card},
                     headers={"X-Principal": "std-test"})
    assert r2.status_code == 409, r2.text      # 同 uid 二次注册：409 而非 400
