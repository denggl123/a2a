import copy
import json
import time
import pytest
from a2n_sdk.settlement_policy import SettlementPolicy
from a2n_sdk.storage import LocalStore
from .test_points_market import point_market, graduate
from .test_paid_market import market, graduate as graduate_native
from a2n_sdk.client import NodeClient, NodeRequestError


def sdk(node):
    return NodeClient(node.runtime.local_base_url, token=node.runtime.management_token)


def test_sdk_default_call_uses_owner_policy_and_replay_never_requotes(point_market, monkeypatch):
    provider, buyer, _, executed = point_market
    configure(buyer, [{"method": "a2n-points/1", "mode": "EARN", "max_amount_minor": 5}])
    api = sdk(buyer)
    for i in range(11):
        task = api.call("use", "add", {"a": i, "b": 1}, task_id="sdk-auto-" + str(i))
        assert task["artifacts"][0]["parts"][0]["data"] == {"sum": i + 1}
        assert task["metadata"]["settlement"]["state"] == ("NOT_REQUIRED" if i < 10 else "CONFIRMED")
    monkeypatch.setattr(buyer.trades, "quote", lambda *_: pytest.fail("replay must not requote"))
    replay = api.call("use", "add", {"a": 10, "b": 1}, task_id="sdk-auto-10")
    assert api.get_task("use", "sdk-auto-10")["artifacts"] == replay["artifacts"]
    assert len(executed) == 11 and len(provider.trials.samples("point-add")) == 10
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    with pytest.raises(NodeRequestError, match="IDEMPOTENCY_CONFLICT"):
        api.call("use", "add", {"a": 100, "b": 1}, task_id="sdk-auto-10")


def test_native_automatic_job_survives_node_restart_and_background_finishes_original(market):
    from a2n_node.daemon import Daemon
    provider, buyer, evm, executed = market
    graduate_native(provider, buyer)
    configure(buyer, [{"method": "evm-native/1", "currency": "ETH", "max_amount_minor": 12345,
                      "fee_cap_minor": 10**15}])
    api = sdk(buyer)
    # Persist the imported projection, as installed nodes do, before restarting.
    api.request("POST", "/v1/projections", {"projection_id": "use", "card": buyer.runtime.imported["use"].network_card})
    first = api.call("use", "add", {"a": 20, "b": 1}, task_id="sdk-native-restart")
    assert first["metadata"]["network"]["automatic_trade"]["state"] == "UNKNOWN"
    plan = first["metadata"]["network"]["automatic_trade"]["plan_id"]
    original_policy = buyer.payment_coordination.policy.public()
    buyer.payment_coordination.policy.configure({**{k: v for k, v in original_policy.items() if k != "revision"},
        "expected_revision": original_policy["revision"], "automatic": False})
    home, protector = buyer.home, buyer.store.protector
    buyer.stop()
    restored = Daemon(home, port=0, protector=protector, beacon=False,
                      coord_allow_networks=["127.0.0.0/8"]).start()
    try:
        evm.chain.mine_blocks(2)
        until = time.monotonic() + 15
        task = None
        while time.monotonic() < until:
            task = sdk(restored).get_task("use", "sdk-native-restart")
            if task["metadata"]["network"]["automatic_trade"]["finished"]:
                break
            time.sleep(.1)
        assert task["artifacts"], json.dumps(sdk(restored).request("GET", "/v1/trades/automatic"))
        assert task["artifacts"][0]["parts"][0]["data"] == {"sum": 21}
        assert task["metadata"]["network"]["automatic_trade"]["plan_id"] == plan
        assert len(executed) == 11
        order = next(iter(restored.store.items("payment_orders").values()))
        assert len(order["attempts"]) == 1
    finally:
        restored.stop()


def configure(node, preferences, *, automatic=True, provider=None):
    return node.payment_coordination.policy.configure({"expected_revision": 0,
        "provider_methods": provider, "buyer_preferences": preferences, "automatic": automatic})


