"""质量证据护栏：计量真签名、模板偏差、归一化评分、试用与毕业。

这一组测试守的是产品最核心的一句承诺：**我们不给结论，我们给证据**。
所以每条机制都同时测"正向生效"和"最容易被绕过去的那条缝"：

  · 归一化：必须复现需求里给的三个数字（80→50 / 90→75 / 40→25），边界不许爆炸；
  · 模板偏差：必须**可复算**（同素材同结果），无模板时**不产生指标**（不伪造 0 偏差）；
  · 计量签名：真签名落库、假签名进争议、**再也不能是写死的 mock 值**；
    且"签了但归属不明"（卡上没声明公钥）同样进争议 —— 签名不等于归属；
  · 评分：绑定具体交付、一条交付只能评一次、自源不计入；
  · 试用与毕业：额度**不按人计**（自己调自己也吃额度），
    但**自源不进公开案例、不进评分统计** —— 这条反例立住，取舍才算立住。
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from a2n_acceptance import deviation, parse_template
from a2n_gateway.gate import resolve
from a2n_kernel.errors import ConflictError, NotFoundError, ValidationError
from a2n_kernel.hashing import new_id
from a2n_p2p import Identity, pub_b64, sign_metering, verify_metering
from a2n_p2p.attest import card_body
from a2n_registry import registry, trial
from a2n_reputation import (counts, graduate_blockers, normalize_score, rate,
                            self_vs_independent, summary)
from a2n_server.routers.quality import (case_counts, case_list, evidence,
                                        objective_facts, quality_facts)
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
          trial_decl: bool | None = None, ident: Identity | None = None) -> dict:
    ext: dict = {
        "deployment": {"region": "cn-east-2"},
        "sla": {"max_latency_ms": 5000},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
        "price_book": {skill: {"CNY": {"dimensions": [
            {"key": "call_count", "amount": price, "per": 1}]}}},
    }
    if template:
        ext["acceptance_template"] = _template()
    if trial_decl is not None:
        # 只在测「卡上不许声明退出免费期」时用。正常注册的卡**不写**这个字段 ——
        # 它没有任何开关作用（想被发现的 agent 一律先免费服务 10 次）。
        ext["trial"] = trial_decl
    if ident is not None:
        # 声明了身份就必须**自己签**（签名域=整卡去 sig），并自带 uid ——
        # uid 属于签名域，平台补写会让签名失效，注册路的自证闸会拒收。
        ext["uid"] = str(uuid.uuid4())
        ext["sovereign"] = {"did": ident.did, "pub": pub_b64(ident.pub_raw)}
    card = {"name": name, "version": "1.0.0", "url": "http://localhost/a2a",
            "skills": [{"id": skill, "name": skill, "tags": []}],
            "accepts": ["peer_account"], "x-a2n": ext}
    if ident is not None:
        ext["sovereign"]["sig"] = ident.sign(card_body(card))
    return card


def _register(name: str, skill: str, *, established: bool = False,
              **kw) -> tuple[dict, dict]:
    """注册一个可供派单的 agent，返回 (agent, card)。

    `established=True` = 模拟"已经开业"的节点（免费期走完、已毕业、按价目表收费）。
    **不能靠卡上声明退出试用** —— 卡上写 `x-a2n.trial=false` 会被 `validate_card` 直接拒
    （任何想被发现的 agent，前 10 次完成调用免费）。

    所以这里走**部署级开关** `A2N_TRIAL_DEFAULT=0`（注册那一刻读一次，见
    `trial.policy_for`），并且只在本次注册期间生效 —— 它是部署的口径，
    不是供给方的出口，测试里也必须这么用，否则测的就不是真实的注册路径。
    """
    card = _card(name, skill, **kw)
    if not established:
        return _registered(card)
    prev = os.environ.get("A2N_TRIAL_DEFAULT")
    os.environ["A2N_TRIAL_DEFAULT"] = "0"
    try:
        return _registered(card)
    finally:
        if prev is None:
            os.environ.pop("A2N_TRIAL_DEFAULT", None)
        else:
            os.environ["A2N_TRIAL_DEFAULT"] = prev


def _registered(card: dict) -> tuple[dict, dict]:
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
    agent, _ = _register("norm-prov", skill, established=True)
    user = _uid("norm-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"], res
    got = rate(t["id"], user, 70)
    mu, raw = got["mu_used"], got["raw_score"]
    expect = raw * 50 / mu if raw <= mu else 50 + (raw - mu) * 50 / (100 - mu)
    assert got["normalized"] == pytest.approx(expect, abs=0.01)
    row = dict(conn().execute("SELECT * FROM ratings WHERE task_id=?", (t["id"],)).fetchone())
    assert (row["mu_used"], row["rater_n"]) == (got["mu_used"], got["rater_n"])


def test_normalized_score_clamps_extreme_slopes_but_never_moves_the_anchor():
    """§8.3 定稿：两端斜率夹在 [0.5, 3] —— 压住"一次打分定生死"，锚点不动。

    手极紧的评分者（μ_用 很低）若按 50/μ 放大，一个低分就能把一家打到地板；
    手极松的（μ_用 很高）同理反向。夹取只在极端区间生效。
    """
    from a2n_reputation import SLOPE_MAX, SLOPE_MIN

    # 手极紧：下段斜率 50/μ_用 会被夹到上界
    low, mu_low = normalize_score(1, 5.0, 500)
    assert mu_low < 50.0 / SLOPE_MAX, f"没构造出夹取区间（μ_用={mu_low}）"
    assert low == pytest.approx(50 + (1 - mu_low) * SLOPE_MAX, abs=0.01), "下段斜率没夹住"

    # 手极松：上段斜率 50/(100−μ_用) 会被夹到上界
    high, mu_high = normalize_score(99, 95.0, 500)
    assert mu_high > 100 - 50.0 / SLOPE_MAX, f"没构造出夹取区间（μ_用={mu_high}）"
    assert high == pytest.approx(50 + (99 - mu_high) * SLOPE_MAX, abs=0.01), "上段斜率没夹住"
    assert low >= 0.0 and high <= 100.0

    # 锚点不许动：μ_用 永远映到 50
    mid, mu_mid = normalize_score(80, 80.0, 10 ** 6)
    assert mid == pytest.approx(50.0, abs=0.02) and mu_mid == pytest.approx(80.0, abs=0.02)

    # 常规区间：与教科书写法等价（老案例才复算得出来）
    for raw, mu_raw, n in ((90, 80.0, 10 ** 6), (40, 80.0, 10 ** 6), (70, 50.0, 3)):
        got, mu = normalize_score(raw, mu_raw, n)
        book = raw * 50 / mu if raw <= mu else 50 + (raw - mu) * 50 / (100 - mu)
        assert got == pytest.approx(book, abs=0.01), f"{raw}@{mu} 与教科书式不一致"
        assert SLOPE_MIN <= 50.0 / (mu if raw <= mu else 100 - mu) <= SLOPE_MAX

    # 夹取只压放大，不许把排序弄反
    assert normalize_score(95, 5.0, 500)[0] > normalize_score(60, 5.0, 500)[0]


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
    agent, _ = _register("tpl-prov", skill, established=True)
    user = _uid("tpl-user")
    t, res = _call(user, agent["agent_id"], skill, result={"text": "hello HELLO"})
    assert not res["passed"] and any("pages" in r for r in res["reasons"]), res
    u = _usage_row(t["id"])
    assert u["quality"] is not None and u["quality"] < 100.0
    assert json.loads(u["template_ref"])["version"] == "7"
    dev = json.loads(u["deviation"])
    assert dev["d_struct"] > 0 and dev["no_reference"] is True


def test_required_template_content_is_a_real_acceptance_gate():
    skill = f"q-hard-{new_id('')[:6]}"
    agent, _ = _register("hard-prov", skill, established=True)
    _, missing = _call(_uid("hard-user"), agent["agent_id"], skill,
                       result={"text": "lowercase", "pages": 1})
    assert missing["passed"] is False
    assert any("必需内容" in r for r in missing["reasons"])
    _, complete = _call(_uid("hard-user-ok"), agent["agent_id"], skill,
                        result={"text": "HELLO", "pages": 1})
    assert complete["passed"] is True


# ---------------------------------------------------------------- 计量真签名

def test_metering_signature_is_real_and_verified():
    """签名必须是真的：验签通过 → attested=1，且落库的签名就是节点签出来的那一串。"""
    skill = f"q-sig-{new_id('')[:6]}"
    ident = Identity.generate()
    agent, card = _register("sig-prov", skill, established=True, ident=ident)
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
    agent, _ = _register("sigbad-prov", skill, established=True, ident=ident)
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
    agent, _ = _register("sigkey-prov", skill, established=True, ident=ident)
    aid = agent["agent_id"]
    t, res = _call(_uid("sigkey-user"), aid, skill,
                   attest=lambda tid, a: sign_metering(
                       other, task_id=tid, node_id=a,
                       dims={"call_count": 1, "wall_time_ms": 1}))
    assert res["passed"]
    u = _usage_row(t["id"])
    assert u["attested"] == 0 and "同一把" in u["attest_reason"]


def test_metering_unattributable_when_card_declares_no_key():
    """签名只证明"某人签了"，不证明"签的人是这个节点"——归属靠卡上的公钥。

    卡没声明钥匙时必须拦下：放行等于任何一把钥匙都能替这个节点背书，
    "已对账"就成了一句空话。宁可如实说"无从归属"，也不装作验过了。
    """
    skill = f"q-nokey-{new_id('')[:6]}"
    ident = Identity.generate()
    agent, _ = _register("nokey-prov", skill, established=True)   # 卡上**不**声明 sovereign
    aid = agent["agent_id"]
    t, res = _call(_uid("nokey-user"), aid, skill,
                   attest=lambda tid, a: sign_metering(
                       ident, task_id=tid, node_id=a,
                       dims={"call_count": 1, "wall_time_ms": 1}))
    assert res["passed"], "归属不明不该让验收失效（它管的是核对记录）"
    u = _usage_row(t["id"])
    assert u["attested"] == 0 and "无从归属" in u["attest_reason"]
    assert u["status"] == "disputed"


def test_unsigned_metering_is_honest_not_fake():
    """没签名就如实说没签名 —— 绝不退回写死的 mock 值假装验过。"""
    skill = f"q-nosig-{new_id('')[:6]}"
    agent, _ = _register("nosig-prov", skill, established=True)
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
    agent, _ = _register("rate-prov", skill, established=True)
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
    agent, _ = _register("rate2-prov", skill, established=True)
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
    agent, _ = _register("low-prov", skill, established=True)
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


def test_card_cannot_opt_out_of_the_free_period(monkeypatch):
    """**卡上没有退出免费期的通道** —— 任何想被发现的 agent，前 10 次完成调用免费。

    曾经卡上写 `x-a2n.trial=false` 就能一上来收费，那条路已经封掉，而且是**直接拒**
    而不是静默忽略：静默忽略等于让供给方以为自己退出了，等到被计费才发现 —— 那是欺骗。
    """
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-opt-{new_id('')[:6]}"
    with pytest.raises(ValidationError) as e:
        registry.register(_uid("opt-owner"), _card("opt-prov", skill, trial_decl=False))
    assert "免费期" in str(e.value), f"拒绝的理由要说清是哪条规矩：{e.value}"

    # 而"真的一开始就收费"的节点仍然存在 —— 但它的免费期是**走完的**，不是跳过的：
    # 部署级开关 A2N_TRIAL_DEFAULT=0（测试/演示口径），注册即毕业。
    agent, card = _register("opt-prov2", skill, established=True)
    assert trial.in_trial(agent["agent_id"]) is False
    with pytest.raises(PermissionError):
        resolve(agent["agent_id"], _uid("opt-user"), card)   # 毕业之后才收费

    # 卡上写 trial=true 是允许的：它只是复述本来就强制的规则，不是开关。
    ok = registry.register(_uid("opt-owner2"), _card("opt-prov3", skill, trial_decl=True))
    assert trial.in_trial(ok["agent_id"]) is True


def test_declaring_trial_true_grants_the_same_quota_as_silence(monkeypatch):
    """声明 `trial: true` 与不声明**完全等价** —— 字段不是开关，别让人以为它有用。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-same-{new_id('')[:6]}"
    a1 = registry.register(_uid("same1"), _card("same-prov1", skill))
    a2 = registry.register(_uid("same2"), _card("same-prov2", skill, trial_decl=True))
    p1, p2 = trial.progress(a1["agent_id"]), trial.progress(a2["agent_id"])
    assert (p1["cap"], p1["used"], p1["grant_kind"]) == (
        p2["cap"], p2["used"], p2["grant_kind"]) == (10, 0, trial.INITIAL)


