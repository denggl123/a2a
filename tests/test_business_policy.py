from types import SimpleNamespace

import pytest

from a2n_sdk.policies import BusinessCandidatePolicy, PolicyBook, DEFAULT_POLICY, RESTRICTION_TTL_SECONDS
from a2n_sdk.reputation import calculate
from a2n_sdk.storage import LocalStore
SUBJECT = {"kind": "service", "provider_did": "seller", "service_id": "svc"}
NOW = 200 * 86400


def opinion(author, uid, score, at):
    return {"record": {"author_did": author, "trade_uid": uid, "feedback_id": uid,
        "provider_did": "seller", "service_id": "svc", "direction": "buyer_to_seller",
        "dimensions": {"quality": score}, "trade_at": at, "service_version": "1",
        "published_at": NOW, "publication_revision": 1},
        "eligible": True, "fresh": True, "chain_complete": True, "conflict": False}


def view(count, *, at=NOW):
    result = calculate(SUBJECT, [opinion(author=str(i), uid=str(i), score=1, at=at) for i in range(count)], now=NOW,
                       coverage={"known_sources_complete": True, "global_completeness": "UNKNOWN"})
    return result


def test_shadow_restriction_needs_new_trades_and_recovers_after_decay():
    book = PolicyBook(LocalStore())
    assert book.assess(view(5))["state"] == "OBSERVE"
    repeated = book.assess(view(5))
    assert repeated["state"] == "OBSERVE" and not repeated["new_trades"]
    assert book.assess(view(6))["state"] == "LIMITED"
    assert book.assess(view(7))["state"] == "MANUAL_ONLY"
    assert book.assess(view(7))["effective_state"] == "NORMAL"
    assert book.assess(view(7, at=NOW - 2000 * 86400))["state"] == "NORMAL"


def test_local_enforcement_requires_support_and_completed_known_source_query():
    book = PolicyBook(LocalStore())
    cfg = {**DEFAULT_POLICY, "mode": "ENFORCE_LOCAL"}
    assert book.update("local-business/1", cfg, expected_revision=0)["revision"] == 1
    with pytest.raises(ValueError, match="REV_CONFLICT"):
        book.update("local-business/1", cfg, expected_revision=0)
    insufficient = view(50, at=NOW - 1000 * 86400)
    for dimension in insufficient["dimensions"].values():
        for group in dimension["contributions"]:
            for record in group["records"]:
                record["trade_uid"] = "historical:" + record["trade_uid"]
    assert book.assess(insufficient)["state"] == "NORMAL"
    missing = view(5)
    missing["coverage"]["known_sources_complete"] = False
    assert book.assess(missing)["effective_state"] == "NORMAL"
    assert book.assess(view(6))["effective_state"] == "OBSERVE"
    assert book.assess(view(7))["effective_state"] == "LIMITED"


def test_incomplete_sources_retain_qualified_decision_without_escalation_or_renewal():
    clock = [NOW]
    store = LocalStore()
    book = PolicyBook(store, now=lambda: clock[0])
    book.update("local-business/1", {**DEFAULT_POLICY, "mode":"ENFORCE_LOCAL"}, expected_revision=0)
    book.assess(view(5))
    original = book.assess(view(6))
    missing = view(7)
    missing["coverage"]["known_sources_complete"] = False
    clock[0] += 60
    retained = book.assess(missing)
    assert retained["state"] == retained["effective_state"] == "LIMITED"
    assert retained["supported_decision"] == original["supported_decision"]
    assert retained["seen_trades"] == original["seen_trades"]
    empty = view(0)
    empty["coverage"]["known_sources_complete"] = False
    assert book.assess(empty)["effective_state"] == "LIMITED"
    # Repeating the same complete observations cannot extend a restriction.
    assert book.assess(view(6))["supported_decision"] == original["supported_decision"]
    assert book.assess(view(7))["state"] == "MANUAL_ONLY"
    clock[0] += RESTRICTION_TTL_SECONDS + 1
    assert book.opportunity(SUBJECT)["effective_state"] == "OBSERVE"
    expired = book.assess(empty)
    assert expired["state"] == expired["effective_state"] == "OBSERVE"
    assert expired["supported_decision"] is None