def test_preferences_are_ordered_and_currency_network_mode_are_not_interchangeable():
    policy = SettlementPolicy(LocalStore())
    policy.configure({"expected_revision": 0, "provider_methods": [], "automatic": True,
        "buyer_preferences": [{"method": "x402/2", "currency": "USDC", "network": "eip155:8453", "max_amount_minor": "10"},
                              {"method": "a2n-points/1", "mode": "EARN", "max_amount_minor": "5"}]})
    offer = {"options": [{"method": "a2n-points/1", "mode": "DEBT", "amount_minor": 3},
        {"method": "a2n-points/1", "mode": "EARN", "amount_minor": 5},
        {"method": "x402/2", "currency": "USDC", "network": "eip155:1", "amount_minor": 2},
        {"method": "x402/2", "currency": "USDC", "network": "eip155:8453", "amount_minor": 10}]}
    assert [c["option_index"] for c in policy.match(offer)["candidates"]] == [3, 1]
    assert not policy.allowed(offer["options"][3], receiving=True)
    offer["options"][3]["amount_minor"] = 11
    assert policy.match(offer)["option_index"] == 1


def test_automatic_requires_explicit_limits_and_points_mode_and_cas():
    policy = SettlementPolicy(LocalStore())
    body = {"expected_revision": 0, "provider_methods": None, "buyer_preferences": None, "automatic": True}
    with pytest.raises(ValueError, match="BOUNDED"):
        policy.configure(body)
    body["buyer_preferences"] = [{"method": "a2n-points/1", "max_amount_minor": "9007199254740993"}]
    with pytest.raises(ValueError, match="BOUNDED"):
        policy.configure(body)
    body["buyer_preferences"][0]["mode"] = "DEBT"
    assert policy.configure(body)["buyer_preferences"][0]["max_amount_minor"] == "9007199254740993"
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        policy.configure(body)


def test_automatic_samples_bypass_all_settlement_without_any_payment_authorization(point_market):
    provider, buyer, _, executed = point_market
    for i in range(10):
        offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": "auto-sample-" + str(i),
            "skill": "add", "payload": {"a": i, "b": 1}}})
        body = {"offer_id": offer["offer_id"], "command_id": "sample-" + str(i)}
        out = buyer.trades.command("/v1/trades/auto-execute", body)[1]
        assert out["state"] == "NOT_REQUIRED" and out["delivery"]["result"] == {"sum": i + 1}
        assert buyer.trades.command("/v1/trades/auto-execute", body)[1] == out
    assert len(executed) == 10 and len(provider.trials.samples("point-add")) == 10
    for node in (provider, buyer):
        assert not node.store.items("payment_orders") and not node.store.items("points_journal")


def test_supplier_support_and_buyer_order_match_then_settle_once(point_market):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    configure(provider, None, automatic=False, provider=[{"method": "a2n-points/1", "mode": "EARN"}])
    configure(buyer, [{"method": "a2n-points/1", "mode": "DEBT", "max_amount_minor": 10},
                      {"method": "a2n-points/1", "mode": "EARN", "max_amount_minor": 10}])
    offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": "automatic-earned",
        "skill": "add", "payload": {"a": 3, "b": 4}}})
    assert [o["mode"] for o in offer["options"]] == ["EARN"]
    body = {"offer_id": offer["offer_id"], "command_id": "automatic-earned"}
    out = buyer.trades.command("/v1/trades/auto-execute", body)[1]
    assert out["state"] == "CONFIRMED" and out["delivery"]["result"] == {"sum": 7}
    assert buyer.trades.command("/v1/trades/auto-execute", body)[1] == out
    assert provider.points_node.book.balance(provider.identity.did)["amount"] == 5
    assert provider.points_node.book.debt(buyer.identity.did) == 0 and len(executed) == 11


