import pytest

from a2n_sdk.payments import PaymentBook, RiskBook
from a2n_sdk.storage import LocalStore


class Driver:
    """Deterministic adapter fixture, never a claim of actual funds testing."""
    def __init__(self):
        self.submits, self.queries = [], []
        self.response = None

    def capabilities(self):
        return {"driver_id": "fixture", "currencies": ["CNY"], "refund": False}

    def submit(self, intent):
        self.submits.append(intent)
        raise TimeoutError("external state unknown")

    def query(self, intent):
        self.queries.append(intent)
        return self.response or {"state": "UNKNOWN"}


def configured(store=None, driver=None, cap=10):
    store = store or LocalStore()
    risk = RiskBook(store)
    if not risk.policy()["revision"]:
        risk.configure({"CNY": {"per_trade": cap, "total_exposure": cap, "daily_spend": cap,
                               "per_counterparty": cap}}, expected_revision=0)
    return PaymentBook(store, risk, driver or Driver()), risk


def create(book, trade="one", amount=5):
    return book.create(trade_uid=trade, currency="CNY", amount_minor=amount,
                       payee="merchant", counterparty_did="seller", command_id=trade)


def test_unconfigured_driver_and_missing_owner_budget_cannot_create_payment():
    store = LocalStore()
    book = PaymentBook(store, RiskBook(store))
    assert not book.capabilities()["available"]
    with pytest.raises(ValueError, match="PAYMENT_UNAVAILABLE"):
        create(book)
    assert not store.items("payment_intents") and not store.items("risk_reservations")
    with pytest.raises(ValueError, match="RISK_LIMIT"):
        create(PaymentBook(store, RiskBook(store), Driver()))


def test_timeout_holds_exposure_and_never_resubmits_even_after_restart():
    driver, store = Driver(), LocalStore()
    book, risk = configured(store, driver)
    row = create(book)
    assert book.submit(row["intent_id"])["state"] == "UNKNOWN"
    restarted = PaymentBook(store, risk, driver)
    assert restarted.submit(row["intent_id"])["state"] == "UNKNOWN"
    assert len(driver.submits) == 1
    assert store.get("risk_reservations", row["intent_id"])["state"] == "ACTIVE"
    with pytest.raises(ValueError, match="RISK_LIMIT"):
        create(restarted, "another", 6)
    assert len(driver.submits) == 1


def test_confirmed_reconciliation_requires_exact_amount_currency_payee_and_source():
    book, risk = configured()
    row = create(book)
    book.submit(row["intent_id"])
    book.driver.response = {"state": "CONFIRMED", "driver_id": "fixture", "verified_source": "adapter/query",
        "reference": "channel-tx", "currency": "CNY", "amount_minor": 6, "payee": "merchant"}
    assert book.reconcile(row["intent_id"])["state"] == "UNKNOWN"
    book.driver.response["amount_minor"] = 5
    assert book.reconcile(row["intent_id"])["state"] == "CONFIRMED"
    assert risk.store.get("risk_reservations", row["intent_id"])["state"] == "CONSUMED"
    assert book.driver.queries[-1]["intent_id"] == row["intent_id"]
    assert len(book.driver.submits) == 1
    assert book.reconcile(row["intent_id"])["state"] == "CONFIRMED"
    assert len(book.driver.queries) == 2


def test_definitive_failure_releases_but_unverified_failure_does_not():
    book, risk = configured()
    row = create(book)
    book.submit(row["intent_id"])
    book.driver.response = {"state": "FAILED", "definitive": True}
    assert book.reconcile(row["intent_id"])["state"] == "UNKNOWN"
    book.driver.response.update(driver_id="fixture", verified_source="adapter/query")
    assert book.reconcile(row["intent_id"])["state"] == "FAILED"
    assert risk.store.get("risk_reservations", row["intent_id"])["state"] == "RELEASED"
    create(book, "next", 10)


def test_submitting_crash_becomes_unknown_and_business_terms_are_immutable():
    book, risk = configured()
    row = create(book)
    book.store.put("payment_intents", row["intent_id"], {**row, "state": "SUBMITTING"})
    restarted = PaymentBook(book.store, risk, book.driver)
    assert restarted.get(row["intent_id"])["state"] == "UNKNOWN"
    assert not restarted.driver.submits
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT|PAYMENT_INTENT_CONFLICT"):
        create(restarted, amount=6)


def test_day_change_does_not_release_unknown_exposure_and_clock_rollback_does_not_reset_day():
    store, clock = LocalStore(), [10 * 86400]
    risk = RiskBook(store, now=lambda: clock[0])
    risk.configure({"CNY": {"per_trade": 10, "total_exposure": 10, "daily_spend": 10,
                           "per_counterparty": 10}}, expected_revision=0)
    risk.reserve("first", currency="CNY", amount=10, counterparty="seller")
    clock[0] += 86400
    with pytest.raises(ValueError, match="RISK_LIMIT"):
        risk.reserve("next", currency="CNY", amount=1, counterparty="new")
    risk.finish("first", confirmed=True)
    current = risk.reserve("next", currency="CNY", amount=10, counterparty="new")
    risk.finish("next", confirmed=True)
    clock[0] -= 86400
    with pytest.raises(ValueError, match="RISK_LIMIT"):
        risk.reserve("rollback", currency="CNY", amount=1, counterparty="new")
    assert current["period"] == 11
