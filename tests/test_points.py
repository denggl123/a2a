"""Ledger invariants, owner consent and durable whole-transaction decisions."""
import copy

import pytest
from a2n_p2p import Identity
from a2n_node.feedback_identity import signer_for, verifier_for
from a2n_sdk.experience import signed, unsigned
from a2n_sdk.points import PointsBook, units
from a2n_sdk.points_coordination import PointsCoordinator, find_routes
from a2n_sdk.storage import LocalStore
from a2n_sdk.trade_facts import digest


def books(count=3):
    identities = [Identity.generate() for _ in range(count)]
    stores = [LocalStore() for _ in identities]
    return identities, [PointsBook(s, node_did=i.did, signer=signer_for(i), verifier=verifier_for()) for s, i in zip(stores, identities)]


def accept(book, issuer, numerator=1, denominator=1, limit=1000):
    return book.set_acceptance(issuer, {"enabled": True, "numerator": numerator, "denominator": denominator,
        "per_trade": limit, "daily": limit, "max_holding": limit}, expected_revision=0)


def coordinator(identity, book):
    return PointsCoordinator(book.store, node_did=identity.did, signer=signer_for(identity), verifier=verifier_for())


def prepare(c, ledgers, ops, command="one", **kwargs):
    routes = {b.node_did: "https://example.test/" + str(i) for i, b in enumerate(ledgers)}
    row = c.create(operations=ops, routes=routes, command_id=command, **kwargs)
    plan, key = row["record"], row["record"]["plan_id"]
    for b in ledgers:
        c.remember(key, "consents", b.node_did, b.consent(plan))
    for b in ledgers:
        c.remember(key, "prepared", b.node_did, b.prepare(plan, c.get(key)["consents"]))
    return c.get(key)


def test_grant_has_explicit_issuer_holder_and_idempotent_issue_or_transfer():
    ids, (a, b) = books(2)
    receipt = a.grant(a.node_did, 30, command_id="seed")
    assert a.grant(a.node_did, 30, command_id="seed") == receipt
    moved = a.grant(b.node_did, 12, command_id="give", mode="TRANSFER")
    assert moved["issuer_did"] == a.node_did and moved["holder_did"] == b.node_did
    assert a.balance(a.node_did)["amount"] == 18 and a.balance(b.node_did)["amount"] == 12
    assert b.balance(b.node_did)["amount"] == 0
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        a.grant(b.node_did, 13, command_id="give", mode="TRANSFER")
    a.configure({"issuance_enabled": False, "allow_http": False})
    with pytest.raises(ValueError, match="ISSUANCE_STOPPED"):
        a.grant(b.node_did, 1, command_id="new")
    a.grant(b.node_did, 1, command_id="move", mode="TRANSFER")


def ring():
    ids, ledgers = books()
    for i, b in enumerate(ledgers):
        b.grant(b.node_did, 20, command_id="seed")
        accept(b, ledgers[(i - 1) % 3].node_did)
    ops = [{"kind": "TRANSFER", "issuer_did": b.node_did, "from_did": b.node_did,
            "to_did": ledgers[(i + 1) % 3].node_did, "amount": 5} for i, b in enumerate(ledgers)]
    return ids, ledgers, ops


def test_three_party_commit_and_recovery_retains_original_decision_and_never_redebits():
    ids, ledgers, ops = ring()
    c = coordinator(ids[0], ledgers[0])
    row = prepare(c, ledgers, ops)
    key, plan = row["record"]["plan_id"], row["record"]
    assert all(b.balance(b.node_did)["available"] == 15 for b in ledgers)
    decided = c.decide(key, "COMMIT")
    # Last node temporarily offline, first two have applied the same whole decision.
    for b in ledgers[:2]:
        b.apply(plan, decided["decision"])
    assert ledgers[2].balance(ledgers[2].node_did)["amount"] == 20
    restarted = PointsBook(ledgers[2].store, node_did=ids[2].did, signer=signer_for(ids[2]), verifier=verifier_for())
    receipt = restarted.apply(plan, decided["decision"])
    assert restarted.apply(plan, decided["decision"]) == receipt
    assert restarted.balance(restarted.node_did)["amount"] == 15
    assert all(b.balance(ledgers[(i + 1) % 3].node_did)["amount"] == 5 for i, b in enumerate(ledgers))
    with pytest.raises(ValueError, match="DECISION_CONFLICT"):
        c.decide(key, "ABORT")


