"""上架名额（"允许被发现的数量"）：限额、占名、续期、闲置释放、派单硬拦、可见性。

它约束的是**同时**在用它的人数，不是累计人次 —— 所以每条断言问的都是
"此刻谁看得见 / 谁能下单"，而不是"一共来过几个人"。

技能 id 一律用唯一值：这类测试最怕"其实选了别人"（同一个候选池里混着别的
用例注册的 agent），唯一技能就把候选池收成一条。
"""
from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from a2n_dispatch import discovery
from a2n_kernel.errors import ValidationError
from a2n_ledger import Ledger
from a2n_registry import registry, seats
from a2n_server.routers.custodian import DepositIn, deposit
from a2n_store import conn
from a2n_task import tasks
from tests.test_flow import demo_card


def _client() -> TestClient:
    from a2n_server.app import app
    return TestClient(app)


def _skill(tag: str) -> str:
    return f"seat-{tag}-{uuid.uuid4().hex[:6]}"


def _u(tag: str) -> str:
    return f"acct:seat-{tag}-{uuid.uuid4().hex[:6]}"


def _put(skill: str, limit: int | None = None) -> dict:
    """上一个 agent（可带名额），并让它可达。"""
    a = registry.register(_u("p"), demo_card("seats-" + skill, skill=skill), "public", limit)
    registry.heartbeat(a["agent_id"])
    return a


def _order(user: str, skill: str, agent_id: str, budget: int = 100) -> dict:
    deposit(DepositIn(account_id=user, amount_fen=budget))
    return tasks.create(user, skill, {"x": 1}, budget=budget, preferred_agents=[agent_id])


def _visible(viewer: str | None, skill: str) -> set[str]:
    rows = discovery.query({"skill": skill}, limit=50, include_unlisted=True, viewer=viewer)
    return {r["agent_id"] for r in rows}


# ---------- 限额与默认 ----------

def test_default_is_unlimited_backward_compatible():
    """不声明 = 不限：老 agent 的上架行为一个字不变。"""
    skill = _skill("unl")
    a = _put(skill)
    u = seats.usage(a["agent_id"])
    assert u["limit"] == 0 and u["unlimited"] and u["full"] is False
    for i in range(3):
        assert a["agent_id"] in _visible(_u(f"v{i}"), skill)


def test_card_declared_limit_is_adopted_and_param_overrides():
    """卡里能声明（那份是供给方自己签的）；显式参数优先。"""
    skill = _skill("decl")
    card = demo_card("seats-decl", skill=skill)
    card["x-a2n"]["discover_limit"] = 2
    a = registry.register(_u("p"), card)
    assert seats.limit_of(a["agent_id"]) == 2

    skill2 = _skill("over")
    card2 = demo_card("seats-over", skill=skill2)
    card2["x-a2n"]["discover_limit"] = 2
    b = registry.register(_u("p"), card2, "public", 5)
    assert seats.limit_of(b["agent_id"]) == 5


# ---------- 名额：占、续期、释放 ----------

def test_full_hides_from_newcomer_but_stays_visible_to_holder():
    """满员 = 不再发给新使用者；但**正在用它的人**不能凭空看不到它。"""
    skill = _skill("one")
    a = _put(skill, limit=1)
    aid = a["agent_id"]
    holder, newcomer = _u("holder"), _u("new")
    _order(holder, skill, aid)

    assert aid in _visible(holder, skill), "已在用的人必须还看得见"
    assert aid not in _visible(newcomer, skill), "新使用者不该再被发到它"

    u = seats.usage(aid, viewer=holder)
    assert u["full"] and u["used"] == 1 and u["left"] == 0 and u["mine"] is True


def test_same_holder_renews_instead_of_double_counting():
    """一个人占一个名额：再来一单是续期，不是又占一个。"""
    skill = _skill("renew")
    a = _put(skill, limit=2)
    holder = _u("h")
    _order(holder, skill, a["agent_id"])
    _order(holder, skill, a["agent_id"])
    assert seats.usage(a["agent_id"])["used"] == 1


def test_idle_seat_is_released_and_reusable():
    """闲置即释放：压的是"同时"，所以一个名额能被下一个人接着用。"""
    skill = _skill("idle")
    a = _put(skill, limit=1)
    aid = a["agent_id"]
    first, second = _u("1st"), _u("2nd")
    _order(first, skill, aid)
    assert aid not in _visible(second, skill)

    # 把租约拨到过去 = 闲置到期（不去 sleep，也不改系统时钟）
    conn().execute("UPDATE discovery_seats SET expires_at=? WHERE agent_id=?",
                   ("2000-01-01T00:00:00Z", aid))
    conn().commit()
    assert seats.usage(aid)["used"] == 0
    assert aid in _visible(second, skill)
    assert seats.sweep() >= 1                      # 回收确实发生了
    assert conn().execute("SELECT COUNT(*) n FROM discovery_seats WHERE agent_id=?",
                          (aid,)).fetchone()["n"] == 0


