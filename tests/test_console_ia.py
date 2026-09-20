"""控制台信息架构改版的后端支撑：行情板、他人调用记录、调用明细。

这一层锁四件事：

  1. **行情不跨币种混合**：同一能力的 CNY 成交均价与 USDC 成交均价是两个
     量纲（分 vs 10⁻⁶）。混着求平均算出来的数没有任何解释力 —— 与
     6e47f7c 收紧 `unit_price_of` 跨币种回退是同一个理由。
  2. **口径单一**：完成数一律用两种终态（SETTLED 积分结算完 / ACCEPTED
     非积分完成）；成功率的分母是"被点名多少次"（含还没派单的），
     不是一个只对成功任务成立的自洽数字。
  3. **行情不泄漏主体**：公开接口里一个主体标识都不许有 —— 行情不能变成
     绕开供应商匿名收口的旁路。
  4. **逐笔明细按行为相关方鉴权**：requester 与 node owner 各看各的视角，
     第三方 403。task_id 不可猜不假，匿名无限枚举能把整网交易量摸出来。
  5. **取数口与投影契约**：业务面投影只吃已解码的 dict（`registry.get`），
     裸行喂进来当场 TypeError —— "静默放行"会把类型错伪装成正常结果，
     只在恰好有数据时炸，空列表时永远绿。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from a2n_account import paymethods
from a2n_kernel.hashing import new_id, now_iso
from a2n_notary import notary
from a2n_registry import registry
from a2n_store import conn
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


def _mk_task(skill: str, node_id: str | None, requester: str, state: str,
             currency: str = "CNY", amount_minor: int = 0) -> str:
    """直接落一条终态任务。

    行情板是**只读聚合**，被测的是算术；为了造出"同一能力两种币种都成交过"
    这种状态去跑一遍完整调用链，只会让测试更脆、更慢，且测不到更多东西。
    """
    tid, ts = new_id("t"), now_iso()
    conn().execute(
        "INSERT INTO tasks (id, requester_id, skill_id, source, payload_hash, payload, budget,"
        " unit_prices, card_hash, state, node_id, delivery, result_hash, result, amount,"
        " currency, amount_minor, budget_minor, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, requester, skill, "native", "h", "{}", amount_minor, "{}", "c", state,
         node_id, "inline", None, None, amount_minor, currency, amount_minor,
         amount_minor, ts, ts))
    conn().commit()
    return tid


@pytest.fixture
def stub_forward(monkeypatch):
    """通道转发就地成功（inline 交付，不用真起节点）。"""
    monkeypatch.setattr(hub, "forward", lambda *a, **k: {
        "status": 200, "body": {"body": {"answer": "42"}}})


def _board_skill(c: TestClient, skill: str) -> dict | None:
    rows = c.get("/v1/market/board").json()["skills"]
    return next((s for s in rows if s["skill_id"] == skill), None)


# ---------------- 行情板 ----------------

def test_market_board_separates_currencies():
    """同一能力的两种币种各占一行，绝不并成一个均价。"""
    skill = "mk-" + new_id("")[2:8]
    _mk_task(skill, "ag_mk_cny", _uid("mk"), "ACCEPTED", "CNY", 300)
    _mk_task(skill, "ag_mk_cny", _uid("mk"), "ACCEPTED", "CNY", 500)
    _mk_task(skill, "ag_mk_usdc", _uid("mk"), "ACCEPTED", "USDC", 5_000_000)

    entry = _board_skill(_client(), skill)
    assert entry is not None, "成交过的能力必须上板"
    assert entry["done"] == 3, "两种终态都算完成"
    quotes = {q["currency"]: q for q in entry["quotes"]}
    assert set(quotes) == {"CNY", "USDC"}
    assert quotes["CNY"]["done_count"] == 2
    assert quotes["CNY"]["avg_minor"] == 400          # (300+500)/2，单位仍是分
    assert quotes["USDC"]["done_count"] == 1
    assert quotes["USDC"]["avg_minor"] == 5_000_000   # 没有除以 100、也没和 CNY 混算


def test_market_board_keeps_listed_and_done_apart():
    """只挂牌、还没成交的能力也要上板，且挂牌价不许冒充成交价。"""
    skill = "mk-listed-" + new_id("")[2:8]
    registry.register(_uid("mkl"), _card("mk-listed", skill, price=7))
    entry = _board_skill(_client(), skill)
    assert entry is not None, "只有供给、没有成交，也是行情"
    assert entry["done"] == 0 and entry["requested"] == 0
    q = entry["quotes"][0]
    assert q["currency"] == "CNY"
    assert q["listed_min_minor"] == 7 and q["listed_max_minor"] == 7
    assert q["listed_count"] == 1
    assert q["done_count"] == 0 and q["avg_minor"] == 0


def test_market_board_free_is_not_zero_price():
    """免费 ≠ 标价 0 元：没挂 call_count 的卡不产生挂牌区间；但有供给就得上板。"""
    skill = "mk-free-" + new_id("")[2:8]
    registry.register(_uid("mkf"), _card("mk-free", skill, free=True))
    entry = _board_skill(_client(), skill)
    assert entry is not None, "免费供给也必须有行情 —— 它是零成本可调的那一类"
    assert entry["supply"] >= 1
    assert entry["quotes"] == [], "没标价就不该被编出一个 0 元的挂牌价"


def test_market_board_success_rate_counts_every_request():
    """分母是"被点名多少次"（含未派单/被打回），不是只对成功成立的自洽数字。"""
    skill = "mk-rate-" + new_id("")[2:8]
    _mk_task(skill, "ag_rate", _uid("mkr"), "ACCEPTED", "CNY", 10)
    _mk_task(skill, "ag_rate", _uid("mkr"), "REJECTED", "CNY", 0)
    _mk_task(skill, None, _uid("mkr"), "CREATED", "CNY", 0)     # 还没派单
    entry = _board_skill(_client(), skill)
    assert entry["requested"] == 3
    assert entry["done"] == 1
    assert entry["success_rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_market_board_leaks_no_identity():
    """公开行情里一个主体标识都没有 —— 否则它就是匿名收口的旁路。"""
    skill = "mk-anon-" + new_id("")[2:8]
    requester = _uid("mkq")
    _mk_task(skill, "ag_anon_x", requester, "ACCEPTED", "CNY", 100)
    dumped = json.dumps(_client().get("/v1/market/board").json(), ensure_ascii=False)
    assert requester not in dumped, "行情不该带出请求方身份"
    assert "ag_anon_x" not in dumped, "行情不该带出节点 id"
    assert "principal" not in dumped


def test_market_skill_detail_projects_suppliers():
    """供给节点清单走发现页同一套投影：行情不是看供应商的旁路。"""
    skill = "mk-det-" + new_id("")[2:8]
    aid = registry.register(_uid("mkd"), _card("mk-det", skill, price=9))["agent_id"]
    d = _client().get(f"/v1/market/skill/{skill}", headers={"X-Principal": "acct:someone"}).json()
    assert d["skill_id"] == skill
    assert any(s["agent_id"] == aid for s in d["suppliers"])
    for s in d["suppliers"]:
        assert "principal_id" not in s
        assert "peer_ip" not in s
    assert d["market"]["quotes"][0]["listed_min_minor"] == 9


# ---------------- 他人调用记录（供给方视角） ----------------

def _provider_call(skill: str, provider: str, caller: str, *,
                   pays: bool = False) -> str:
    """让 caller 真调一次 provider 的 agent，返回 task_id。"""
    c = _client()
    if pays:
        paymethods.register(caller, "alipay", "138****0002")
        aid = registry.register(provider, _card("pc", skill, price=3,
                                                accepts=["direct_pay:alipay"]))["agent_id"]
    else:
        aid = registry.register(provider, _card("pc", skill, free=True))["agent_id"]
    r = c.post("/v1/invoke", json={"agent_id": aid, "skill": skill},
               headers={"X-Principal": caller})
    assert r.status_code == 200, r.text
    mgd = c.get("/v1/console/managed", headers={"X-Principal": caller}).json()
    return next(t["id"] for t in mgd["tasks"] if t["skill_id"] == skill)


def test_provided_calls_only_lists_my_agents(stub_forward):
    """供给方只看见自己的单；使用方在自己的清单里看不见这一笔（那是"他人的调用"）。"""
    provider, caller = _uid("pcr"), _uid("pce")
    skill = "pc-" + new_id("")[2:8]
    _provider_call(skill, provider, caller)

    c = _client()
    mine = c.get("/v1/console/provided-calls", headers={"X-Principal": provider}).json()
    row = next(r for r in mine if r["skill_id"] == skill)
    assert row["requester_id"] == caller
    assert row["agent_name"] == "pc"          # 卡片名，人不该靠 agent_id 认单

    as_caller = c.get("/v1/console/provided-calls", headers={"X-Principal": caller}).json()
    assert not any(r["skill_id"] == skill for r in as_caller), \
        "使用方名下没有 agent，他人的调用不该出现在他的供给记录里"


def test_provided_calls_agent_filter_is_not_escalation(stub_forward):
    """agent_id 只是过滤条件，不是越权入口：传别人的 agent 只会得到空。"""
    provider, other = _uid("pcf"), _uid("pco")
    skill = "pcf-" + new_id("")[2:8]
    _provider_call(skill, provider, _uid("pcf-user"))
    other_aid = registry.register(other, _card("other", "pcf-other-" + new_id("")[2:8]))["agent_id"]

    c = _client()
    r = c.get("/v1/console/provided-calls", params={"agent_id": other_aid},
              headers={"X-Principal": provider})
    assert r.status_code == 200
    assert r.json() == []


# ---------------- 调用明细（全流程抽屉的数据源） ----------------

def test_call_detail_serves_both_sides_and_refuses_third_party(stub_forward):
    provider, caller, stranger = _uid("cdp"), _uid("cdc"), _uid("cdx")
    skill = "cd-" + new_id("")[2:8]
    tid = _provider_call(skill, provider, caller)

    c = _client()
    as_req = c.get(f"/v1/console/calls/{tid}", headers={"X-Principal": caller}).json()
    assert as_req["role"] == "requester"
    assert as_req["task"]["id"] == tid

    as_prov = c.get(f"/v1/console/calls/{tid}", headers={"X-Principal": provider}).json()
    assert as_prov["role"] == "provider"
    assert as_prov["requester_id"] == caller            # 供给方要知道谁点的单
    assert as_prov["self_call"] is False, "别人点的单不是同源调用"
    assert "principal_id" not in (as_prov["agent"] or {})

    r = c.get(f"/v1/console/calls/{tid}", headers={"X-Principal": stranger})
    assert r.status_code == 403, "第三方看别人的单应当 403"
    assert c.get("/v1/console/calls/t_does_not_exist",
                 headers={"X-Principal": stranger}).status_code == 404


def test_call_detail_flags_self_calls(stub_forward):
    """自己调自己（同源）：一个身份**既买又卖**，于是"我既是发起方、又是 agent 的主人"
    是一种真会出现的形状（2026-09-20 起一个节点一个身份，控制台开箱就是它）。

    role 判据有先后（先判 requester），只会说出其中一半 —— 供给视角的抽屉会拿
    "requester" 把它渲染成「我是使用方」，等于把同源调用**冒充成"他人的调用"**。
    所以服务端必须单独回一个布尔值，让前端能说清"这次是自己调自己"。
    （口径与证据页一致：同源照记、照吃试用额度，但不进公开统计。）
    """
    provider = _uid("scs")
    skill = "sc-" + new_id("")[2:8]
    tid = _provider_call(skill, provider, provider)     # 同一个人调自己的 agent

    c = _client()
    d = c.get(f"/v1/console/calls/{tid}", headers={"X-Principal": provider}).json()
    assert d["self_call"] is True, "自己调自己必须被标出来，否则抽屉会当成别人的单"
    assert d["requester_id"] == provider
    assert d["role"] == "requester"                   # 旧判据不变，只是不再"只是"它
    assert "principal_id" not in (d["agent"] or {}), "身份不外发这条不变"


def test_call_detail_timeline_comes_from_receipt_chain(stub_forward):
    """明细里的流程就是链上那几枚章，按序取，不是另糊一份时间线。"""
    provider, caller = _uid("tjp"), _uid("tjc")
    skill = "tj-" + new_id("")[2:8]
    tid = _provider_call(skill, provider, caller)

    d = _client().get(f"/v1/console/calls/{tid}", headers={"X-Principal": caller}).json()
    types = [r["event_type"] for r in d["timeline"]]
    for expect in ("task.created", "task.assigned", "task.submitted", "acceptance.passed"):
        assert expect in types, f"全流程应当有 {expect}，实际 {types}"
    seqs = [r["seq"] for r in d["timeline"]]
    assert seqs == sorted(seqs), "时间线按发生顺序"
    # 披露过滤：链不动，呈现层剥身份
    assert all("principal_id" not in (r["payload"] or "") for r in d["timeline"])
    # 计量随任务一起来，自报与观测分列（认不出的维度不假装知道）
    assert d["usage"] is not None
    assert "dims" in d["usage"] and "observed" in d["usage"]


def test_notary_for_task_is_exact_not_fuzzy():
    """按 task_id 精确取章：模糊匹配会让"这枚章属于谁的单"变成猜。"""
    assert notary.for_task("t_never_existed_" + new_id("")[2:8]) == []


def test_charge_detail_points_at_its_task(stub_forward):
    """流水行 → 凭证 → 任务，三级一次点击到位（用户不该自己拼 id）。"""
    provider, caller = _uid("chp"), _uid("chc")
    skill = "ch-" + new_id("")[2:8]
    tid = _provider_call(skill, provider, caller, pays=True)

    c = _client()
    led = c.get("/v1/ledger", headers={"X-Principal": caller}).json()["items"]
    out = next(i for i in led if i["note"] == skill and i["direction"] == "out")
    d = c.get(f"/v1/console/charges/{out['ref_id']}", headers={"X-Principal": caller}).json()
    assert d["task_id"] == tid
    assert d["charge"]["state"] in {"AUTHORIZED", "CAPTURED"}
    assert "mandate_chain" not in d["charge"], "授权链原文不随明细一起发"
    # 供给方 owner 也看得到（要核对自己的货款）
    assert c.get(f"/v1/console/charges/{out['ref_id']}",
                 headers={"X-Principal": provider}).status_code == 200
    assert c.get(f"/v1/console/charges/{out['ref_id']}",
                 headers={"X-Principal": _uid("chy")}).status_code == 403


def test_console_detail_views_fail_closed_without_principal(stub_forward):
    """缺身份不是"给你空列表"：直接调处理函数，越权面要么 403 要么空集。"""
    from a2n_server.routers import public as pub

    provider, caller = _uid("fcp"), _uid("fcc")
    skill = "fc-" + new_id("")[2:8]
    tid = _provider_call(skill, provider, caller)

    with pytest.raises(HTTPException) as e1:
        pub.call_detail(tid, principal=None)
    assert e1.value.status_code == 403, "明细只认行为相关方，不认'没身份'"
    with pytest.raises(HTTPException) as e2:
        pub.charge_detail("c_whatever", principal=None)
    assert e2.value.status_code in (403, 404)
    # 供给记录：principal 为空时 SQL 匹配不到任何行 → 空集（fail-closed）
    assert pub.provided_calls(agent_id=None, limit=50, principal=None) == []


# ---------------- 待使用（收藏回表） ----------------

def test_managed_roster_reads_back_with_identity_projected():
    """收藏（待使用名单）非空时，/v1/console/managed 必须能正常回表。

    回归用例：这一条以前是 500 —— 路由层拿 `conn().execute(...)` 的裸
    sqlite3.Row 去做业务面投影，而投影函数对非 dict 是**静默原样返回**的，
    于是错到下一行 `d.get(k)` 才炸。空收藏时回表循环根本不执行，所以一直是绿的；
    前端又把 500 翻成"你还没有发起过调用"，一个后端类型错伪装成了空态。
    """
    owner, viewer = _uid("rso"), _uid("rsv")
    skill = "rs-" + new_id("")[2:8]
    aid = registry.register(owner, _card("rs-node", skill, price=11))["agent_id"]

    c = _client()
    assert c.post(f"/v1/roster/{aid}",
                  headers={"X-Principal": viewer}).status_code == 200
    r = c.get("/v1/console/managed", headers={"X-Principal": viewer})
    assert r.status_code == 200, f"收藏非空时回表不该炸：{r.text}"

    row = next(x for x in r.json()["roster"] if x["agent_id"] == aid)
    assert row["name"] == "rs-node"
    assert "principal_id" not in row, "别人收藏的卡不带供应方主体"
    assert "peer_ip" not in row
    assert row.get("card_json"), "卡原文要在（上架回填与价目派生都要用）"
    assert row["price_book"], "价目随卡一起给出来，前端不自己从卡里翻"
    # 行解码仍归 a2n-registry：v1 遗留列不该被路由层顺手带出来
    assert "price_hint" not in row


def test_projection_refuses_raw_db_rows():
    """裸 sqlite3.Row 喂进投影必须当场报错，不许静默放行。

    "静默原样返回"看着像宽容，其实是把类型错伪装成"已投影的卡"继续往下传，
    只在恰好走到那条路径时才炸（见上一条回归）。取数口错、类型错，宁可当场说。
    """
    from a2n_server.routers.registry import _project_agent

    owner = _uid("raw")
    aid = registry.register(owner, _card("raw-node", "raw-" + new_id("")[2:8]))["agent_id"]
    raw = conn().execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
    assert raw is not None

    with pytest.raises(TypeError) as e:
        _project_agent(raw, "acct:someone")
    assert "Row" in str(e.value), "报错要说清收到了什么类型，别让人去猜"
    # 走对取数口给的是 dict，照常放行
    assert isinstance(_project_agent(registry.get(aid), "acct:someone"), dict)
