"""C0 契约测试 —— 数据形状、稳定商品键、状态与额度模型。

这些是**纯逻辑**，不连网、不起节点、不碰钱：它们守的是
``a2n_sdk.coordination`` 这个"层间契约"本身。任何一条红了，说明契约与
[COORDINATION-API.md] 对不上了，先改契约再改实现。
"""
from __future__ import annotations

import pytest

from a2n_sdk import coordination as coord


def _card(**extra):
    card = {"name": "视频剪辑", "skills": [{"id": "video-edit"}], "description": "v1"}
    card.update(extra)
    return card


# ---------------------------------------------------------------- 稳定商品键


def test_candidate_key_rejects_empty_parts():
    with pytest.raises(coord.CoordinationError):
        coord.CandidateKey("", "svc_1")
    with pytest.raises(coord.CoordinationError):
        coord.CandidateKey("did:a2n:ag_x", "")


def test_candidate_key_stable_across_text_and_version_changes():
    """改错别字、升版本、换上游地址都**不换**商品身份（2026-09-29 修正的核心）。"""
    did = "did:a2n:ag_provider"
    k0 = coord.CandidateKey.of_card(did, _card())
    k1 = coord.CandidateKey.of_card(did, _card(description="改了错别字", version="2.0"))
    k2 = coord.CandidateKey.of_card(did, _card(url="http://127.0.0.1:9999/agent"))
    assert k0 == k1 == k2
    assert k0.service_id.startswith("svc_")


def test_candidate_key_differs_between_providers_and_skills():
    a = coord.CandidateKey.of_card("did:a2n:ag_a", _card())
    b = coord.CandidateKey.of_card("did:a2n:ag_b", _card())
    other = coord.CandidateKey.of_card("did:a2n:ag_a",
                                       {"name": "视频剪辑", "skills": [{"id": "audio-edit"}]})
    assert a != b and a != other


def test_candidate_key_roundtrip():
    key = coord.CandidateKey("did:a2n:ag_a", "svc_x")
    assert coord.CandidateKey.from_dict(key.to_dict()) == key


# ---------------------------------------------------------------- 额度模型


def test_budget_requires_positive_int_and_rejects_bool():
    ok = coord.Budget.defaults()
    assert ok.to_dict() == coord.DEFAULT_BUDGET
    for bad in ({"received_bytes": True}, {"duration_ms": 0}, {"max_concurrency": -1},
                {"remote_operations": 1.5}):
        payload = {**coord.DEFAULT_BUDGET, **bad}
        with pytest.raises(coord.CoordinationError):
            coord.Budget(**payload)


def test_budget_ledger_probe_is_a_sub_quota_not_a_gift():
    """探测同扣 probe_operations 与 remote_operations —— 子额度，不是白送的网络操作。"""
    led = coord.BudgetLedger(coord.Budget.defaults())
    assert led.probe() is True
    used = led.used()
    assert used["probe_operations"] == 1 and used["remote_operations"] == 1


def test_budget_ledger_reserve_is_atomic_on_overrun():
    led = coord.BudgetLedger(coord.Budget.defaults())
    assert led.reserve(received_bytes=2_000_000) is False
    assert led.used()["received_bytes"] == 0  # 失败不改账
    assert led.reserve(received_bytes=1000) is True
    assert led.used()["received_bytes"] == 1000


def test_budget_ledger_observe_uses_high_water_mark():
    led = coord.BudgetLedger(coord.Budget.defaults())
    assert led.observe("introduction_depth", 4) is False      # 超过总额度 3
    assert led.observe("introduction_depth", 2) is True
    assert led.observe("introduction_depth", 1) is True       # 不回退
    assert led.used()["introduction_depth"] == 2


