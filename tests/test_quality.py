"""质量证据护栏：计量真签名、模板偏差、归一化评分、试用与毕业。

这一组测试守的是产品最核心的一句承诺：**我们不给结论，我们给证据**。
所以每条机制都同时测"正向生效"和"最容易被绕过去的那条缝"：

  · 归一化：必须复现需求里给的三个数字（80→50 / 90→75 / 40→25），边界不许爆炸；
  · 模板偏差：必须**可复算**（同素材同结果），无模板时**不产生指标**（不伪造 0 偏差）；
  · 计量签名：真签名落库、假签名进争议、**再也不能是写死的 mock 值**；
  · 评分：绑定具体交付、一条交付只能评一次、自源不计入；
  · 试用与毕业：额度**不按人计**（自己调自己也吃额度），
    但**自源不进公开案例、不进评分统计** —— 这条反例立住，取舍才算立住。
"""
from __future__ import annotations

import json

import pytest

from a2n_acceptance import deviation, parse_template
from a2n_gateway.gate import resolve
from a2n_kernel.errors import ConflictError, NotFoundError, ValidationError
from a2n_kernel.hashing import new_id
from a2n_p2p import Identity, pub_b64, sign_metering, verify_metering
from a2n_registry import registry, trial
from a2n_reputation import counts, graduate_blockers, normalize_score, rate, summary
from a2n_server.routers.quality import case_list, evidence, objective_facts, quality_facts
from a2n_store import conn
from a2n_task import tasks


def _uid(p: str) -> str:
    return f"{p}-{new_id('')[:8]}"


def _template() -> dict:
    return {
        "version": "7",
        "required_fields": ["text", "pages"],
        "required_content": [
            {"key": "已处理", "path": "text", "mode": "contains", "value": "HELLO"},
        ],
        "tolerance": 0.2,
    }


def _card(name: str, skill: str, *, price: int = 5, template: bool = True,
          trial_flag: bool | None = None, ident: Identity | None = None) -> dict:
    ext: dict = {
        "deployment": {"region": "cn-east-2"},
        "sla": {"max_latency_ms": 5000},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
        "price_book": {skill: {"CNY": {"dimensions": [
            {"key": "call_count", "amount": price, "per": 1}]}}},
    }
    if template:
        ext["acceptance_template"] = _template()
    if trial_flag is not None:
        ext["trial"] = trial_flag
    if ident is not None:
        ext["sovereign"] = {"did": ident.did, "pub": pub_b64(ident.pub_raw)}
    return {"name": name, "version": "1.0.0", "url": "http://localhost/a2a",
            "skills": [{"id": skill, "name": skill, "tags": []}],
            "accepts": ["peer_account"], "x-a2n": ext}


def _register(name: str, skill: str, **kw) -> tuple[dict, dict]:
    """注册一个可供派单的 agent，返回 (agent, card)。"""
    card = _card(name, skill, **kw)
    agent = registry.register(_uid("owner"), card)
    registry.heartbeat(agent["agent_id"])       # 心跳 = "我在线"
    return agent, card


def _call(requester: str, agent_id: str, skill: str, result: dict | None = None,
          *, attest=None, settle: bool = False) -> tuple[dict, dict]:
    """走一次"建任务 → 提交"（inline 交付，与网关主链路同一个交付方式）。"""
    t = tasks.create(requester, skill, {"text": "hello"}, budget=100,
                     preferred_agents=[agent_id], delivery="inline", hold_budget=False)
    usage: dict = {"call_count": 1, "wall_time_ms": 1}
    if attest is not None:
        usage["attest"] = attest(t["id"], agent_id)
    res = tasks.submit(t["id"], agent_id, result if result is not None else {"text": "HELLO", "pages": 1},
                       usage, settle=settle)
    return t, res


