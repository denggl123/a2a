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


# ---------- 5. 资金红线：判据出自服务端、增发需持牌凭证 ----------

def test_x402_verifies_against_server_price():
    """x402 判据必须出自服务端行情：付 1 分不能过真实价的门。"""
    from a2n_custodian import get_custodian
    from a2n_gateway.gate import X402, resolve

    card = {"name": "x402-agent", "url": "http://localhost:9104/a2a",
            "skills": [{"id": "x402-skill"}],
            "x-a2n": {"accepts": [X402],
                      "price_book": {"x402-skill": {"CNY": {"dimensions": [
                          {"key": "call_count", "amount": 500, "per": 1}]}}}}}
    agent = registry.register("acct:x402p", card)
    try:
        # 只付 1 分：凭证自带 maxAmountRequired=1 也不算数 —— 要求由服务端出
        cheap = {"scheme": "exact", "signature": "sig-ok",
                 "maxAmountRequired": 1, "payload": {"amount": 1, "signature": "sig-ok"}}
        try:
            resolve(agent["agent_id"], "acct:x402u", card, payment=cheap)
            assert False, "付 1 分不该通过 5 元价的门"
        except PermissionError as e:
            assert "x402 支付凭证无效" in str(e)
    finally:
        pass


def test_deposit_requires_custodian_signature(monkeypatch):
    """充值回调无持牌方签名即拒 —— 否则任何人都能凭空增发（破廉洁铁律）。"""
    import pytest
    from fastapi import HTTPException

    from a2n_server.routers.custodian import DepositIn, deposit
    monkeypatch.delenv("A2N_DEMO_CUSTODIAN", raising=False)
    with pytest.raises(HTTPException) as ei:
        deposit(DepositIn(account_id="acct:mint", amount_fen=100))
    assert ei.value.status_code == 403 and "持牌方签名" in str(ei.value.detail)
    # 显式开演示模式才放行（管理台模拟充值）
    monkeypatch.setenv("A2N_DEMO_CUSTODIAN", "1")
    out = deposit(DepositIn(account_id="acct:mint", amount_fen=100))
    assert out["minted"] == 100


def test_a2a_cancel_rejects_non_requester():
    """A2A 取消必须验调用方：知道 task_id 不等于有权限。"""
    from a2n_server.routers.a2a import _tasks_cancel
    from a2n_task import tasks as tasksvc

    s = uuid.uuid4().hex[:8]
    owner = f"acct:own{s}"
    skill = _uniq("cancel")
    registry.register(owner, _card(skill))            # 造一个可达节点供派单
    t = tasksvc.create(owner, skill, {}, 10, hold_budget=False)
    try:
        _tasks_cancel({"id": t["id"]}, f"acct:other{s}")
        assert False, "非发起方取消应被拒"
    except PermissionError as e:
        assert "只有任务发起方能取消" in str(e)


# ---------- 6. 地址投影：拿到 card 也不能绕开 A2N 直连节点 ----------

def test_agent_address_projected_for_non_owner():
    """对外只发 A2N 中继门牌号：直连也回到平台，绕不过门禁/计量/刻章。"""
    from a2n_server.routers.registry import _project_agent
    import json

    real = "http://1.2.3.4:9000/a2a"
    card = {"name": "direct-node", "url": real, "skills": [{"id": _uniq("dir")}],
            "x-a2n": {"connection": {"mode": "direct"}}}
    a = registry.register("acct:owner1", card)
    aid = a["agent_id"]
    row = registry.get(aid)

    pub = _project_agent(row, None)                    # 匿名/他人视角
    assert json.loads(pub["card_json"])["url"] == f"/v1/relay/{aid}"
    assert pub["connection"]["url"] == f"/v1/relay/{aid}"
    assert pub["projected"] is True and pub["entry"] == f"/v1/relay/{aid}"
    assert real not in json.dumps(pub)                 # 真实地址一个字都不漏

    own = _project_agent(row, "acct:owner1")           # owner 看自己：不投影
    assert json.loads(own["card_json"])["url"] == real
    assert own.get("projected") is None


def test_verify_token_endpoint_answers_nodes():
    """节点自守门：direct 节点可拿 X-A2N-Call 问平台真伪（平台只答真伪）。"""
    from a2n_server.routers.transport import VerifyTokenIn, verify_token

    card = {"name": "vt", "url": "http://localhost:9105/a2a",
            "skills": [{"id": _uniq("vt")}]}
    a = registry.register("acct:vtp", card)
    out = verify_token(VerifyTokenIn(agent_id=a["agent_id"], token="bad-token"))
    assert out["ok"] is False and out["subject"] is None