def test_assignable_blocks_newcomer_when_full():
    skill = _skill("asg")
    a = _put(skill, limit=1)
    holder, newcomer = _u("h"), _u("n")
    _order(holder, skill, a["agent_id"])

    ok, why = discovery.assignable(a["agent_id"], principal=newcomer)
    assert not ok and "名额已满" in why
    ok2, _ = discovery.assignable(a["agent_id"], principal=holder)
    assert ok2, "已在用的人派单不该被名额挡住"


def test_create_is_rejected_without_side_effects_when_full():
    """满员的新使用者下单必须整体失败：不留任务、不冻预算。"""
    skill = _skill("roll")
    a = _put(skill, limit=1)
    _order(_u("h"), skill, a["agent_id"])

    newcomer = _u("late")
    deposit(DepositIn(account_id=newcomer, amount_fen=100))
    before_tasks = conn().execute(
        "SELECT COUNT(*) n FROM tasks WHERE requester_id=?", (newcomer,)).fetchone()["n"]
    with pytest.raises(ValueError) as ei:
        tasks.create(newcomer, skill, {"x": 1}, budget=100,
                     preferred_agents=[a["agent_id"]])
    # 说清是哪一种"没候选"：有节点，但都不接新使用者（不是"全网没有这能力"）
    assert "不接新使用者" in str(ei.value)
    assert conn().execute("SELECT COUNT(*) n FROM tasks WHERE requester_id=?",
                          (newcomer,)).fetchone()["n"] == before_tasks
    assert Ledger().balance(newcomer) == 100, "预算冻结必须跟着回滚"


