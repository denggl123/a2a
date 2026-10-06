"""B0: observable delivery, durable projections, free admission and recovery."""
import pytest

from a2n_sdk.calls import CallService
from a2n_sdk.feedback import FeedbackBook
from a2n_sdk.pipeline import CallPipeline, CallbackAcceptance, CallbackSettlement
from a2n_sdk.ports import AgentTarget, CallRequest, CallResponse, CallOutcome
from a2n_sdk.storage import LocalStore
from a2n_sdk.trade_facts import TradeFactsBook, axes, trade_uid
from a2n_sdk.trials import TrialBook


class Transport:
    def invoke(self, _target, _request):
        return CallResponse.success("actual bad output")


def test_rejected_delivery_keeps_sample_and_can_receive_quality_feedback():
    store = LocalStore()
    request = CallRequest(task_id="bad", payload="requested", metadata={"a2nSampleConsent": {
        "input_public": True, "output_public": True}})
    outcome = CallPipeline(Transport(), acceptance=CallbackAcceptance(
        lambda *_: {"passed": False, "quality_measured": True})).invoke(AgentTarget("svc", {}), request)
    assert not outcome.ok and outcome.state == "REJECTED"
    outcome.metadata["sample_policy"] = {"safe_output": True}
    store.claim("svc", request.task_id, "fingerprint")
    store.finish("svc", request.task_id, outcome.to_dict())
    trials = TrialBook(store)
    trials.admit("svc", request.task_id, caller_did="buyer", provider_did="seller")
    sample = trials.record("svc", request.task_id, outcome, request=request)
    assert sample["quality"] == "RULE_FAIL" and "actual bad output" in sample["preview"]
    assert trials.status("svc")["completed"] == 1
    book = FeedbackBook(store)
    feedback = book.open(scope="svc", task_id="bad", direction="buyer_to_seller",
                         author_did="buyer", provider_did="seller", dimensions={"quality": 1})
    assert feedback["task_state"] == "REJECTED"
    assert book.revise(feedback_id=feedback["feedback_id"], dimensions={"quality": 2})["revision"] == 2


def test_payment_failure_does_not_erase_delivery():
    outcome = CallPipeline(Transport(), settlement=CallbackSettlement(
        lambda *_: {"state": "FAILED"})).invoke(AgentTarget("svc", {}), CallRequest())
    assert axes(outcome.to_dict())["execution"] == "DELIVERED"
    assert axes(outcome.to_dict())["payment"] == "FAILED"
    assert axes(CallOutcome(False, "t", "TIMEOUT").to_dict())["execution"] == "UNKNOWN"


def test_projection_crash_rolls_back_then_recovers_without_rerunning_agent():
    store = LocalStore()
    trials = TrialBook(store)
    seen = []
    fail = [True]
    def project(scope, request, outcome):
        trials.record(scope, request.task_id, outcome)
        if fail[0]:
            raise RuntimeError("projection crash")
    def invoke(_scope, request):
        seen.append(request.task_id)
        return CallOutcome(True, request.task_id, "COMPLETED", result="output")
    calls = CallService(invoke, store, on_delivery=project, durable_delivery=True)
    try:
        outcome = calls.invoke("svc", CallRequest(task_id="one"))
        assert outcome.result == "output" and outcome.metadata["projection_pending"]
        assert trials.status("svc")["completed"] == 0
        fail[0] = False
        calls.recover_deliveries()
        calls.recover_deliveries()
        assert trials.status("svc")["completed"] == 1
        assert seen == ["one"]
        assert not calls.get("svc", "one").metadata.get("projection_pending")
    finally:
        calls.stop()


def test_free_terms_frozen_at_nine_and_bounded_even_when_unknown():
    trials = TrialBook(LocalStore())
    for i in range(9):
        trials.record("svc", str(i), {"state": "COMPLETED", "ok": True, "result": i})
    a = trials.admit("svc", "a")
    b = trials.admit("svc", "b")
    assert a["free"] and b["free"]
    with pytest.raises(ValueError, match="CAPACITY_LIMIT"):
        trials.admit("svc", "c")
    trials.finish_admission("svc", "a", {"state": "TIMEOUT", "ok": False})
    with pytest.raises(ValueError, match="CAPACITY_LIMIT"):
        trials.admit("svc", "c")
    for tid in ("b", "a"):
        data = {"state": "COMPLETED", "ok": True, "result": tid}
        trials.record("svc", tid, data)
        trials.finish_admission("svc", tid, data)
    assert trials.status("svc")["completed"] == 11
    samples = trials.samples("svc")
    assert len([s for s in samples if s["sample_group"] == "INITIAL"]) == 10
    assert len(samples) == 11 and samples[-1]["sample_group"] == "INITIAL_OVERFLOW" and samples[-1]["slot"] is None
    assert trials.store.get("trial_admissions", "svc::a")["free"] is True


def test_ready_recovered_but_running_unknown_never_replayed():
    store = LocalStore()
    request = CallRequest(task_id="ready")
    from dataclasses import asdict
    store.claim("svc", "ready", CallService._fingerprint(asdict(request)), asdict(request), state="READY")
    store.claim("svc", "unknown", "old", asdict(CallRequest(task_id="unknown")))
    seen = []
    calls = CallService(lambda _s, req: (seen.append(req.task_id) or
        CallOutcome(True, req.task_id, "COMPLETED", result="ok")), store)
    try:
        assert calls.get("svc", "unknown").state == "INTERRUPTED"
        calls.recover_ready()
        assert calls.invoke("svc", request).result == "ok"
        assert seen == ["ready"]
    finally:
        calls.stop()


def test_task_replay_ignores_transport_but_binds_business_and_caller():
    store = LocalStore()
    seen = []
    calls = CallService(lambda _s, req: (seen.append(req.task_id) or
        CallOutcome(True, req.task_id, "COMPLETED")), store)
    try:
        for nonce in (1, 2):
            calls.invoke("svc", CallRequest(task_id="t", payload="input", metadata={
                "_a2n_transport": {"nonce": nonce}, "_a2n_verified_peer": {"caller_did": "buyer"}}))
        assert seen == ["t"]
        with pytest.raises(ValueError):
            calls.invoke("svc", CallRequest(task_id="t", payload="different"))
        with pytest.raises(ValueError):
            calls.invoke("svc", CallRequest(task_id="t", payload="input", metadata={
                "_a2n_verified_peer": {"caller_did": "other"}}))
    finally:
        calls.stop()


def test_trade_identity_stable_and_unknown_party_not_invented():
    store = LocalStore()
    facts = TradeFactsBook(store)
    request = CallRequest(task_id="task")
    row = facts.admit("svc", request, {"buyer_did": "buyer", "provider_did": "seller", "service_id": "svc"})
    assert row["trade_uid"] == trade_uid("buyer", "seller", "svc", "task")
    assert row["trade_uid"] != trade_uid("other", "seller", "svc", "task")
    local = facts.admit("svc", CallRequest(task_id="unsigned"), {"provider_did": "seller"})
    assert local["buyer_did"] == ""
    assert not local["relation_verified"]
