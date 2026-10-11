"""Algorithm fixtures are synthetic tests, never production calibration evidence."""
import copy
import pytest
from a2n_sdk.selection.learning import fit
from a2n_sdk.selection.scoring import DEFAULT_PROFILE
from a2n_sdk.selection.service import SelectionService
from a2n_sdk.storage import LocalStore

NOW = 200 * 86400


def observations():
    return [{"trade_uid": "trade-" + str(i), "provider_did": "provider-" + str(i % 3),
        "reference": "ref-" + str(i), "at": NOW - (80 - i) * 4000,
        "label_at": NOW - (80 - i) * 4000 + 10, "origin": "PRODUCTION",
        "label_source": "HUMAN", "consent": True, "withdrawn": False, "known_self": False,
        "verified_delivery": True, "features": {k: float(i % 2) if k == "quality" else .5 for k in DEFAULT_PROFILE["weights"]},
        "label": float(i % 2)} for i in range(80)]


def test_private_learning_improves_later_records_with_bounded_weights():
    baseline = DEFAULT_PROFILE["weights"]
    result = fit(observations(), baseline, now=NOW)
    assert result["state"] == "READY" and result["heldout"]["candidate_mse"] < result["heldout"]["baseline_mse"]
    assert abs(sum(result["weights"].values()) - 1) < 1e-9
    assert all(abs(result["weights"][k] - baseline[k]) <= .050000001 for k in baseline)
    assert result["weights"]["quality"] > baseline["quality"]
    assert fit(observations(), baseline, now=NOW) == result


@pytest.mark.parametrize("field,value", [("origin", "CONTROLLED"), ("label_source", "ASSISTANT"),
    ("consent", False), ("known_self", True), ("verified_delivery", False)])
def test_unqualified_labels_never_train_preferences(field, value):
    rows = observations()
    for row in rows:
        row[field] = value
    result = fit(rows, DEFAULT_PROFILE["weights"], now=NOW)
    assert result["state"] == "INSUFFICIENT_DATA" and result["support"]["human_samples"] == 0


def test_withdrawn_latest_label_cannot_resurrect_older_label():
    rows = observations()
    rows += [{**row, "withdrawn": True, "label_at": NOW, "reference": "withdraw-" + row["reference"]} for row in rows]
    assert fit(rows, DEFAULT_PROFILE["weights"], now=NOW)["support"]["human_samples"] == 0


def test_apply_rechecks_withdrawal_and_owner_changes_and_preserves_hard_policy():
    class Source:
        rows = observations()
        def preference_observations(self, *_):
            return self.rows
    source = Source()
    service = SelectionService(LocalStore(), source, now=lambda: NOW)
    original = service.update_profile("balanced", {"required": {"min_credit": .5},
        "budgets": {"CNY": {"comfortable": 10, "maximum": 20}}}, expected_revision=0)
    candidate = service.learn("balanced")
    source.rows = copy.deepcopy(source.rows)
    source.rows[0]["withdrawn"] = True
    with pytest.raises(ValueError, match="HISTORY_CHANGED"):
        service.apply_learning(candidate["candidate_id"], original["revision"])
    source.rows = observations()
    applied = service.apply_learning(candidate["candidate_id"], original["revision"])
    assert applied["values"]["required"] == original["values"]["required"]
    assert applied["values"]["budgets"] == original["values"]["budgets"]
    with pytest.raises(ValueError, match="REV_CONFLICT"):
        service.apply_learning(candidate["candidate_id"], original["revision"])


def test_adapter_uses_the_actual_trade_index_and_only_owner_verified_delivery():
    from a2n_sdk.selection_adapter import SelectionFactAdapter
    from a2n_sdk.selection.contracts import DIMENSIONS
    from a2n_sdk.trade_facts import digest as trade_digest
    adapter = SelectionFactAdapter.__new__(SelectionFactAdapter)
    adapter.store, adapter.node_did = LocalStore(), "buyer"
    store = adapter.store
    store.put("trade_fact_index", trade_digest(["scope", "task"]), "trade")
    fact = {"buyer_did": "buyer", "relation_verified": True, "execution": "DELIVERED"}
    store.put("trade_facts", "trade", fact)
    store.put("task_reviews", "review", {"trade_uid": "trade", "provider_did": "provider",
        "record_digest": "record", "planned": True, "at": 110, "label_at": 120,
        "origin": "PRODUCTION", "known_self": False, "consent": True, "withdrawn": False,
        "label_source": "HUMAN", "human": {"usefulness": 5}})
    store.put("selection_snapshots", "snapshot", {"profile_id": "balanced", "profile_revision": 0,
        "items": [{"key": "candidate", "dimensions": {k: {"value": None, "status": "UNKNOWN"} for k in DIMENSIONS}}]})
    store.put("selection_choices", "choice", {"scope": "scope", "task_id": "task",
        "snapshot_id": "snapshot", "candidate_key": "candidate", "chosen_at": 100})
    rows = adapter.preference_observations("balanced", 0)
    assert len(rows) == 1 and rows[0]["label"] == 1
    assert all(v == .5 for v in rows[0]["features"].values())
    assert adapter.preference_observations("balanced", 1) == []
    store.put("trade_facts", "trade", {**fact, "buyer_did": "other-owner"})
    assert adapter.preference_observations("balanced", 0) == []