def test_no_matching_mode_never_executes_or_creates_a_payment(point_market):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    configure(buyer, [{"method": "a2n-points/1", "mode": "EARN", "max_amount_minor": 4}])
    offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": "too-expensive",
        "skill": "add", "payload": {"a": 3, "b": 4}}})
    with pytest.raises(ValueError, match="NO_COMMON"):
        buyer.trades.command("/v1/trades/auto-execute", {"offer_id": offer["offer_id"], "command_id": "too-expensive"})
    assert len(executed) == 10 and not buyer.store.items("points_orders")


def test_native_pending_confirmation_only_recovers_original_payment(market):
    provider, buyer, evm, executed = market
    graduate_native(provider, buyer)
    configure(buyer, [{"method": "evm-native/1", "currency": "ETH", "max_amount_minor": 12345,
                      "fee_cap_minor": 10**15}])
    offer = buyer.trades.quote({"projection_id": "use", "request": {"task_id": "automatic-native",
        "skill": "add", "payload": {"a": 4, "b": 5}}})
    body = {"offer_id": offer["offer_id"], "command_id": "automatic-native"}
    first = buyer.trades.command("/v1/trades/auto-execute", body)[1]
    assert first["state"] == "UNKNOWN" and len(executed) == 10
    policy = buyer.payment_coordination.policy.public()
    buyer.payment_coordination.policy.configure({**{k:v for k,v in policy.items() if k!='revision'},
        "expected_revision": policy['revision'], "automatic": False})
    evm.chain.mine_blocks(2)
    recovered = buyer.trades.command("/v1/trades/auto-execute", body)[1]
    assert recovered["state"] == "CONFIRMED" and recovered["delivery"]["result"] == {"sum": 9}
    assert buyer.trades.command("/v1/trades/auto-execute", body)[1] == recovered
    order = next(iter(buyer.store.items("payment_orders").values()))
    assert len(order["attempts"]) == 1 and len(executed) == 11


def test_auto_revoked_between_acceptance_and_payment_never_spends(market, monkeypatch):
    provider, buyer, evm, executed = market
    graduate_native(provider, buyer)
    configure(buyer, [{"method":"evm-native/1", "max_amount_minor":12345, "fee_cap_minor":10**15}])
    offer = buyer.trades.quote({"projection_id":"use", "request":{"task_id":"revoke", "skill":"add", "payload":{"a":1,"b":2}}})
    original = buyer.payment_coordination.prepare
    def revoke(body):
        result = original(body)
        p = buyer.payment_coordination.policy.public()
        buyer.payment_coordination.policy.configure({"expected_revision":p['revision'],
            "provider_methods":p['provider_methods'], "buyer_preferences":p['buyer_preferences'], "automatic":False})
        return result
    monkeypatch.setattr(buyer.payment_coordination, 'prepare', revoke)
    with pytest.raises(ValueError, match='AUTHORIZATION_REVOKED'):
        buyer.trades.command('/v1/trades/auto-execute', {'offer_id':offer['offer_id'], 'command_id':'revoke'})
    assert evm.broadcasts == 0 and len(executed) == 10


def test_automatic_points_lost_commit_recovers_same_transaction(point_market, monkeypatch):
    provider, buyer, _, executed = point_market
    graduate(provider, buyer)
    configure(buyer, [{"method":"a2n-points/1", "mode":"EARN", "max_amount_minor":5}])
    offer = buyer.trades.quote({"projection_id":"use", "request":{"task_id":"auto-lost", "skill":"add", "payload":{"a":1,"b":2}}})
    body = {'offer_id':offer['offer_id'], 'command_id':'auto-lost'}
    original = buyer.points_node.request
    lost=[]
    def request(target, action, payload, **kwargs):
        out=original(target, action, payload, **kwargs)
        if target==provider.identity.did and action=='apply' and not lost:
            lost.append(True)
            raise TimeoutError('receipt lost after commit')
        return out
    monkeypatch.setattr(buyer.points_node, 'request', request)
    first=buyer.trades.command('/v1/trades/auto-execute', body)[1]
    assert first['state']=='PENDING' and first['order_state']=='COMMITTING'
    from a2n_node.points_payment import PointsPayment
    buyer.points=PointsPayment(buyer.points_node)
    recovered=buyer.trades.command('/v1/trades/auto-execute', body)[1]
    assert recovered['plan_id']==first['plan_id'] and recovered['state']=='CONFIRMED'
    assert len(executed)==11 and provider.points_node.book.balance(provider.identity.did)['amount']==5