def test_abort_releases_every_reservation_and_never_transfers_balance():
    ids, ledgers, ops = ring()
    c = coordinator(ids[0], ledgers[0]); row = prepare(c, ledgers, ops)
    decision = c.decide(row["record"]["plan_id"], "ABORT")["decision"]
    for b in ledgers:
        b.apply(row["record"], decision)
        assert b.balance(b.node_did)["available"] == 20
    with pytest.raises(ValueError, match="TRANSACTION_ABORTED"):
        ledgers[0].prepare(row["record"], row["consents"])


def test_unaccepted_issuer_and_uncompensated_third_party_debit_are_rejected():
    ids, ledgers, ops = ring()
    accept(ledgers[1], ledgers[2].node_did)
    bad = [{"kind": "TRANSFER", "issuer_did": ledgers[1].node_did,
            "from_did": ledgers[1].node_did, "to_did": ledgers[0].node_did, "amount": 10}]
    c = coordinator(ids[0], ledgers[0])
    row = c.create(operations=bad, routes={ids[0].did: "https://a", ids[1].did: "https://b"}, command_id="evil")
    with pytest.raises(ValueError, match="OWNER_AUTHORIZATION"):
        ledgers[1].consent(row["record"])


def test_concurrent_reservations_prevent_double_spend_and_limits_change_does_not_change_prepared_terms():
    ids, ledgers, ops = ring()
    c = coordinator(ids[0], ledgers[0]); first = prepare(c, ledgers, [{**o, "amount": 15} for o in ops])
    second = c.create(operations=[{**o, "amount": 10} for o in ops],
        routes=first["record"]["routes"], command_id="two")["record"]
    consents = {b.node_did: b.consent(second) for b in ledgers}
    with pytest.raises(ValueError, match="INSUFFICIENT_BALANCE"):
        ledgers[0].prepare(second, consents)
    rule = ledgers[1].acceptance(ledgers[0].node_did)
    ledgers[1].set_acceptance(ledgers[0].node_did, {k: v for k, v in rule.items() if k not in {"issuer_did", "revision"}} | {"enabled": False}, expected_revision=1)
    decision = c.decide(first["record"]["plan_id"], "COMMIT")["decision"]
    assert ledgers[1].apply(first["record"], decision)["state"] == "COMMITTED"


def test_routes_use_integer_ceiling_and_minimum_cost_in_one_selected_issuer():
    catalogs = {
        "seller": {"acceptance": [{"issuer_did": "mid", "enabled": True, "numerator": 3, "denominator": 2, "per_trade": 100}]},
        "mid": {"exchange_available": 50, "acceptance": [{"issuer_did": "fund", "enabled": True, "numerator": 2, "denominator": 3, "per_trade": 100}]}}
    paths = find_routes(buyer_did="buyer", funding_issuer="fund", balance=20, provider_did="seller", price=10, catalogs=catalogs)
    assert paths[0]["cost"] == 11 and [o["amount"] for o in paths[0]["operations"]] == [11, 7]
    assert not find_routes(buyer_did="buyer", funding_issuer="fund", balance=10, provider_did="seller", price=10, catalogs=catalogs)


@pytest.mark.parametrize("value", [True, -1, 1.5, "1e3", "１２", str(10**18 + 1)])
def test_invalid_units(value):
    with pytest.raises(ValueError, match="UNITS"):
        units(value)
