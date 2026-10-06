import pytest

from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook
from a2n_sdk.reconnect import ReconnectBook


def setup():
    store, clock = LocalStore(), [100000.0]
    trials = TrialBook(store)
    book = ReconnectBook(store, trials, node_did="owner", now=lambda: clock[0])
    for i in range(10):
        trials.admit("svc", str(i))
        done = {"ok": True, "state": "COMPLETED", "result": "genuine"}
        trials.finish_admission("svc", str(i), done)
        trials.record("svc", str(i), done)
    return store, trials, book, clock


def consume(trials, tid, state="COMPLETED"):
    row = trials.admit("svc", tid)
    outcome = {"ok": state == "COMPLETED", "state": state, "result": "new", "metadata": {}}
    trials.finish_admission("svc", tid, outcome)
    sample = trials.record("svc", tid, outcome)
    return row, sample


def test_optional_observed_reconnect_grants_never_reset_initial_samples_or_stack_after_restart():
    store, trials, book, clock = setup()
    assert not book.note_verified_connection("peer", ["svc"])
    clock[0] += 86401
    assert not book.note_verified_connection("peer", ["svc"])
    book.configure(enabled=True, expected_revision=0)
    clock[0] += 86401
    grant = book.note_verified_connection("peer", ["svc"])[0]
    assert grant["size"] == 5
    initial = trials.samples("svc")
    row, sample = consume(trials, "r0")
    assert row["free_reason"] == "FREE_RECONNECT" and sample["sample_group"] == "RECONNECT" and sample["slot"] is None
    assert trials.samples("svc")[:10] == initial
    book = ReconnectBook(store, trials, node_did="owner", now=lambda: clock[0])
    clock[0] += 86401
    assert not book.note_verified_connection("peer", ["svc"])  # unspent grant remains
    assert trials.status("svc")["used"] == 10 and trials.status("svc")["reconnect"]["completed"] == 1
    book.configure(enabled=False, expected_revision=1)
    assert not trials.admit("svc", "disabled")["free"]
    book.configure(enabled=True, expected_revision=2)
    assert book.available("svc")["grant_id"] == grant["grant_id"]


def test_unknown_keeps_reservation_failure_releases_and_lifetime_cap_cannot_be_reset_by_version():
    store, trials, book, clock = setup()
    book.configure(enabled=True, expected_revision=0)
    book.note_verified_connection("peer", ["svc"])
    clock[0] += 86401
    book.note_verified_connection("peer", ["svc"])
    consume(trials, "unknown", "DELIVERY_UNKNOWN")
    assert book.summary("svc")["reserved"] == 1
    consume(trials, "failed", "FAILED")
    assert book.summary("svc")["completed"] == 0 and book.summary("svc")["reserved"] == 1
    clock[0] += 86401
    assert not book.note_verified_connection("peer", ["svc"])
    trials.finish_admission("svc", "unknown", {"ok": False, "state": "FAILED"})
    for i in range(20):
        if i and i % 5 == 0:
            clock[0] += 86401
            assert book.note_verified_connection("peer", ["svc"])
        row, _ = consume(trials, f"completed-{i}")
        assert row["free_reason"] == "FREE_RECONNECT"
    clock[0] += 86401
    assert not book.note_verified_connection("peer", ["svc"])
    assert book.summary("svc")["completed"] == 20 and not trials.admit("svc", "over-cap")["free"]


def test_initial_boundary_overflow_has_separate_sample_group_and_fixed_original_slots():
    trials = TrialBook(LocalStore(), cap=1)
    assert trials.admit("svc", "a")["free"] and trials.admit("svc", "b")["free"]
    for tid in ("a", "b"):
        done = {"ok": True, "state": "COMPLETED", "result": tid}
        trials.finish_admission("svc", tid, done)
        trials.record("svc", tid, done)
    samples = trials.samples("svc")
    assert [s["sample_group"] for s in samples] == ["INITIAL", "INITIAL_OVERFLOW"]
    assert [s["slot"] for s in samples] == [1, None]
