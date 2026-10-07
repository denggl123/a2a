import copy
import pytest
from a2n_p2p import Identity
from a2n_node.feedback_identity import signer_for, verifier_for
from a2n_node.evm_payment import EvmNativeDriver, wallet_binding, verify_wallet
from a2n_node.x402.evm import EvmSigner
from a2n_sdk.payments import PaymentBook, RiskBook
from a2n_sdk.payment_coordination import PaymentCoordinator
from a2n_sdk.storage import LocalStore
from a2n_sdk.ports import CallRequest
from .test_payments import Driver, configured, create


def test_multiple_drivers_keep_unknown_order_locked_and_require_explicit_new_attempt():
    book, _ = configured(cap=20)
    first = create(book)
    second = Driver(); second.capabilities = lambda: {"driver_id": "second", "currencies": ["CNY"]}
    book.register(second)
    book.submit(first["intent_id"])
    with pytest.raises(ValueError, match="PAYMENT_INTENT_CONFLICT"):
        book.create(trade_uid="one", currency="CNY", amount_minor=5, payee="merchant", counterparty_did="seller",
            command_id="switch", driver_id="second")
    with pytest.raises(ValueError, match="PAYMENT_ALREADY_EXPOSED"):
        book.cancel(first["intent_id"])
    book.driver.response = {"state":"FAILED", "driver_id":"fixture", "verified_source":True, "definitive":True}
    book.reconcile(first["intent_id"])
    new = book.create(trade_uid="one", currency="CNY", amount_minor=5, payee="merchant", counterparty_did="seller", command_id="switch", driver_id="second")
    assert new["intent_id"] != first["intent_id"] and len(book.order("one")["attempts"]) == 2
    assert book.get(first["intent_id"])["state"] == "FAILED"


def test_two_signed_parties_fix_terms_and_detect_tampering():
    buyer, seller = Identity.generate(), Identity.generate()
    book, _ = configured(cap=20)
    b = PaymentCoordinator(book.store, book, node_did=buyer.did, signer=signer_for(buyer), verifier=verifier_for())
    s = PaymentCoordinator(LocalStore(), book, node_did=seller.did, signer=signer_for(seller), verifier=verifier_for())
    method = {"method":"fixture", "network":"test", "asset":"native", "currency":"CNY", "flow":"upfront"}
    key = EvmSigner(bytes.fromhex("01"*32))
    cap = b.capabilities([method], wallet_binding(key, buyer.did))
    query = b.query(CallRequest(task_id="one"), provider_did=seller.did, service_id="service", capabilities=cap)
    option = {**method,"amount_minor":5,"payee":"merchant","platform_commission_minor":0}
    offer = s.offer(query, source_card={"version":"1"}, options=[option])
    plan = b.plan(offer, option_index=0, fee_cap_minor=2, driver_id="fixture")
    acceptance = s.accept(plan)
    intent = b.activate(plan["plan_id"], acceptance, command_id="approved")
    assert intent["amount_minor"] == 5 and book.risk.store.get("risk_reservations",intent["intent_id"])["amount_minor"] == 7
    assert s.accept(plan) == acceptance
    tampered = copy.deepcopy(plan); tampered["terms"]["payee"] = "evil"
    with pytest.raises(ValueError, match="SIGNATURE"):
        s.accept(tampered)
    verify_wallet(cap["wallet_binding"], buyer.did)
    with pytest.raises(ValueError, match="WALLET_BINDING"):
        verify_wallet(cap["wallet_binding"], seller.did)


def test_actual_evm_transaction_lost_response_reconcile_and_request_binding():
    pytest.importorskip("eth_tester")
    from .payment_evm_support import TestEVM
    evm = TestEVM()
    store = LocalStore()
    cfg = {"currency":"ETH","network":"eip155:"+str(evm.chain_id),"rpc_url":"http://127.0.0.1:1","allow_http":True,"confirmations":3}
    signer = EvmSigner(evm.keys[0].to_bytes())
    driver = EvmNativeDriver(store,cfg,signer,rpc=evm)
    risk = RiskBook(store)
    risk.configure({"ETH":dict(per_trade=10**17,total_exposure=10**17,daily_spend=10**17,per_counterparty=10**17)},expected_revision=0)
    book = PaymentBook(store,risk,driver)
    payee = evm.chain.get_accounts()[1]
    before = evm.chain.get_balance(payee)
    intent = book.create(trade_uid="real",currency="ETH",amount_minor=12345,payee=payee,counterparty_did="seller",command_id="actual",
        plan_id="approved-plan",fee_cap_minor=10**15)
    evm.drop_response = True
    first = book.submit(intent["intent_id"])
    assert first["state"] == "UNKNOWN" and evm.broadcasts == 1
    assert evm.chain.get_balance(payee) == before+12345
    evm.chain.mine_blocks(2)
    restarted = PaymentBook(store,risk,EvmNativeDriver(store,cfg,signer,rpc=evm))
    assert restarted.reconcile(intent["intent_id"])["state"] == "CONFIRMED"
    restarted.submit(intent["intent_id"])
    assert evm.broadcasts == 1
    result = driver.observe(reference=first["reference"],payer=signer.address,payee=payee,amount_minor=12345,plan_id="different-trade")
    assert result["state"] == "UNKNOWN"


def test_never_exposed_transfer_fails_safely_and_uses_no_nonce():
    pytest.importorskip("eth_tester")
    from .payment_evm_support import TestEVM
    evm, store = TestEVM(), LocalStore()
    signer = EvmSigner(evm.keys[0].to_bytes())
    d = EvmNativeDriver(store, {"currency":"ETH","network":"eip155:"+str(evm.chain_id),"rpc_url":"http://127.0.0.1:1","allow_http":True}, signer, rpc=evm)
    intent={"intent_id":"never","currency":"ETH","driver_id":d.driver_id,"amount_minor":1,"payee":evm.chain.get_accounts()[1],"fee_cap_minor":1}
    assert d.submit(intent)["state"] == "FAILED"
    assert d.query(intent)["definitive"] and not evm.broadcasts and not store.items("evm_next_nonce")