# ---------------------------------------------------------------- 重连采样 / 自源差值

def test_reconnect_quota_is_stability_sampling_not_quality_evidence(monkeypatch):
    """重连给的免费额度是**采网络稳定参数**用的，不是继续攒质量证据。

    所以落在那一段里的交付虽然照常走完（免费、验收、评分），但：
    不进公开案例、不进质量统计。**同时必须看得见它有多少次** ——
    隔离不等于隐藏，隐藏等于假装没发生，那是另一种撒谎。
    """
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-stab-{new_id('')[:6]}"
    card = _card("stab-prov", skill)
    owner = _uid("stab-owner")
    agent = registry.register(owner, card)
    registry.heartbeat(agent["agent_id"])
    aid = agent["agent_id"]

    for _ in range(10):                    # 首装额度走完（自源，只吃额度不算证据）
        _call(owner, aid, skill)
    assert trial.progress(aid)["trial"] is False
    # 首装那 10 单是 INITIAL：它们是"换毕业证据"用的，照进质量模板
    assert trial.current_kind(aid) == trial.INITIAL
    samples_before = quality_facts(aid)["samples"]
    assert samples_before == 10, "首装额度的样本要进质量模板"

    _st, msg = trial.grant(aid)
    prog = trial.progress(aid)
    assert prog["grant_kind"] == trial.RECONNECT, msg
    assert prog["stability"] is True and prog["cap"] == 5 and prog["used"] == 0
    assert trial.current_kind(aid) == trial.RECONNECT
    assert "质量模板" in msg, "补额时必须说清这段额度是干什么的"

    user = _uid("stab-user")               # 重连期的这一单：非自源，但仍是采样
    t, res = _call(user, aid, skill)
    assert res["passed"] and res["amount"] == 0
    row = conn().execute("SELECT trial, trial_kind FROM tasks WHERE id=?", (t["id"],)).fetchone()
    assert row["trial"] == 1 and row["trial_kind"] == "RECONNECT", "建单那一刻就要冻住性质"

    rt = rate(t["id"], user, 90)
    assert rt["stability"] is True and rt["credited"] is False

    assert counts(aid)["non_self"] == 0, "稳定性采样不算公开证据"
    assert counts(aid)["stability"] == 1, "但必须数得出来"
    assert case_list(aid) == [], "也不进公开案例"
    q = quality_facts(aid)
    assert q["samples"] == samples_before, "质量模板的样本数不该被采样污染"
    assert q["stability_samples"] == 1, "被排除的采样数要报出来"
    assert "稳定性采样" in q["note"]
    o = objective_facts(aid)
    assert o["stability_calls"] == 1 and o["trial_calls"] == 11
    assert case_counts(aid)["stability_excluded"] == 1