def test_concurrent_take_lets_exactly_one_win():
    """并发抢最后一个名额：恰好一个人拿到（其余按满员被拒，不是都拿在手里）。"""
    skill = _skill("race")
    a = _put(skill, limit=1)
    aid = a["agent_id"]
    got: list[bool] = []
    lock = threading.Lock()

    def grab(user: str) -> None:
        try:
            ok, _ = seats.take(aid, user)
        except Exception:      # noqa: BLE001 - 写锁竞争被挡下也算"没拿到"
            ok = False
        with lock:
            got.append(ok)

    ts = [threading.Thread(target=grab, args=(_u(f"r{i}"),)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sum(got) == 1, f"恰好一个人该拿到名额，实际 {got}"
    assert seats.usage(aid)["used"] == 1


# ---------- 形状、名单、改上架 ----------

def test_shape_validation_rejects_negative_and_bool():
    skill = _skill("bad")
    with pytest.raises(ValidationError):
        registry.register(_u("p"), demo_card("seats-bad", skill=skill), "public", -1)
    with pytest.raises(ValidationError):
        registry.register(_u("p"), demo_card("seats-bad2", skill=_skill("bad2")),
                          "public", True)
    card = demo_card("seats-bad3", skill=_skill("bad3"))
    card["x-a2n"]["discover_limit"] = -3
    with pytest.raises(ValidationError):
        registry.register(_u("p"), card)
    with pytest.raises(ValueError):
        seats.set_limit(_put(_skill("bad4"))["agent_id"], -1)


def test_holders_list_is_opt_in():
    """占用者名单是使用者的身份：只有明着要（owner 视角）才给。"""
    skill = _skill("who")
    a = _put(skill, limit=3)
    holder = _u("h")
    _order(holder, skill, a["agent_id"])
    assert "holders" not in seats.usage(a["agent_id"])
    holders = seats.usage(a["agent_id"], with_holders=True)["holders"]
    assert [h["principal_id"] for h in holders] == [holder]


def test_set_listing_owner_only_and_effective():
    c = _client()
    owner, other = _u("own"), _u("oth")
    skill = _skill("http")
    r = c.post("/v1/registry/agents", json={"card": demo_card("seats-http", skill=skill),
                                            "visibility": "public",
                                            "discover_limit": 2},
               headers={"X-Principal": owner})
    assert r.status_code == 200, r.text
    aid = r.json()["agent_id"]

    bad = c.put(f"/v1/registry/agents/{aid}/listing", json={"discover_limit": 9},
                headers={"X-Principal": other})
    assert bad.status_code == 403

    ok = c.put(f"/v1/registry/agents/{aid}/listing", json={"discover_limit": 9},
               headers={"X-Principal": owner})
    assert ok.status_code == 200 and ok.json()["discover_limit"] == 9

    bad_shape = c.put(f"/v1/registry/agents/{aid}/listing", json={"visibility": "everyone"},
                      headers={"X-Principal": owner})
    assert bad_shape.status_code == 400


def test_public_list_hides_full_and_unlisted_but_direct_ask_explains():
    """发现页走 /v1/registry/agents?scope=all：满员的与不公开名单的都不出现；
    但直接点名问它仍然答得出来，并写清为什么别人看不到。"""
    c = _client()
    owner, holder, newcomer = _u("o"), _u("h"), _u("n")
    skill = _skill("scope")
    aid = _put(skill, limit=1)["agent_id"]
    _order(holder, skill, aid)

    listed = {a["agent_id"] for a in c.get("/v1/registry/agents", params={"scope": "all"},
                                           headers={"X-Principal": newcomer}).json()}
    assert aid not in listed
    listed_for_holder = {a["agent_id"] for a in
                         c.get("/v1/registry/agents", params={"scope": "all"},
                               headers={"X-Principal": holder}).json()}
    assert aid in listed_for_holder, "名额是我的 → 名单里还得有它"

    one = c.get(f"/v1/registry/agents/{aid}", headers={"X-Principal": newcomer})
    assert one.status_code == 200
    assert one.json()["seats"]["full"] is True

    # unlisted = 不进公开名单（但点名仍然能拿到）
    skill2 = _skill("unl2")
    a2 = registry.register(_u("p2"), demo_card("seats-unl", skill=skill2), "unlisted")
    listed2 = {a["agent_id"] for a in
               c.get("/v1/registry/agents", params={"scope": "all"},
                     headers={"X-Principal": newcomer}).json()}
    assert a2["agent_id"] not in listed2
    assert c.get(f"/v1/registry/agents/{a2['agent_id']}").status_code == 200


def test_discovery_rows_carry_the_same_seat_fact_as_registry():
    """**同一件事不能长两张脸**：发现行与 registry 列表必须带同一份名额事实。

    这个洞真出现过：dispatch 里那次 `seats.expose` 只被用来判"能不能被发现"，
    算出来的 seats 被丢掉。控制台走 registry 列表 → 看着一切正常；
    SDK / CLI / 派单候选走发现行 → 把**每个** agent 都印成「不限」。
    最刺眼的是：供给方刚声明了名额 2，转头在发现页看到自己是"不限"。
    """
    c = _client()
    owner, holder = _u("o2"), _u("h2")
    skill = _skill("face")
    aid = _put(skill, limit=2)["agent_id"]

    def _find(rows):
        return next(r for r in rows if r["agent_id"] == aid)

    row = _find(discovery.query({"skill": skill}, limit=50))
    assert row.get("seats") is not None, "发现行必须带名额（丢了就会印成『不限』）"
    assert row["seats"]["limit"] == 2 and row["seats"]["used"] == 0

    # 占一个之后发现行也得跟着涨 —— 买家要靠它决定"现在排不排"
    _order(holder, skill, aid)
    row2 = _find(discovery.query({"skill": skill}, limit=50, viewer=holder))
    assert row2["seats"]["used"] == 1 and row2["seats"]["mine"] is True

    # 与控制台那条路同源：同一时刻、同一个数字
    listed = _find(c.get("/v1/registry/agents", params={"scope": "all"},
                         headers={"X-Principal": owner}).json())
    assert listed["seats"]["limit"] == row2["seats"]["limit"] == 2
    assert listed["seats"]["used"] == row2["seats"]["used"] == 1

    # HTTP 发现端点也是这条路（SDK discover / deals 的 peer-ready 都走它）
    http = _find(c.post("/v1/discovery/query",
                        json={"require": {"skill": skill}, "limit": 50}).json())
    assert http["seats"]["limit"] == 2, "发现端点丢了名额 = SDK 会印『不限』"

    # 不限名额的行给**同形状**（unlimited=True），不是 None —— 否则界面又要猜
    free_skill = _skill("free")
    _put(free_skill, limit=None)
    frow = next(r for r in discovery.query({"skill": free_skill}, limit=50))
    assert frow["seats"]["unlimited"] is True and frow["seats"]["limit"] == 0


def test_sdk_discover_never_claims_unlimited_for_a_limited_agent():
    """CLI 投影不许把"没有名额字段"翻译成「不限」——那是主动说一句可能为假的话。"""
    from a2n_sdk.__main__ import _slim_found

    skill = _skill("cli")
    aid = _put(skill, limit=2)["agent_id"]
    row = next(r for r in discovery.query({"skill": skill}, limit=50))
    slim = _slim_found(row)
    assert slim["seats"] != "不限", "声明了 2 个名额却被印成「不限」"
    assert slim["seats"] == "0/2"