def test_budget_ledger_restart_does_not_reset_spent():
    """崩溃重启按 used 恢复，**不得**因为重启获得额外额度（规则 §6.4）。"""
    led = coord.BudgetLedger(coord.Budget.defaults())
    led.reserve(remote_operations=20)
    revived = coord.BudgetLedger.from_dict(led.to_dict())
    assert revived.used()["remote_operations"] == 20
    assert revived.reserve(remote_operations=5) is False  # 只剩 4
    assert revived.reserve(remote_operations=4) is True


def test_budget_ledger_exhaustion_and_stop_reason():
    led = coord.BudgetLedger(coord.Budget.defaults())
    assert led.exhausted() is False and led.stop_reason() == ""
    assert led.reserve(remote_operations=24) is True
    assert led.exhausted() is True and led.stop_reason() == "remote_operations"


def test_budget_ledger_rejects_unknown_field_and_negative():
    led = coord.BudgetLedger(coord.Budget.defaults())
    with pytest.raises(coord.CoordinationError):
        led.reserve(nonsense=1)
    with pytest.raises(coord.CoordinationError):
        led.reserve(received_bytes=-1)


# ---------------------------------------------------------------- 公开条件指纹


def test_query_fingerprint_is_stable_and_excludes_private_fields():
    """指纹只由 {skill, coarse_requirements, page_size} 决定 —— request_id/私有偏好不进。"""
    fp = coord.query_fingerprint("video-edit", {"language": "zh"}, 10)
    assert fp == coord.query_fingerprint("video-edit", {"language": "zh"}, 10)
    assert len(fp) == 64 and fp == fp.lower()
    assert fp != coord.query_fingerprint("video-edit", {"language": "zh"}, 5)
    assert fp != coord.query_fingerprint("audio-edit", {"language": "zh"}, 10)
    with pytest.raises(coord.CoordinationError):
        coord.query_fingerprint("video-edit", {}, 0)
    with pytest.raises(coord.CoordinationError):
        coord.query_fingerprint("video-edit", {}, True)


# ---------------------------------------------------------------- 形状往返


def test_node_record_roundtrip_and_op_whitelist():
    rec = coord.NodeRecord(
        node_did="did:a2n:ag_a", versions=["a2n-coord/1"],
        coord_routes=[coord.CoordRoute("direct_https", "https://a/x")],
        operations=["HELLO", "FIND"], issued_at=1, expires_at=2)
    assert coord.NodeRecord.from_dict(rec.to_dict()).to_dict() == rec.to_dict()
    assert rec.covers("a2n-coord/1") and not rec.covers("a2n-coord/9")
    with pytest.raises(coord.CoordinationError):
        coord.NodeRecord(node_did="did:a2n:ag_a", versions=["a2n-coord/1"],
                         coord_routes=[], operations=["TELEPORT"],
                         issued_at=1, expires_at=2)


def test_referral_rejects_self_reference():
    rec = coord.NodeRecord(node_did="did:a2n:ag_a", versions=["a2n-coord/1"],
                           coord_routes=[], operations=["FIND"], issued_at=1, expires_at=2)
    with pytest.raises(coord.CoordinationError):
        coord.Referral(introducer_did="did:a2n:ag_a", target_record=rec,
                       target_record_hash="h", skill_hint=[], observation="x",
                       observed_at=1, expires_at=2)


def test_route_descriptor_channel_whitelist():
    key = coord.CandidateKey("did:a2n:ag_a", "svc_1")
    coord.RouteDescriptor("r1", key, "direct_a2a", "h", {}, 10)
    coord.RouteDescriptor("r2", key, "sealed_relay_a2a", "h", {}, 10, relay_did="did:a2n:ag_r")
    with pytest.raises(coord.CoordinationError):
        coord.RouteDescriptor("r3", key, "direct_https", "h", {}, 10)


def test_candidate_verification_whitelist():
    key = coord.CandidateKey("did:a2n:ag_a", "svc_1")
    assert coord.Candidate(key, verification="CARD_VERIFIED").to_dict()["verification"] == "CARD_VERIFIED"
    with pytest.raises(coord.CoordinationError):
        coord.Candidate(key, verification="TRUSTED")