@pytest.mark.parametrize("lost_response", [False, True])
def test_automatic_x402_actual_eip3009_contract_settlement_once(market, lost_response, monkeypatch):
    from .payment_evm_support import TestFacilitator, facilitator_http
    provider, buyer, evm, executed=market
    facilitator=TestFacilitator(evm)
    facilitator.lose_response = lost_response
    original_rpc = evm.call
    if lost_response:
        def interrupted_observation(method, params):
            if facilitator.settles and method in {"eth_getLogs", "eth_getTransactionReceipt"}:
                raise TimeoutError("controlled receipt observation outage")
            return original_rpc(method, params)
        monkeypatch.setattr(evm, "call", interrupted_observation)
    with facilitator_http(facilitator) as url:
        for node in (provider,buyer):
            cfg=copy.deepcopy(node.payment_coordination.config); native=cfg['native'][0]
            cfg['x402']={'allow_http':True,'assets':[{'currency':'USDC','network':native['network'], 'asset':facilitator.token,
                'name':'A2N Test Token','version':'1','rpc_url':native['rpc_url'],'confirmations':3}]}
            if node is provider:cfg['facilitator_url']=url
            node.payment_coordination.configure({'config':cfg})
            limits=copy.deepcopy(node.risk.policy()['limits']); limits['USDC']={k:100000 for k in ('per_trade','total_exposure','daily_spend','per_counterparty')}
            node.risk.configure(limits, expected_revision=node.risk.policy()['revision'])
        provider.runtime.bindings.get('paid-add').source_card['x-a2n']['price_book']['add']['USDC']={'dimensions':[{'key':'call_count','amount':10000,'per':1}]}
        buyer.runtime.import_agent(provider.runtime.project_binding('paid-add'), projection_id='auto-x402')
        graduate_native(provider,buyer)
        configure(buyer,[{'method':'x402/2','currency':'USDC','max_amount_minor':10000},
                         {'method':'evm-native/1','max_amount_minor':12345,'fee_cap_minor':10**15}])
        offer=buyer.trades.quote({'projection_id':'auto-x402','request':{'task_id':'auto-x402','skill':'add','payload':{'a':8,'b':9}}})
        body={'offer_id':offer['offer_id'],'command_id':'auto-x402'}
        result=buyer.trades.command('/v1/trades/auto-execute',body)[1]
        if lost_response:
            assert result['state'] == 'UNKNOWN'
            original_plan = result['plan_id']
            monkeypatch.setattr(evm, "call", original_rpc)
            # No second command is issued: the worker must recover the same
            # authorization and the provider's signed original delivery.
            until = time.monotonic() + 15
            while time.monotonic() < until:
                row = buyer.store.get('automatic_trades', body['command_id'])
                if row.get('finished'):
                    result = row['result']; break
                time.sleep(.1)
            assert result['plan_id'] == original_plan
        assert result['state']=='CONFIRMED' and result['delivery']['result']=={'sum':17}
        assert offer['options'][result['option_index']]['method']=='x402/2'
        assert buyer.trades.command('/v1/trades/auto-execute',body)[1]==result
        assert facilitator.balance(buyer.payment_coordination.signer.address)==990000
        assert facilitator.balance(provider.payment_coordination.signer.address)==1010000 and len(executed)==11
