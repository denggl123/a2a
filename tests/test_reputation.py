import math

import pytest

from a2n_sdk.reputation import calculate, configuration

SUBJECT = {"kind": "service", "provider_did": "seller", "service_id": "svc"}
NOW = 200 * 86400


def opinion(author="buyer", uid="one", score=5, at=NOW, version="1", fid=None):
    return {"record": {"author_did": author, "trade_uid": uid, "feedback_id": fid or uid,
        "provider_did": "seller", "service_id": "svc", "direction": "buyer_to_seller",
        "dimensions": {"quality": score}, "trade_at": at, "service_version": version,
        "published_at": NOW, "publication_revision": 1},
        "eligible": True, "fresh": True, "chain_complete": True, "conflict": False}


def quality(views, **kwargs):
    return calculate(SUBJECT, views, now=NOW, **kwargs)["dimensions"]["quality"]


def test_empty_is_neutral_with_zero_support():
    row = quality([])
    assert row["theta"] == .5 and row["support"] == "UNKNOWN"
    assert row["n_eff"] == row["confidence"] == row["effective_mass"] == 0


def test_hundred_repeated_trades_have_one_counterparty_not_hundred():
    row = quality([opinion(uid=str(i)) for i in range(100)])
    assert row["effective_mass"] == 1 and row["n_eff"] == row["counterparty_groups"] == 1
    assert row["theta"] == pytest.approx(2 / 3)
    assert math.fsum(r["weight"] for r in row["contributions"][0]["records"]) == pytest.approx(1)


def test_self_testing_has_no_independent_contribution():
    result = calculate(SUBJECT, [opinion(author="seller")], now=NOW)
    assert result["dimensions"]["quality"]["effective_mass"] == 0
    assert result["excluded"][0]["reason"] == "SELF_SOURCE"


def test_group_support_and_decay_are_distinct_and_version_weight_is_explained():
    row = quality([opinion(author=str(i), uid=str(i), at=NOW - 900 * 86400) for i in range(100)])
    assert row["counterparty_groups"] == 100 and row["n_eff"] == pytest.approx(100)
    assert row["effective_mass"] == pytest.approx(100 / 1024)
    assert row["support"] == "LIMITED"
    current = quality([opinion()], current_version="2")
    assert current["effective_mass"] == .5
    assert current["contributions"][0]["records"][0]["version_weight"] == .5


def test_duplicate_trade_opinions_and_ineligible_versions_are_not_double_weighted():
    old = opinion(score=1)
    newest = opinion(score=5, fid="new")
    newest["record"]["published_at"] += 1
    expired = opinion(author="other", uid="expired")
    expired.update(eligible=False, fresh=False)
    result = calculate(SUBJECT, [old, newest, expired], now=NOW)
    assert result["included_count"] == 1
    assert {r["reason"] for r in result["excluded"]} == {"DUPLICATE_TRADE_OPINION", "EXPIRED"}
    assert result["dimensions"]["quality"]["theta"] == pytest.approx(2 / 3)


def test_underflow_zero_weights_and_bad_parameters_are_safe():
    assert quality([opinion(at=-1e100)])["n_eff"] == 0
    for overrides in ({"neutral_mass": 0}, {"half_life_days": float("nan")},
                      {"other_version_weight": 2}, {"counterparty_cap": True}):
        with pytest.raises(ValueError):
            configuration(overrides)


def test_fixed_clock_replay_is_identical_and_not_sensitive_to_order():
    rows = [opinion(author="b", uid="b", score=2), opinion(author="a", uid="a")]
    assert calculate(SUBJECT, rows, now=NOW) == calculate(SUBJECT, list(reversed(rows)), now=NOW)