def _usage_row(task_id: str) -> dict:
    return dict(conn().execute(
        "SELECT * FROM usage_reports WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (task_id,)).fetchone())


# ---------------------------------------------------------------- 归一化评分

def test_normalized_score_reproduces_the_spec_numbers():
    """需求里给的三个数字必须分毫不差：μ=80 时 80→50 / 90→75 / 40→25。"""
    for raw, want in ((80, 50.0), (90, 75.0), (40, 25.0)):
        got, mu = normalize_score(raw, 80.0, 10 ** 6)
        assert got == pytest.approx(want, abs=0.02), f"{raw} 在 μ=80 下应得 {want}"
        assert mu == pytest.approx(80.0, abs=0.02)


def test_normalized_score_keeps_bounds_and_shrinks_early_samples():
    """μ=0 不许爆炸、μ=100 不许除零；样本少时 μ 往 50 拉（不是恒 50）。"""
    for raw in (0, 50, 100):
        for mu_raw in (0.0, 100.0):
            v, mu = normalize_score(raw, mu_raw, 5)
            assert 0.0 <= v <= 100.0 and 1.0 <= mu <= 99.0
    first, mu_used = normalize_score(90, 50.0, 0)
    assert first == pytest.approx(90.0) and mu_used == pytest.approx(50.0)


def test_normalized_score_is_recomputable_from_stored_anchors():
    """归一化分必须能从随评分存档的 (raw, μ_用) 复算出来 —— 否则它是黑盒。"""
    skill = f"q-norm-{new_id('')[:6]}"
    agent, _ = _register("norm-prov", skill, trial_flag=False)
    user = _uid("norm-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"], res
    got = rate(t["id"], user, 70)
    mu, raw = got["mu_used"], got["raw_score"]
    expect = raw * 50 / mu if raw <= mu else 50 + (raw - mu) * 50 / (100 - mu)
    assert got["normalized"] == pytest.approx(expect, abs=0.01)
    row = dict(conn().execute("SELECT * FROM ratings WHERE task_id=?", (t["id"],)).fetchone())
    assert (row["mu_used"], row["rater_n"]) == (got["mu_used"], got["rater_n"])


# ---------------------------------------------------------------- 模板偏差（硬指标）

def test_template_deviation_is_recomputable_and_marks_no_reference():
    tpl = parse_template({"x-a2n": {"acceptance_template": _template()}})
    assert tpl and tpl["version"] == "7"
    a = deviation(tpl, {"text": "hello HELLO", "pages": 1})
    b = deviation(tpl, {"text": "hello HELLO", "pages": 1})
    assert a == b, "同一份素材必须算出同一个偏差 —— 否则它不叫硬指标"
    assert a["D"] == 0.0 and a["quality"] == 100.0
    assert a["no_reference"] is True, "无参考必须标出来，不能假装算过内容一致性"


def test_template_deviation_counts_missing_and_extra():
    tpl = parse_template({"x-a2n": {"acceptance_template": _template()}})
    missing = deviation(tpl, {"text": "hello HELLO"})          # 缺 pages
    assert missing["d_struct"] > 0 and any("pages" in r for r in missing["reasons"])
    extra = deviation(tpl, {"text": "hello HELLO", "pages": 1, "junk": 1})
    assert extra["d_struct"] > 0, "多出模板未声明的字段同样是偏差"
    uncovered = deviation(tpl, {"text": "lowercase", "pages": 1})
    assert uncovered["d_completeness"] > 0 and uncovered["quality"] < 100.0


def test_no_template_means_no_quality_metric():
    """绝不允许伪造一个 0 偏差：没声明模板就不产生质量指标。"""
    assert parse_template({"x-a2n": {}}) is None
    assert parse_template({"x-a2n": {"acceptance_template": {}}}) is None


def test_acceptance_records_quality_into_the_report():
    skill = f"q-tpl-{new_id('')[:6]}"
    agent, _ = _register("tpl-prov", skill, trial_flag=False)
    user = _uid("tpl-user")
    t, res = _call(user, agent["agent_id"], skill, result={"text": "hello HELLO"})
    assert res["passed"], res
    u = _usage_row(t["id"])
    assert u["quality"] is not None and u["quality"] < 100.0
    assert json.loads(u["template_ref"])["version"] == "7"
    dev = json.loads(u["deviation"])
    assert dev["d_struct"] > 0 and dev["no_reference"] is True


# ---------------------------------------------------------------- 计量真签名

def test_metering_signature_is_real_and_verified():
    """签名必须是真的：验签通过 → attested=1，且落库的签名就是节点签出来的那一串。"""
    skill = f"q-sig-{new_id('')[:6]}"
    ident = Identity.generate()
    agent, card = _register("sig-prov", skill, trial_flag=False, ident=ident)
    aid = agent["agent_id"]
    t, res = _call(_uid("sig-user"), aid, skill,
                   attest=lambda tid, a: sign_metering(
                       ident, task_id=tid, node_id=a,
                       dims={"call_count": 1, "wall_time_ms": 1}))
    assert res["passed"], res
    u = _usage_row(t["id"])
    assert u["attested"] == 1 and u["attest_reason"] == "验签通过"
    assert u["status"] == "reconciled"
    assert u["signature"] and u["signature"] != "mock-signature", "写死的假签名必须消失"


def test_tampered_metering_goes_to_dispute_not_reconciled():
    """验签不通过 → 进争议，不许叫"已对账"。"""
    skill = f"q-sigbad-{new_id('')[:6]}"
    ident = Identity.generate()
    agent, _ = _register("sigbad-prov", skill, trial_flag=False, ident=ident)
    aid = agent["agent_id"]

    def forged(tid: str, a: str) -> dict:
        att = sign_metering(ident, task_id=tid, node_id=a,
                            dims={"call_count": 1, "wall_time_ms": 1})
        att["payload"]["dims"] = {"call_count": 999}     # 签完之后改内容
        return att

    t, res = _call(_uid("sigbad-user"), aid, skill, attest=forged)
    assert res["passed"], "计量真伪不该让验收失效（它管的是核对记录）"
    u = _usage_row(t["id"])
    assert u["attested"] == 0 and u["status"] == "disputed"
    assert "签名" in u["attest_reason"] or "不一致" in u["attest_reason"]


def test_metering_signature_must_match_the_declared_card_key():
    """卡上声明了钥匙，就必须用同一把 —— 否则等于拿另一个身份给我背书。"""
    skill = f"q-sigkey-{new_id('')[:6]}"
    ident, other = Identity.generate(), Identity.generate()
    agent, _ = _register("sigkey-prov", skill, trial_flag=False, ident=ident)
    aid = agent["agent_id"]
    t, res = _call(_uid("sigkey-user"), aid, skill,
                   attest=lambda tid, a: sign_metering(
                       other, task_id=tid, node_id=a,
                       dims={"call_count": 1, "wall_time_ms": 1}))
    assert res["passed"]
    u = _usage_row(t["id"])
    assert u["attested"] == 0 and "同一把" in u["attest_reason"]


def test_unsigned_metering_is_honest_not_fake():
    """没签名就如实说没签名 —— 绝不退回写死的 mock 值假装验过。"""
    skill = f"q-nosig-{new_id('')[:6]}"
    agent, _ = _register("nosig-prov", skill, trial_flag=False)
    t, res = _call(_uid("nosig-user"), agent["agent_id"], skill)
    assert res["passed"]
    u = _usage_row(t["id"])
    assert u["attested"] == 0 and u["signature"] == ""
    assert "未签名" in u["attest_reason"]
    assert u["status"] == "reconciled"        # 对账照旧，但"是不是证据"写清楚了


def test_verify_metering_binds_to_this_task_and_node():
    ident = Identity.generate()
    att = sign_metering(ident, task_id="t_a", node_id=ident.node_id,
                        dims={"call_count": 1})
    assert verify_metering(att, expect_task_id="t_a", expect_node_id=ident.node_id,
                           expect_dims={"call_count": 1})[0] is True
    ok, why = verify_metering(att, expect_task_id="t_b")
    assert ok is False and "另一单" in why
    ok, why = verify_metering(att, expect_node_id="ag_someone_else")
    assert ok is False and "另一个节点" in why


# ---------------------------------------------------------------- 评分

def test_rating_is_bound_to_a_delivery_and_idempotent():
    skill = f"q-rate-{new_id('')[:6]}"
    agent, _ = _register("rate-prov", skill, trial_flag=False)
    user = _uid("rate-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"]
    first = rate(t["id"], user, 88)
    assert first["task_id"] == t["id"]
    with pytest.raises(ConflictError):
        rate(t["id"], user, 60)                      # 一条交付只能评一次
    with pytest.raises(ConflictError):
        rate(t["id"], _uid("someone-else"), 60)      # 不是发起人不能评
    with pytest.raises(ValidationError):
        rate(t["id"], user, 101)


def test_rating_requires_a_completed_delivery():
    skill = f"q-rate2-{new_id('')[:6]}"
    agent, _ = _register("rate2-prov", skill, trial_flag=False)
    user = _uid("rate2-user")
    t = tasks.create(user, skill, {"text": "x"}, budget=100,
                     preferred_agents=[agent["agent_id"]], delivery="inline",
                     hold_budget=False)
    with pytest.raises(ConflictError):
        rate(t["id"], user, 90)                      # 还没完成
    with pytest.raises(NotFoundError):
        rate("t_does_not_exist", user, 90)


def test_low_sample_rater_does_not_publish_scores():
    """样本不够时给原因，不给空白：分数为 None，且说清还差几位评分者。"""
    skill = f"q-low-{new_id('')[:6]}"
    agent, _ = _register("low-prov", skill, trial_flag=False)
    user = _uid("low-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"]
    got = rate(t["id"], user, 95)
    assert got["credited"] is False, "评分者没过最小样本门 → 只进本地校准池"
    s = summary(agent["agent_id"])
    assert s["published"] is False and s["score"] is None and s["needed_raters"] > 0
    assert s["samples"] == 0, "未发布的分不该进对外样本数"


# ---------------------------------------------------------------- 试用与毕业

def test_self_calls_fill_the_quota_but_never_become_public_evidence(monkeypatch):
    """**P0 的反例**：自己调自己 10 次 → 能凑满额度（额度不按人计），
    但这 10 条**不进公开案例、不进评分统计**。

    这条立住，"免费换案例"与"提供者不被白嫖"这两个诉求才算同时成立。
    """
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-self-{new_id('')[:6]}"
    card = _card("self-prov", skill)
    owner = _uid("self-owner")
    agent = registry.register(owner, card)
    registry.heartbeat(agent["agent_id"])
    aid = agent["agent_id"]

    assert trial.in_trial(aid) is True
    assert resolve(aid, owner, card).mode is None, "试用期必须表现为免费"

    task_ids = []
    for _ in range(10):
        t, res = _call(owner, aid, skill)          # 主人自己调自己
        assert res["passed"] and res["amount"] == 0, "试用期不许收钱"
        task_ids.append(t["id"])
        rate(t["id"], owner, 95)                   # 自己给自己打分

    prog = trial.progress(aid)
    assert prog["used"] == 10 and prog["trial"] is False, "额度按次数计，谁调的都算"
    assert _usage_row(task_ids[0])["quality"] is not None

    ev = counts(aid)
    assert ev["total"] == 10 and ev["non_self"] == 0, "自源评分照存，但不是证据"
    s = summary(aid)
    assert s["raters"] == 0 and s["score"] is None
    assert case_list(aid) == [], "自源调用不进公开案例"

    blockers = graduate_blockers(card=card, trial=prog, evidence=ev)
    assert any("非自源" in b for b in blockers), "证据不够就必须卡住毕业"


def test_real_evidence_graduates_then_charging_starts(monkeypatch):
    """补上非自源证据 → 可毕业 → 毕业后才允许收费（收费能力此前一直是个空承诺）。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-grad-{new_id('')[:6]}"
    card = _card("grad-prov", skill)
    owner = _uid("grad-owner")
    agent = registry.register(owner, card)
    registry.heartbeat(agent["agent_id"])
    aid = agent["agent_id"]

    for _ in range(10):                            # 用尽试用额度
        _call(owner, aid, skill)
    assert trial.progress(aid)["used"] == 10

    for i in range(3):                             # 三位不同使用者留下非自源案例
        u = _uid(f"grad-user{i}")
        t, res = _call(u, aid, skill)
        assert res["passed"]
        rate(t["id"], u, 80 + i)
    assert counts(aid)["non_self"] == 3

    prog = trial.progress(aid)
    blockers = graduate_blockers(card=card, trial=prog, evidence=counts(aid),
                                open_disputes=0, unfinished=0)
    assert blockers == [], f"四条判据都满足了还能被卡住？{blockers}"
    trial.graduate(aid)
    assert trial.progress(aid)["state"] == "GRADUATED"
    with pytest.raises(PermissionError):
        resolve(aid, _uid("grad-stranger"), card)   # 毕业之后才真要钱了


def test_graduate_blockers_spell_out_what_is_missing(monkeypatch):
    """毕业按钮点不动时，必须写清"还差什么" —— 而不是一个沉默的灰按钮。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-gap-{new_id('')[:6]}"
    card = _card("gap-prov", skill, template=False)     # 故意不声明验收模板
    agent = registry.register(_uid("gap-owner"), card)
    aid = agent["agent_id"]
    blockers = graduate_blockers(card=card, trial=trial.progress(aid), evidence=counts(aid))
    assert any("验收模板" in b for b in blockers)
    assert any("免费额度" in b for b in blockers)
    assert any("非自源" in b for b in blockers)


def test_trial_regrant_is_bounded(monkeypatch):
    """重连补额必须有界：不补无用之额、每自然日 1 次、生命周期有总上限。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-grant-{new_id('')[:6]}"
    agent = registry.register(_uid("grant-owner"), _card("grant-prov", skill))
    aid = agent["agent_id"]

    _state, msg = trial.grant(aid)
    assert "不需要补" in msg, "还有额度时不该补"

    for _ in range(10 + 5 + 5):
        trial.consume(aid)
    for _ in range(2):                       # 第一次补 5 → 用尽 → 同日再补被拒
        conn().execute("UPDATE trial_offers SET used=cap WHERE agent_id=?", (aid,))
        conn().commit()
        st, msg = trial.grant(aid)
    assert "每自然日" in msg, f"同一自然日不许重复补额：{msg}"

    conn().execute("UPDATE trial_offers SET granted_total=30, used=cap, last_grant_at=NULL"
                   " WHERE agent_id=?", (aid,))
    conn().commit()
    _st, msg2 = trial.grant(aid)
    assert "上限" in msg2


def test_trial_opt_out_card_never_gets_a_trial(monkeypatch):
    """卡上显式退出试用的（演示节点/成熟节点）永远不走试用 —— 老账不重算。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-opt-{new_id('')[:6]}"
    agent, card = _register("opt-prov", skill, trial_flag=False)
    assert trial.in_trial(agent["agent_id"]) is False
    with pytest.raises(PermissionError):
        resolve(agent["agent_id"], _uid("opt-user"), card)   # 退出试用 = 一上来就收费


# ---------------------------------------------------------------- 证据三段

def test_evidence_is_three_segments_never_one_score():
    """三段证据分开给出、各有口径；**没有任何"综合分"字段** —— 我们不给结论。"""
    skill = f"q-evi-{new_id('')[:6]}"
    agent, _ = _register("evi-prov", skill, trial_flag=False)
    user = _uid("evi-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"]
    rate(t["id"], user, 90)

    ev = evidence(agent["agent_id"], limit=5)
    assert set(ev) == {"agent_id", "objective", "quality", "ratings", "trial", "cases"}
    assert ev["objective"]["rtt_source"] == "平台探测"
    assert ev["objective"]["observed_source"] == "使用端实测"
    assert ev["objective"]["metering_source"] == "节点签名 + 平台观测对账"
    assert ev["quality"]["declared"] is True
    assert ev["quality"]["components"] is not None
    assert ev["ratings"]["published"] is False and ev["ratings"]["score"] is None
    assert "score" not in ev and "total" not in ev, "证据面不许出现一个合成的总分"
    assert len(ev["cases"]) == 1
    case = ev["cases"][0]
    assert "requester_id" not in case, "别人调的单不该把别人名字挂出来"
    assert case["quality"] is not None


def test_no_quality_metric_for_undeclared_template():
    """没声明模板 → 明确写"未声明验收模板"，而不是一个漂亮的 100 分。"""
    skill = f"q-notpl-{new_id('')[:6]}"
    agent, _ = _register("notpl-prov", skill, template=False, trial_flag=False)
    _call(_uid("notpl-user"), agent["agent_id"], skill)
    q = quality_facts(agent["agent_id"])
    assert q["declared"] is False and q["quality"] is None
    assert "未声明验收模板" in q["note"]
    objective = objective_facts(agent["agent_id"])
    assert objective["done"] >= 1, "客观表现照旧要给 —— 它不依赖模板"


def test_declared_template_without_samples_is_not_reported_as_undeclared():
    """「声明了模板但还没人调过」与「没声明模板」必须分开说。

    混成一句的代价：owner 明明声明过模板，界面却告诉他"未声明验收模板"，
    他会去补一个早就补过的东西（而真正的缺口是"还没有交付样本"）。
    """
    skill = f"q-tpl-nosample-{new_id('')[:6]}"
    agent, _ = _register("tpl-nosample-prov", skill, template=True, trial_flag=False)
    q = quality_facts(agent["agent_id"])
    assert q["declared"] is True, "卡上有模板"
    assert q["measured"] is False, "但一次都没算过"
    assert q["quality"] is None, "没样本就不许给出一个数"
    assert "已声明验收模板" in q["note"] and "未声明" not in q["note"]
    assert q["template_version"] == "7", "声明的是哪个版本，也要能核对"
    # 列表页摘要同样带上 measured：徽标只在"真的算过"时才挂"偏差 x"
    assert quality_facts(agent["agent_id"])["measured"] is False