def test_complete_withdrawal_and_explicit_unblock_clear_a_retained_restriction():
    book = PolicyBook(LocalStore())
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"ENFORCE_LOCAL"}, expected_revision=0)
    book.assess(view(5))
    assert book.assess(view(6))["effective_state"] == "LIMITED"
    assert book.assess(view(0))["state"] == "NORMAL"
    book.assess(view(5))
    assert book.assess(view(8))["state"] == "LIMITED"
    book.block(SUBJECT, blocked=False)
    missing = view(8)
    missing["coverage"]["known_sources_complete"] = False
    assert book.assess(missing)["effective_state"] == "NORMAL"


def test_legacy_supported_restriction_migrates_without_extending_original_deadline():
    store = LocalStore()
    book = PolicyBook(store, now=lambda: NOW + 10)
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"ENFORCE_LOCAL"}, expected_revision=0)
    from a2n_sdk.trade_facts import digest
    store.put("local_opportunities", digest(SUBJECT), {"state":"LIMITED","seen_trades":[],
        "at":NOW,"mode":"ENFORCE_LOCAL","effective_state":"LIMITED","reasons":["NEW_SUPPORTED_LOW_TRADES"]})
    missing=view(0)
    missing["coverage"]["known_sources_complete"]=False
    result=book.assess(missing)
    assert result["effective_state"]=="LIMITED"
    assert result["supported_decision"]["expires_at"]==NOW+RESTRICTION_TTL_SECONDS


def test_owner_block_requires_explicit_unblock_even_with_good_or_decayed_opinions():
    book = PolicyBook(LocalStore())
    book.block(SUBJECT, blocked=True)
    assert book.assess(view(0))["effective_state"] == "BLOCKED_LOCAL"
    book.block(SUBJECT, blocked=False)
    assert book.assess(view(0))["effective_state"] == "NORMAL"


def test_policy_mode_change_immediately_updates_existing_restrictions():
    book = PolicyBook(LocalStore())
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"ENFORCE_LOCAL"}, expected_revision=0)
    book.assess(view(5))
    book.assess(view(6))
    original = book.assess(view(7))["supported_decision"]
    assert book.opportunity(SUBJECT)["effective_state"] == "MANUAL_ONLY"
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"SHADOW"}, expected_revision=1)
    assert book.list()[0]["effective_state"] == "NORMAL"
    assert book.opportunity(SUBJECT)["supported_decision"] == original
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"ENFORCE_LOCAL"}, expected_revision=2)
    assert book.opportunity(SUBJECT)["effective_state"] == "MANUAL_ONLY"
    book.update("local-business/1", {**DEFAULT_POLICY,"mode":"DISABLED"}, expected_revision=3)
    assert book.opportunity(SUBJECT)["supported_decision"] is None
    book.block(SUBJECT, blocked=True)
    assert book.list()[0]["effective_state"] == "BLOCKED_LOCAL"


def candidate(provider, version="1", amount=0):
    card = {"version": version, "x-a2n": {"price_book": {"write": {"CNY": {
        "dimensions": [{"key": "call_count", "amount": amount, "per": 1}]}}}}}
    return {"key": {"provider_did": provider, "service_id": "svc"}, "verification": "CARD_VERIFIED",
            "cards": [{"card": card}]}


class EmptyReputation:
    def get(self, subject):
        return None


def test_exploration_does_not_bypass_version_price_or_authorize_execution():
    spec = SimpleNamespace(policy_id="local-business/1", skill="write", required={"version": "1", "budget_minor": 0, "currency": "CNY"},
                           preferences={"min_candidates": 1})
    policy = BusinessCandidatePolicy(spec, EmptyReputation(), PolicyBook(LocalStore()))
    result = policy.evaluate([candidate("expensive", amount=10), candidate("wrong", version="2"), candidate("new")], "1", {})
    assert result.decision == "SATISFIED"
    assert [k.provider_did for k in result.selected_keys] == ["new"]
    assert "DISPLAY_ONLY" in result.reason_codes
    spec.required["allow_unknown"] = False
    assert policy.evaluate([candidate("new")], "1", {}).decision == "NEEDS_INPUT"
