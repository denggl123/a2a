"""Real sockets, outbound-only providers and encrypted payment coordination."""
import base64
import copy
import os
import time

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.settlement_transport import MAILBOX_PREFIX, VERSION, verify_lease
from a2n_sdk.ports import CallRequest


def wait(fn, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("outbound mailbox/relay did not become ready")


@pytest.fixture
def nat_market(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / "root", port=0, protector=protector, beacon=False, coord_allow_networks=["127.0.0.0/8"]).start()
    nodes = []
    try:
        root.management.discovery_public_base = root.runtime.local_base_url
        root.management.public_services["task_relay"] = True
        for i in range(3):
            # This advertised address has no listener. All requests to these
            # nodes must reach an outbound worker through the root mailbox.
            node = Daemon(tmp_path / str(i), port=0, protector=protector, beacon=False,
                discovery_public_base=f"http://127.0.0.1:{19991+i}", public_nodes=[root.runtime.local_base_url],
                relay_node=root.runtime.local_base_url, coord_mailbox_nodes=[root.runtime.local_base_url],
                coord_allow_networks=["127.0.0.0/8"]).start()
            nodes.append(node)
            node.points_node.book.configure({"issuance_enabled": True, "allow_http": True})
        provider, buyer, middle = nodes
        for node in nodes:
            wait(lambda n=node: n.settlement_mailbox.lease)
        executed = []
        provider.runtime.mount_callable({"name": "真实文本处理", "version": "1", "skills": [{"id": "inspect"}]},
            lambda p: executed.append(p) or {"length": len(p["text"]), "text": p["text"]}, service_id="text")
        provider.points_node.book.set_service("text", {"enabled": True, "amount": 5, "modes": ["EARN", "DEBT", "PAY"], "debt_limit": 20}, expected_revision=0)
        provider.relay_provider.request_refresh()
        wait(lambda: provider.relay_provider.registered == 1)
        card = provider.relay_provider._cards()[0]
        buyer.runtime.import_agent(card, projection_id="use")
        yield root, provider, buyer, middle, executed
    finally:
        for node in reversed(nodes):
            node.stop()
        root.stop()


def graduate(provider, buyer):
    for i in range(10):
        offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": "sample-" + str(i), "skill": "inspect", "payload": {"text": "公开样品"}}})
        assert offer["payment_state"] == "NOT_REQUIRED"
        assert buyer.trades.free_execute(offer["offer_id"])["ok"]
    assert len(provider.trials.samples("text")) == 10


def plan(buyer, task, mode):
    offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": task, "skill": "inspect", "payload": {"text": "private after sample"}}})
    index = next(i for i, option in enumerate(offer["options"]) if option.get("mode") == mode)
    return buyer.payment_coordination.prepare({"offer_id": offer["offer_id"], "option_index": index, "command_id": task, "fee_cap_minor": 0})


def test_points_service_prepare_commit_and_private_recovery_through_outbound_mailbox(nat_market):
    root, provider, buyer, _, executed = nat_market
    captured = []
    original = root.public_settlement_mailbox._forward
    def inspect(author, body):
        captured.append(copy.deepcopy(body))
        return original(author, body)
    root.public_settlement_mailbox._forward = inspect
    graduate(provider, buyer)
    assert not buyer.store.items("points_orders") and not provider.store.items("points_transactions")
    row = plan(buyer, "earned", "EARN")
    key = row["record"]["plan_id"]
    assert row["state"] == "PREPARED", row
    assert all(isinstance(route, dict) for route in row["record"]["routes"].values())
    assert buyer.trades.execute(key)["payment"]["state"] == "CONFIRMED"
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    buyer.trades.execute(key)
    assert len(executed) == 11
    assert "private after sample" not in str(captured)
    assert not root.store.items("points_transactions") and not root.store.items("points_journal")
    # Lose the delivery proof before the buyer can decide COMMIT. The provider
    # retains its original result; recovery must not invoke the Agent again.
    second = plan(buyer, "debt-response-lost", "DEBT")
    original_invoke = buyer.calls.invoke
    def lose_proof(*args, **kwargs):
        outcome = copy.deepcopy(original_invoke(*args, **kwargs))
        outcome.metadata.pop("points_delivery", None)
        return outcome
    buyer.calls.invoke = lose_proof
    second_key = second["record"]["plan_id"]
    assert buyer.trades.execute(second_key)["payment"]["state"] == "UNKNOWN"
    buyer.calls.invoke = original_invoke
    result = buyer.trades.recover_points(second_key)
    assert result["payment"]["state"] == "CONFIRMED"
    assert result["delivery"]["result"]["text"] == "private after sample"
    assert len(executed) == 12 and provider.points_node.book.balance(provider.identity.did)["amount"] == 10
    assert provider.points_node.book.debt(buyer.identity.did) == 5


def test_verified_discovery_imports_nat_points_catalog_without_accepting_currency(nat_market):
    root, provider, buyer, middle, _ = nat_market
    wait(lambda: provider.public_coordination.mailbox_route)
    buyer.public_coordination.remember(provider.public_coordination.record())
    assert provider.identity.did in buyer.points_node.discover_peers()
    row = buyer.store.get("points_peers", provider.identity.did)
    assert row["route"]["kind"] == "settlement_mailbox"
    assert not buyer.store.items("points_acceptance")
    assert provider.identity.did in buyer.points_node.catalogs()[0]


def test_settlement_lease_and_route_authorization_reject_forgery(nat_market):
    _, provider, buyer, _, _ = nat_market
    lease = provider.settlement_mailbox.lease
    assert verify_lease(lease)
    assert not verify_lease({**lease, "author_did": buyer.identity.did})
    route = provider.settlement_transport.own_route()
    with pytest.raises(ValueError, match="LEASE_MISMATCH|LEASE_UNAVAILABLE"):
        buyer.settlement_transport.json("did:a2n:ag_" + "0" * 24, route, "points", "catalog", {})
    with pytest.raises(ValueError, match="SETTLEMENT_REJECTED"):
        buyer.settlement_transport.json(provider.identity.did, route, "points", "balance", {"payload": {}, "auth": provider.points_node.auth(provider.identity.did, "balance", {})})
    assert not provider.store.items("points_transactions")


@pytest.mark.parametrize("method", ["native", "x402"])
def test_actual_private_chain_payment_and_delivery_when_provider_has_no_inbound_entry(nat_market, method):
    from contextlib import nullcontext
    from eth_account import Account
    from .payment_evm_support import TestEVM, TestFacilitator, evm_http, facilitator_http
    root, provider, buyer, _, executed = nat_market
    evm = TestEVM()
    facilitator = TestFacilitator(evm) if method == "x402" else None
    with evm_http(evm) as rpc_port, (facilitator_http(facilitator) if facilitator else nullcontext(None)) as facilitator_url:
        for i, node in enumerate((provider, buyer)):
            cfg = {"allow_http": True, "native": [{"currency": "ETH", "network": "eip155:" + str(evm.chain_id),
                "rpc_url": f"http://127.0.0.1:{rpc_port}", "allow_http": True, "confirmations": 1}]}
            if facilitator:
                cfg["x402"] = {"allow_http": True, "assets": [{"currency": "USDC", "network": "eip155:" + str(evm.chain_id),
                    "asset": facilitator.token, "name": "A2N Test Token", "version": "1", "rpc_url": f"http://127.0.0.1:{rpc_port}", "confirmations": 1}]}
                if node is provider:
                    cfg["facilitator_url"] = facilitator_url
            node.payment_coordination.configure({"config": cfg, "keystore": Account.encrypt(evm.keys[i].to_bytes(), "test", kdf="pbkdf2", iterations=1000), "password": "test"})
            node.risk.configure({c: {k: 10**18 for k in ("per_trade", "total_exposure", "daily_spend", "per_counterparty")} for c in ("ETH", "USDC")}, expected_revision=0)
        provider.points_node.book.set_service("text", {"enabled": False, "amount": 5, "modes": ["EARN", "DEBT", "PAY"], "debt_limit": 20}, expected_revision=1)
        currency = "USDC" if facilitator else "ETH"
        provider.runtime.bindings.get("text").source_card.setdefault("x-a2n", {})["price_book"] = {"inspect": {currency: {"dimensions": [{"key": "call_count", "amount": 10000, "per": 1}]}}}
        provider.relay_provider._register()
        buyer.runtime.import_agent(provider.relay_provider._cards()[0], projection_id="money")
        graduate(provider, buyer)
        offer = buyer.trades.quote({"projection_id": "money", "request": {"task_id": "paid-" + method, "skill": "inspect", "payload": {"text": "private paid service"}}})
        index = next(i for i, o in enumerate(offer["options"]) if o["method"] == ("x402/2" if facilitator else "evm-native/1"))
        row = buyer.payment_coordination.prepare({"offer_id": offer["offer_id"], "option_index": index, "command_id": "approve-" + method, "fee_cap_minor": 0 if facilitator else 10**15})
        key = row["record"]["plan_id"]
        assert isinstance(row["settlement_route"], dict)
        if not facilitator:
            assert buyer.payment_coordination.pay(key)["state"] == "CONFIRMED"
        result = buyer.trades.execute(key)
        assert result["ok"] and result["result"]["text"] == "private paid service", result
        assert buyer.payments.get(row["intent_id"])["state"] == "CONFIRMED"
        buyer.trades.execute(key)
        assert len(executed) == 11
        if facilitator:
            assert facilitator.balance(buyer.payment_coordination.signer.address) == 990000
            assert facilitator.balance(provider.payment_coordination.signer.address) == 1010000
            assert facilitator.settles == 1
        else:
            assert evm.broadcasts == 1
        # The provider must also reach an outbound-only buyer when notifying
        # a signed partial refund. Never create a replacement service call.
        dispute = buyer.resolutions.open("money", "paid-" + method, "requester", "partial refund")
        proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "REFUND",
            "text": "agreed partial refund", "amount_minor": 1000, "currency": currency}, command_id="refund")
        buyer.resolution_delivery.drain()
        buyer.resolutions.accept(dispute["id"], proposal["message_id"], command_id="buyer-accept")
        buyer.resolution_delivery.drain()
        provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="provider-accept")
        provider.resolution_delivery.drain()
        remote_errors = []
        original_remote = provider.payment_coordination._remote
        def trace_remote(*args, **kwargs):
            try:
                return original_remote(*args, **kwargs)
            except Exception as exc:
                remote_errors.append((type(exc).__name__, str(exc)))
                raise
        provider.payment_coordination._remote = trace_remote
        refund = provider.payment_coordination.refund({"proposal_id": proposal["message_id"],
            "fee_cap_minor": 0 if facilitator else 10**15})
        if refund["state"] != "CONFIRMED":
            refund = provider.payment_coordination.refund({"proposal_id": proposal["message_id"]}, reconcile=True)
        assert refund["state"] == "CONFIRMED", refund
        assert not remote_errors, (remote_errors, buyer.settlement_mailbox.last_error,
            buyer.settlement_mailbox.lease, list(root.public_settlement_mailbox._boxes),
            provider.store.get("payment_acceptances", key).get("settlement_route"))
        assert buyer.trade_facts.get(offer["trade_uid"])["refund"] == "CONFIRMED"
        provider.payment_coordination.refund({"proposal_id": proposal["message_id"],
            "fee_cap_minor": 0 if facilitator else 10**15})
        assert len(executed) == 11
        if facilitator:
            assert facilitator.balance(buyer.payment_coordination.signer.address) == 991000
            assert facilitator.balance(provider.payment_coordination.signer.address) == 1009000
            assert facilitator.settles == 2
        else:
            assert evm.broadcasts == 2