def test_case_counts_says_why_cases_are_missing(monkeypatch):
    """案例被排掉多少条、为什么，要**数得出来** —— 否则"没有案例"读起来像"这家不行"。"""
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-cc-{new_id('')[:6]}"
    agent, _ = _register("cc-prov", skill)
    aid = agent["agent_id"]
    owner = conn().execute("SELECT principal_id FROM agents WHERE agent_id=?",
                           (aid,)).fetchone()["principal_id"]

    _call(owner, aid, skill)                       # 自源：不进公开案例
    u = _uid("cc-user")
    _call(u, aid, skill)                           # 独立：进公开案例
    c = case_counts(aid)
    assert c["done_total"] == 2 and c["self_excluded"] == 1
    assert c["stability_excluded"] == 0 and c["public_basis"] == 1
    assert len(case_list(aid)) == 1


def test_self_source_delta_is_evidence_not_a_verdict(monkeypatch):
    """自调用被允许，但要让使用者看得见"自评 vs 独立"差多少 —— 给差异，不给结论。

    两条纪律：① 用**原始分**（归一化会把"自打 95、别人打 60"抹平）；
    ② 样本不足时**不给差值**（给 0 比不给更误导）。
    """
    monkeypatch.setenv("A2N_TRIAL_DEFAULT", "1")
    skill = f"q-delta-{new_id('')[:6]}"
    card = _card("delta-prov", skill)
    owner = _uid("delta-owner")
    agent = registry.register(owner, card)
    registry.heartbeat(agent["agent_id"])
    aid = agent["agent_id"]

    for _ in range(3):                             # 自己调自己 3 次，每次自打 95
        t, _ = _call(owner, aid, skill)
        rate(t["id"], owner, 95)
    d = self_vs_independent(aid)
    assert d["self_cases"] == 3 and d["independent_cases"] == 0
    assert d["delta_raw"] is None and d["comparable"] is False, "只有一边的均值不是差值"
    assert "独立使用者" in d["note"]
    assert summary(aid)["samples"] == 0, "自源评分永远不进对外统计"

    for i in range(3):                             # 三位独立使用者打 60
        u = _uid(f"delta-user{i}")
        tt, _ = _call(u, aid, skill)
        rate(tt["id"], u, 60)
    d = self_vs_independent(aid)
    assert (d["self_cases"], d["independent_cases"]) == (3, 3)
    assert d["comparable"] is True
    assert d["delta_raw"] == 35.0, "95 − 60 = 35（原始分，未经归一化）"
    assert "绝不合成总分" in d["note"]
    # 差值参数不改动对外分数：独立评分要过最小样本门才发布，自源永远不发布
    assert summary(aid)["raters"] == 0
    assert counts(aid)["non_self"] == 3


