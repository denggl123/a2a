import ast
import copy
import json
from pathlib import Path

import pytest

from a2n_sdk.selection.contracts import metric
from a2n_sdk.selection.metrics import aggregate, credit, execution, network, normalize, personalized
from a2n_sdk.selection.scoring import DEFAULT_PROFILE, normalize_profile, rank
from a2n_sdk.selection.service import SelectionService
from a2n_sdk.storage import LocalStore

NOW = 200 * 86400


def candidate(name="a", provider=None, **extra):
    return {"key": (provider or name)+"|"+name, "provider_did": provider or name,
            "service_id": name, "version": "1", "skills": ["extract"],
            "input_modes": ["text/plain"], "output_modes": ["application/json"],
            "methods": ["points"], "point_modes": ["PAY"], "verified": True,
            "tags": [], "costs": [], **extra}


def opinion(author="b", uid="1", value=1, **extra):
    return {"trade_uid": uid, "author_did": author, "at": NOW,
            "revision": 1, "value": value, "ref": uid, **extra}


def test_pure_modules_cannot_import_business_implementations_or_io():
    root = Path(__file__).parents[1]/"packages/a2n-sdk/src/a2n_sdk/selection"
    allowed = {"__future__", "math", "hashlib", "json", "typing", "secrets", "time", "re"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1
                if not node.level:
                    assert node.module.split(".")[0] in allowed
            if isinstance(node, ast.Import):
                assert all(x.name.split(".")[0] in allowed for x in node.names)


def test_normalization_is_anchored_not_relative_to_other_candidates():
    assert normalize(5, 1, 10) == pytest.approx(5/9)
    assert normalize(20, 1, 10) == 0
    assert normalize(0, 1, 10) == 1
    for bad in ((1,1), (10,1)):
        with pytest.raises(ValueError):
            normalize(5, *bad)
    for invalid in (True, float("nan"), float("inf"), -1):
        with pytest.raises(ValueError):
            normalize(invalid, 1, 10)
    first = rank([candidate()], now=NOW)
    second = rank([candidate(), candidate("other", costs=[{"currency":"CNY", "amount_minor":10**20,"complete":True}])], now=NOW)
    assert first["items"][0]["score"] == next(r for r in second["items"] if r["key"] == "a|a")["score"]


def test_raw_public_copies_and_same_counterparty_cannot_multiply_contribution():
    one = aggregate([opinion()], now=NOW)
    assert one == aggregate([opinion()]*100, now=NOW)
    repeated = aggregate([opinion(uid=str(i)) for i in range(100)], now=NOW)
    assert repeated["value"] == one["value"]
    assert repeated["support"]["effective_mass"] == 1
    assert repeated["status"] == "LIMITED"
    independent = aggregate([opinion(author=str(i), uid=str(i)) for i in range(3)], now=NOW)
    assert independent["status"] == "SUPPORTED"
    assert independent["value"] == pytest.approx(.8)


def test_revisions_replace_and_do_not_refresh_trade_age():
    original = opinion(at=NOW-90*86400, value=0)
    revision = opinion(at=NOW-90*86400, value=1, revision=2)
    result = aggregate([original, revision], now=NOW)
    assert result["support"]["samples"] == 1
    assert result["support"]["effective_mass"] == .5
    assert result["value"] == pytest.approx(.6)
    assert aggregate([original, revision], now=NOW) == aggregate([revision, original], now=NOW)


def test_personal_experience_does_not_need_three_people_and_keeps_unknown_external_neutral():
    result = personalized([opinion(uid=str(i), value=0) for i in range(20)], [], now=NOW)
    assert result["status"] == "SUPPORTED"
    assert result["value"] == pytest.approx(.22)
    assert personalized([], [], now=NOW)["value"] is None


def test_credit_closure_cannot_create_bonus_single_external_claim_is_not_applied():
    base = metric(.85, status="SUPPORTED")
    good = [opinion(value=0)]
    assert credit(base, good, good, now=NOW)["value"] == .85
    alone = credit(base, [], [], now=NOW, public_handling=[opinion()], complete=True)
    assert alone["value"] == .85
    own = credit(base, [opinion()], [], now=NOW)
    assert own["value"] == pytest.approx(.65)
    bad = [opinion(author=str(i), uid=str(i)) for i in range(3)]
    assert credit(base, [], [], now=NOW, public_breaches=bad, complete=True)["value"] == pytest.approx(.49)
    assert credit(base, [], [], now=NOW, public_breaches=bad, complete=False)["value"] == .85


def test_credit_same_proved_behavior_is_not_deducted_as_handling_again():
    base=metric(.85,status="SUPPORTED")
    breach=[opinion(value=1)]
    assert credit(base,breach,breach,now=NOW)["value"]==credit(base,breach,[],now=NOW)["value"]


def test_bounded_explanations_and_invalid_future_network_samples():
    result=aggregate([opinion(author=str(i),uid=str(i)) for i in range(500)],now=NOW)
    assert result['ref_count']==500 and len(result['refs'])==64 and result['refs_truncated']
    result=network([{'checked_at':NOW+1,'reachable':True,'rtt_ms':1},
                    {'checked_at':NOW,'reachable':1,'rtt_ms':1}],now=NOW,anchors=DEFAULT_PROFILE['anchors'])
    assert result['value'] is None


def test_budget_uses_required_points_mode_and_precise_decimal_integers():
    c=candidate(costs=[{'currency':'points:a','amount_minor':0,'complete':True,'mode':'EARN'},
                       {'currency':'points:a','amount_minor':100,'complete':True,'mode':'PAY'}],point_modes=['EARN','PAY'])
    cfg=normalize_profile({'required':{'point_modes':['PAY']},'budgets':{'points:a':{'comfortable':'0','maximum':'50'}}})
    row=rank([c],profile=cfg,now=NOW)['items'][0]
    assert row['state']=='FAIL' and row['dimensions']['cost']['value']==0
    assert normalize_profile({'budgets':{'wei':{'comfortable':str(2**128),'maximum':str(2**129)}}})['budgets']['wei']['maximum']==2**129


def test_timeouts_are_not_hidden_and_are_not_quality_or_execution_failures():
    success = [{"trade_uid":str(i),"at":NOW,"execution":"DELIVERED","elapsed_ms":1000,
                "first_useful_ms":1000,"complete_timing":True} for i in range(10)]
    timeout = [{"trade_uid":"timeout"+str(i),"at":NOW,"execution":"UNKNOWN","elapsed_ms":60000,
                "no_useful_result":True} for i in range(10)]
    a = execution(success, now=NOW, anchors=DEFAULT_PROFILE["anchors"])
    b = execution(success+timeout, now=NOW, anchors=DEFAULT_PROFILE["anchors"])
    assert b["time"]["value"] < a["time"]["value"]
    assert b["reliability"]["value"] == a["reliability"]["value"]
    assert b["reliability"]["status"] == "LIMITED"
    assert b["time"]["raw"]["outcomes"]["UNKNOWN"] == 10
    network_failure = [{**r,"failure_origin":"network"} for r in timeout]
    assert execution(network_failure, now=NOW, anchors=DEFAULT_PROFILE["anchors"])["time"]["value"] is None


def test_network_retries_use_time_buckets_and_stale_is_explicit():
    obs = {"checked_at":NOW,"reachable":True,"rtt_ms":100,"kind":"tcp-connect"}
    one = network([obs], now=NOW, anchors=DEFAULT_PROFILE["anchors"])
    repeated = network([obs]*100, now=NOW, anchors=DEFAULT_PROFILE["anchors"])
    assert one["value"] == repeated["value"]
    assert one["support"]["buckets"] == 1
    assert one["raw"]["probe_kind"] == "tcp-connect"
    assert network([{**obs,"checked_at":NOW-121}], now=NOW, anchors=DEFAULT_PROFILE["anchors"])["status"] == "STALE"


def test_hard_unknown_and_failure_cannot_be_compensated_by_high_scores():
    cfg = normalize_profile({"required":{"min_quality":.7,"require_supported":True}})
    result = rank([candidate(), candidate("wrong",skills=["translate"])], profile=cfg, task={"skill":"extract"}, now=NOW)
    assert result["counts"] == {"acquired":2,"pass":0,"pending":1,"excluded":1}
    assert next(r for r in result["items"] if r["key"]=="a|a")["dimensions"]["quality"]["value"] is None
    cfg = normalize_profile({"required":{"point_modes":["PAY"]}})
    assert rank([candidate(point_modes=["DEBT"])], profile=cfg, now=NOW)["items"][0]["state"] == "FAIL"


def test_points_amounts_are_only_compared_in_personally_accepted_units():
    cfg = normalize_profile({"budgets":{"points:issuer-A":{"comfortable":50,"maximum":100}}})
    result = rank([candidate(costs=[{"currency":"points:issuer-B","amount_minor":1,"complete":True}])], profile=cfg, now=NOW)
    assert result["items"][0]["state"] == "UNKNOWN"
    assert result["items"][0]["dimensions"]["cost"]["value"] is None
    cfg = normalize_profile({"budgets":{"points:issuer-A":{"comfortable":0,"maximum":0}}})
    result = rank([candidate(costs=[{"currency":"points:issuer-A","amount_minor":0,"complete":True}])], profile=cfg, now=NOW)
    assert result["items"][0]["dimensions"]["cost"]["value"] == 1


def test_newcomer_opportunity_never_bypasses_local_block_and_is_deterministic():
    rows = [candidate(str(i), provider="large" if i<4 else str(i)) for i in range(10)]
    blocked = candidate("blocked", opportunity="BLOCKED_LOCAL")
    metrics = {r["key"]:{"quality":metric(.9, status="SUPPORTED")} for r in rows[:8]}
    before = copy.deepcopy([rows,metrics])
    result = rank(rows+[blocked], metrics=metrics, now=NOW, seed="session")
    assert any(r["newcomer"] for r in result["items"][:5])
    assert all(r["key"] != "blocked|blocked" for r in result["items"][:5])
    assert sum(r["provider_did"]=="large" for r in result["items"][:5]) <= 2
    assert result == rank(list(reversed(rows+[blocked])), metrics=metrics, now=NOW, seed="session")
    assert [rows,metrics] == before


def test_durable_changes_are_atomic_with_facts_and_sequences_survive_pruning():
    store = LocalStore()
    store.track_changes(["feedback"])
    with pytest.raises(RuntimeError):
        with store.tx():
            store.put("feedback","one",{"rating":1})
            raise RuntimeError("crash")
    assert store.get("feedback","one") is None and store.changes() == []
    store.put("feedback","one",{"rating":1})
    head = store.change_head()
    store.prune_changes(head)
    assert store.change_head() == head
    store.delete("feedback","one")
    assert store.changes(after=head)[0]["sequence"] > head


class LocalSource:
    def snapshot(self, sid, revision, task, profile, now):
        return {"candidates":[candidate(str(i)) for i in range(150)],"metrics":{},"task":task,
                "result_revision":1,"metrics_revision":"one","coverage":{"global":"UNKNOWN"}}


def test_service_pages_are_frozen_profile_revision_is_checked_and_explain_has_no_io():
    book = SelectionService(LocalStore(), LocalSource(), now=lambda:NOW)
    first = book.assess({"search_id":"local-search","limit":100})
    assert first["counts"]["acquired"] == 150
    assert len(first["items"]) == 100 and first["next_cursor"]
    book.update_profile("balanced", {"required":{"min_quality":.9}}, expected_revision=0)
    second = book.page(first["snapshot_id"],cursor=first["next_cursor"],limit=100)
    assert len(second["items"]) == 50 and second["profile_revision"] == 0
    assert len({r["key"] for r in first["items"]+second["items"]}) == 150
    assert book.explain(first["snapshot_id"],first["items"][0]["key"])["item"] == first["items"][0]
    with pytest.raises(ValueError,match="REV_CONFLICT"):
        book.update_profile("balanced",{},expected_revision=0)
    wrong = json.loads(first["next_cursor"]); wrong[0]="other"
    with pytest.raises(ValueError,match="CURSOR"):
        book.page(first["snapshot_id"],cursor=json.dumps(wrong))


@pytest.mark.parametrize("bad", [{"weights":{"quality":1}}, {"top_k":True}, {"anchors":{"first":[0,0]}},
                                  {"required":{"magic":True}}, {"budgets":{"CNY":{"comfortable":2,"maximum":1}}}])
def test_invalid_profiles_do_not_silently_change_rules(bad):
    with pytest.raises(ValueError):
        normalize_profile(bad)
