"""Actual signed HTTP node calls, mandatory public samples and points settlement."""
import base64
import os

import pytest
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.ports import CallRequest


@pytest.fixture
def point_market(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    nodes = [Daemon(tmp_path / str(i), port=0, protector=protector, beacon=False,
                    coord_allow_networks=["127.0.0.0/8"]).start() for i in range(3)]
    try:
        for node in nodes:
            node.management.discovery_public_base = node.runtime.local_base_url
            node.points_node.book.configure({"issuance_enabled": True, "allow_http": True})
        for node in nodes:
            for peer in nodes:
                if peer is not node:
                    node.points_node.add_peer(peer.runtime.local_base_url)
        provider, buyer, middle = nodes
        executed = []
        card = {"name": "积分真实计算", "version": "1", "skills": [{"id": "add", "name": "add"}]}
        provider.runtime.mount_callable(card, lambda p: executed.append(p) or {"sum": p["a"] + p["b"]}, service_id="point-add")
        provider.points_node.book.set_service("point-add", {"enabled": True, "amount": 5, "modes": ["EARN", "DEBT", "PAY"], "debt_limit": 20}, expected_revision=0)
        buyer.runtime.import_agent(provider.runtime.project_binding("point-add"), projection_id="use")
        yield provider, buyer, middle, executed
    finally:
        for node in reversed(nodes):
            node.stop()


def graduate(provider, buyer):
    for i in range(10):
        offer = buyer.payment_coordination.quote({"projection_id": "use", "request": {"task_id": "sample-" + str(i), "skill": "add", "payload": {"a": i, "b": 2}}})
        assert offer["payment_state"] == "NOT_REQUIRED"
        out = buyer.payment_coordination.free_execute(offer["offer_id"])
        assert out["ok"] and out["settlement"]["state"] == "NOT_REQUIRED"
    assert len(provider.trials.samples("point-add")) == 10


def planned(buyer, task, mode, **body):
    offer = buyer.payment_coordination.quote({"projection_id": "use", "request": {"task_id": task, "skill": "add", "payload": {"a": 3, "b": 4}}})
    index = next(i for i, o in enumerate(offer["options"]) if o.get("mode") == mode)
    row = buyer.payment_coordination.prepare({"offer_id": offer["offer_id"], "option_index": index, "fee_cap_minor": 0, "command_id": task, **body})
    return row["record"]["plan_id"]


def test_first_ten_bypass_all_payment_orders_then_supplier_earns_own_points(point_market):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    for node in (provider, buyer):
        assert not node.store.items("points_transactions") and not node.store.items("points_journal")
        assert not node.store.items("payment_orders") and not node.store.items("payment_offers")
    key = planned(buyer, "earned", "EARN")
    out = buyer.payment_coordination.execute(key)
    assert out["payment"]["state"] == "CONFIRMED", out
    assert out["delivery"]["result"] == {"sum": 7}
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    assert provider.points_node.book.balance(buyer.identity.did)["amount"] == 0
    assert provider.points_node.book.debt(buyer.identity.did) == 0
    buyer.payment_coordination.execute(key)
    assert len(executed) == 11 and provider.points_node.book.balance(provider.identity.did)["amount"] == 5


def test_finished_free_calls_release_offer_capacity_but_unexecuted_quotes_are_bounded(point_market):
    provider, buyer, _, executed = point_market
    card = {"name": "持续免费服务", "version": "1", "skills": [{"id": "add"}], "x-a2n": {
        "price_book": {"add": {"CNY": {"dimensions": [{"key": "call_count", "amount": 0, "per": 1}]}}}}}
    provider.runtime.mount_callable(card, lambda p: executed.append(p) or {"sum": p["a"] + p["b"]}, service_id="point-free")
    buyer.runtime.import_agent(provider.runtime.project_binding("point-free"), projection_id="free-use")
    for i in range(20):
        offer = buyer.trades.quote({"projection_id": "free-use", "request": {"task_id": "free-" + str(i), "skill": "add", "payload": {"a": i, "b": 1}}})
        assert buyer.trades.free_execute(offer["offer_id"])["ok"]
    assert len(executed) == 20 and len(provider.trials.samples("point-free")) == 10
    for i in range(16):
        buyer.trades.quote({"projection_id": "free-use", "request": {"task_id": "pending-" + str(i), "skill": "add", "payload": {"a": i, "b": 1}}})
    with pytest.raises(ValueError, match="HTTP_SERVICE_ERROR"):
        buyer.trades.quote({"projection_id": "free-use", "request": {"task_id": "too-many", "skill": "add", "payload": {"a": 1, "b": 1}}})
    assert len(executed) == 20 and not provider.store.items("payment_orders")


def test_debt_then_manual_grant_and_repayment_never_reexecutes_service(point_market):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    out = buyer.payment_coordination.execute(planned(buyer, "debt", "DEBT"))
    assert out["payment"]["state"] == "CONFIRMED", out
    assert provider.points_node.book.debt(buyer.identity.did) == 5
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    provider.points_node.book.grant(buyer.identity.did, 5, command_id="recharge")
    row = buyer.points.repay({"issuer_did": provider.identity.did, "amount": 5, "command_id": "repay"})
    assert row["state"] == "CONFIRMED"
    assert provider.points_node.book.debt(buyer.identity.did) == 0 and len(executed) == 11


def test_paid_existing_points_transfer_only_and_three_node_exchange(point_market):
    provider, buyer, middle, executed = point_market
    graduate(provider, buyer)
    buyer.points_node.book.grant(buyer.identity.did, 20, command_id="fund")
    middle.points_node.book.grant(middle.identity.did, 20, command_id="fund")
    from .test_points import accept
    accept(middle.points_node.book, buyer.identity.did)
    accept(provider.points_node.book, middle.identity.did)
    key = planned(buyer, "exchange", "PAY", funding_issuer=buyer.identity.did, max_cost=5)
    row = buyer.points.get(key)
    assert len(row["record"]["operations"]) == 2
    out = buyer.payment_coordination.execute(key)
    assert out["payment"]["state"] == "CONFIRMED", out
    assert buyer.points_node.book.balance(buyer.identity.did)["amount"] == 15
    assert buyer.points_node.book.balance(middle.identity.did)["amount"] == 5
    assert middle.points_node.book.balance(middle.identity.did)["amount"] == 15
    assert middle.points_node.book.balance(provider.identity.did)["amount"] == 5
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 0
    assert len(executed) == 11


def test_lost_commit_receipt_recovers_same_transaction_without_reexecution(point_market, monkeypatch):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    key = planned(buyer, "lost-receipt", "EARN")
    original = buyer.points_node.request
    lost = []
    def request(target, action, payload, **kwargs):
        result = original(target, action, payload, **kwargs)
        if target == provider.identity.did and action == "apply" and not lost:
            lost.append(True)
            raise TimeoutError("response lost after issuer applied")
        return result
    monkeypatch.setattr(buyer.points_node, "request", request)
    out = buyer.payment_coordination.execute(key)
    assert out["order_state"] == "COMMITTING" and provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    decision = buyer.points.get(key)["decision"]
    # Reconstruct the coordinator as a restart would, retaining the durable store.
    from a2n_node.points_payment import PointsPayment
    buyer.points = PointsPayment(buyer.points_node)
    out = buyer.trades.execute_points(key, reconcile=True)
    assert out["payment"]["state"] == "CONFIRMED"
    assert buyer.points.get(key)["decision"] == decision and len(executed) == 11
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    assert buyer.trade_facts.get(buyer.points.get(key)["record"]["offer"]["trade_uid"])["payment"] == "CONFIRMED"


def test_lost_service_result_recovers_private_original_delivery_without_reinvoking(point_market, monkeypatch):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    key = planned(buyer, "lost-delivery", "EARN")
    original = buyer.calls._invoke
    def lose(scope, request):
        original(scope, request)
        from a2n_sdk.ports import CallOutcome
        return CallOutcome(False, request.task_id, "DELIVERY_UNKNOWN", error="lost service response")
    monkeypatch.setattr(buyer.calls, "_invoke", lose)
    out = buyer.payment_coordination.execute(key)
    assert out["payment"]["state"] == "UNKNOWN" and len(executed) == 11
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 0
    out = buyer.trades.execute_points(key, reconcile=True)
    assert out["payment"]["state"] == "CONFIRMED", out
    assert out["delivery"]["result"] == {"sum": 7} and len(executed) == 11
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5


def test_unknown_request_not_admitted_is_sealed_before_releasing_reservations(point_market, monkeypatch):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    key = planned(buyer, "never-arrived", "EARN")
    from a2n_sdk.ports import CallOutcome
    monkeypatch.setattr(buyer.calls, "_invoke", lambda scope, request: CallOutcome(False, request.task_id, "DELIVERY_UNKNOWN"))
    buyer.payment_coordination.execute(key)
    out = buyer.trades.execute_points(key, reconcile=True)
    assert out["payment"]["state"] == "FAILED", out
    assert provider.store.get("business_points_canceled", key) is True
    assert len(executed) == 10 and provider.points_node.book.balance(provider.identity.did)["amount"] == 0


def test_bilateral_refund_transfers_original_received_points_once(point_market):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    provider.points_node.book.grant(buyer.identity.did, 10, command_id="purchase")
    key = planned(buyer, "refund-service", "PAY")
    assert buyer.payment_coordination.execute(key)["payment"]["state"] == "CONFIRMED"
    dispute = buyer.resolutions.open("use", "refund-service", "requester", "双方约定退款")
    proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "REFUND", "text": "退原积分",
        "amount_minor": 5, "currency": "points:" + provider.identity.did}, command_id="proposal")
    provider.resolutions.receive(proposal)
    accepted_buyer = buyer.resolutions.accept(dispute["id"], proposal["message_id"], command_id="buyer-accept")
    provider.resolutions.receive(accepted_buyer)
    accepted_provider = provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="provider-accept")
    buyer.resolutions.receive(accepted_provider)
    first = provider.trades.refund_points({"proposal_id": proposal["message_id"]})
    assert first["state"] == "CONFIRMED", first
    second = provider.trades.refund_points({"proposal_id": proposal["message_id"]}, reconcile=True)
    assert second["reference"] == first["reference"]
    assert provider.points_node.book.balance(buyer.identity.did)["amount"] == 10
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 0 and len(executed) == 11
