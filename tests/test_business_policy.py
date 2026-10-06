from types import SimpleNamespace

import pytest

from a2n_sdk.policies import BusinessCandidatePolicy, PolicyBook, DEFAULT_POLICY
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
    assert book.assess(view(6))["effective_state"] == "LIMITED"


def test_owner_block_requires_explicit_unblock_even_with_good_or_decayed_opinions():
    book = PolicyBook(LocalStore())
    book.block(SUBJECT, blocked=True)
    assert book.assess(view(0))["effective_state"] == "BLOCKED_LOCAL"
    book.block(SUBJECT, blocked=False)
    assert book.assess(view(0))["effective_state"] == "NORMAL"


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