# ---------------------------------------------------------------- 证据三段

def test_evidence_is_three_segments_never_one_score():
    """三段证据分开给出、各有口径；**没有任何"综合分"字段** —— 我们不给结论。"""
    skill = f"q-evi-{new_id('')[:6]}"
    agent, _ = _register("evi-prov", skill, established=True)
    user = _uid("evi-user")
    t, res = _call(user, agent["agent_id"], skill)
    assert res["passed"]
    rate(t["id"], user, 90)

    ev = evidence(agent["agent_id"], limit=5)
    assert set(ev) == {"agent_id", "objective", "quality", "ratings", "trial", "cases",
                       "self_source", "case_counts"}
    assert ev["objective"]["rtt_source"] == "平台探测"
    assert ev["objective"]["observed_source"] == "使用端实测"
    assert ev["objective"]["metering_source"] == "节点签名 + 平台观测对账"
    assert ev["quality"]["declared"] is True
    assert ev["quality"]["components"] is not None
    assert ev["ratings"]["published"] is False and ev["ratings"]["score"] is None
    assert "score" not in ev and "total" not in ev, "证据面不许出现一个合成的总分"
    # 新增的自源差值块也**不是**结论：它给的是两个原始分均值 + 差值 + 样本数，
    # 没有任何"真实分/可信度/加权后分数"这类合成字段。
    assert set(ev["self_source"]) == {"self_cases", "self_mean_raw", "independent_cases",
                                      "independent_mean_raw", "delta_raw", "comparable",
                                      "note"}
    assert len(ev["cases"]) == 1
    case = ev["cases"][0]
    assert "requester_id" not in case, "别人调的单不该把别人名字挂出来"
    assert case["quality"] is not None


def test_no_quality_metric_for_undeclared_template():
    """没声明模板 → 明确写"未声明验收模板"，而不是一个漂亮的 100 分。"""
    skill = f"q-notpl-{new_id('')[:6]}"
    agent, _ = _register("notpl-prov", skill, template=False, established=True)
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
    agent, _ = _register("tpl-nosample-prov", skill, template=True, established=True)
    q = quality_facts(agent["agent_id"])
    assert q["declared"] is True, "卡上有模板"
    assert q["measured"] is False, "但一次都没算过"
    assert q["quality"] is None, "没样本就不许给出一个数"
    assert "已声明验收模板" in q["note"] and "未声明" not in q["note"]
    assert q["template_version"] == "7", "声明的是哪个版本，也要能核对"
    # 列表页摘要同样带上 measured：徽标只在"真的算过"时才挂"偏差 x"
    assert quality_facts(agent["agent_id"])["measured"] is False