def test_search_snapshot_state_whitelist_and_roundtrip():
    snap = coord.SearchSnapshot(search_id="s1", state="RUNNING", round=2)
    assert coord.SearchSnapshot(**snap.to_dict()).state == "RUNNING"
    with pytest.raises(coord.CoordinationError):
        coord.SearchSnapshot(search_id="s1", state="SEARCHING")


def test_search_spec_auto_continue_requires_total_budget():
    coord.SearchSpec(skill="video-edit", round_budget=coord.Budget.defaults())
    with pytest.raises(coord.CoordinationError):
        coord.SearchSpec(skill="video-edit", auto_continue=True)
    spec = coord.SearchSpec(skill="video-edit", auto_continue=True,
                            total_budget=coord.Budget.defaults())
    assert spec.coarse() == {"skill": "video-edit"}  # 私有偏好不上网


def test_policy_decision_and_route_plan_shapes():
    decision = coord.PolicyDecision("SATISFIED", reason_codes=["enough"],
                                    selected_keys=[coord.CandidateKey("did:a2n:ag_a", "svc_1")])
    assert decision.to_dict()["selected_keys"][0]["service_id"] == "svc_1"
    with pytest.raises(coord.CoordinationError):
        coord.PolicyDecision("MAYBE")
    plan = coord.RoutePlan(coord.CandidateKey("did:a2n:ag_a", "svc_1"),
                           preferred_route_id="r1")
    assert plan.to_dict()["key"]["provider_did"] == "did:a2n:ag_a"


def test_route_observation_unknown_is_none_not_false():
    """未知任务可达性必须是 None（unknown），不能写成 False（规则 §9.2）。"""
    obs = coord.RouteObservation(route_id="r1", measured_by="did:a2n:ag_me", at=1,
                                 probe_kind="control", control_reachable=True,
                                 task_reachable=None)
    assert obs.to_dict()["task_reachable"] is None
    assert obs.to_dict()["control_reachable"] is True


# ---------------------------------------------------------------- 枚举与端口


def test_operation_and_error_tables_are_complete():
    assert coord.OPERATIONS == ("HELLO", "FIND", "GET_CARD", "RESOLVE_ROUTES",
                                "PROBE", "FORWARD_COORD")
    assert len(coord.RESULT_TYPES) == len(coord.OPERATIONS) + 1  # +ERROR
    for code, http in coord.ERROR_HTTP.items():
        assert isinstance(http, int) and 200 <= http <= 599, code
    # 契约 §7 明确列出的码一个都不能少
    for code in ("INVALID_REQUEST", "UNVERIFIED_IDENTITY", "FORBIDDEN_TARGET",
                 "NOT_FOUND", "REV_CONFLICT", "RESULT_CHANGED",
                 "IDEMPOTENCY_CONFLICT", "STATE_CONFLICT", "CURSOR_EXPIRED",
                 "SESSION_EXPIRED", "REQUEST_TOO_LARGE", "RESPONSE_TOO_LARGE",
                 "UNSUPPORTED_ROUTE", "UNSUPPORTED_VERSION", "REV_REQUIRED",
                 "RATE_LIMITED", "UNREACHABLE", "BUSY", "DEADLINE_EXCEEDED",
                 "NO_MATCH_IN_SNAPSHOT"):
        assert code in coord.ERROR_HTTP


def test_state_sets_are_consistent():
    assert coord.TERMINAL_SEARCH_STATES <= set(coord.SEARCH_STATES)
    assert coord.RESUMABLE_STATES <= set(coord.SEARCH_STATES)
    assert not (coord.TERMINAL_SEARCH_STATES & coord.RESUMABLE_STATES)


def test_three_ports_are_protocols():
    from typing import Protocol
    for port in (coord.CoordinationPort, coord.LocalCandidatePolicy,
                 coord.CoordinationNetworkPort):
        assert issubclass(port, Protocol)
        assert getattr(port, "_is_runtime_protocol", False) is True
