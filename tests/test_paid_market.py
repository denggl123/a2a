"""Complete two-node market calls; real private EVM transfers and free samples."""
import base64
import copy
import os
import pytest
from eth_account import Account
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.ports import CallRequest
from .payment_evm_support import TestEVM, evm_http


@pytest.fixture
def market(tmp_path):
    pytest.importorskip("eth_tester")
    evm = TestEVM()
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    with evm_http(evm) as port:
        nodes = [Daemon(tmp_path/str(i),port=0,protector=protector,beacon=False,coord_allow_networks=["127.0.0.0/8"]).start() for i in range(2)]
        try:
            for i,n in enumerate(nodes):
                n.management.discovery_public_base=n.runtime.local_base_url
                cfg={"allow_http":True,"native":[{"currency":"ETH","network":"eip155:"+str(evm.chain_id),
                    "rpc_url":f"http://127.0.0.1:{port}","allow_http":True,"confirmations":3}]}
                n.payment_coordination.configure({"config":cfg,"keystore":Account.encrypt(evm.keys[i].to_bytes(),"local-test",kdf="pbkdf2",iterations=1000),"password":"local-test"})
                n.risk.configure({"ETH":dict(per_trade=10**17,total_exposure=10**18,daily_spend=10**18,per_counterparty=10**18)},expected_revision=0,max_pending=8)
            provider,buyer=nodes
            executed=[]
            card={"name":"实际计算","version":"1","skills":[{"id":"add","name":"add"}],
                "x-a2n":{"price_book":{"add":{"ETH":{"dimensions":[{"key":"call_count","amount":12345,"per":1}]}}}}}
            provider.runtime.mount_callable(card,lambda p: executed.append(p) or {"sum":p["a"]+p["b"]},service_id="paid-add")
            buyer.runtime.import_agent(provider.runtime.project_binding("paid-add"),projection_id="use")
            yield provider,buyer,evm,executed
        finally:
            for n in reversed(nodes): n.stop()


def graduate(provider,buyer):
    for i in range(10):
        outcome=buyer.calls.invoke("use",CallRequest(skill="add",task_id=f"free-{i}",payload={"a":i,"b":1}))
        assert outcome.ok
        assert outcome.metadata["admission_contract"]["amount_minor"]==0
        assert outcome.settlement["state"]=="NOT_REQUIRED"
        assert buyer.trade_facts.get(outcome.metadata["trade_uid"])["payment"]=="NOT_REQUIRED"
    assert provider.trials.status("paid-add")["completed"]==10
    assert len(provider.trials.samples("paid-add"))==10


def complete_paid(provider,buyer,evm,task_id="for-refund"):
    offer=buyer.payment_coordination.quote({"projection_id":"use","request":{"task_id":task_id,"skill":"add","payload":{"a":1,"b":2}}})
    row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":task_id})
    buyer.payment_coordination.pay(row["record"]["plan_id"])
    evm.chain.mine_blocks(2)
    buyer.payment_coordination.pay(row["record"]["plan_id"],reconcile=True)
    assert buyer.payment_coordination.execute(row["record"]["plan_id"])["ok"]
    return row


def test_free_first_ten_then_bilateral_paid_call_on_actual_chain(market):
    provider,buyer,evm,executed=market
    # A signed quote cannot charge an Agent still in its initial free period.
    offer=buyer.payment_coordination.quote({"projection_id":"use","request":{"task_id":"initial","skill":"add","payload":{"a":2,"b":3}}})
    assert offer["payment_state"]=="NOT_REQUIRED" and not offer["options"]
    graduate(provider,buyer)
    request={"task_id":"paid-one","skill":"add","payload":{"a":5,"b":9}}
    offer=buyer.payment_coordination.quote({"projection_id":"use","request":request})
    assert len(offer["options"])==1
    row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"approve"})
    plan_id=row["record"]["plan_id"]
    with pytest.raises(ValueError,match="CONFIRMED_UPFRONT"):
        buyer.payment_coordination.execute(plan_id)
    before=evm.chain.get_balance(provider.payment_coordination.signer.address)
    paid=buyer.payment_coordination.pay(plan_id)
    assert paid["state"]=="UNKNOWN" and len(executed)==10
    evm.chain.mine_blocks(2)
    assert buyer.payment_coordination.pay(plan_id,reconcile=True)["state"]=="CONFIRMED"
    result=buyer.payment_coordination.execute(plan_id)
    assert result["ok"] and result["result"]=={"sum":14}
    assert evm.chain.get_balance(provider.payment_coordination.signer.address)==before+12345
    assert len(executed)==11 and evm.broadcasts==1
    buyer.payment_coordination.execute(plan_id)
    assert len(executed)==11 and evm.broadcasts==1
    assert result["metadata"]["admission_contract"]["payment_plan_id"]==plan_id
    assert buyer.trade_facts.get(offer["trade_uid"])["payment"]=="CONFIRMED"
    assert provider.trade_facts.get(offer["trade_uid"])["payment"]=="CONFIRMED"
    assert len(provider.trials.samples("paid-add"))==10


def test_cancel_before_exposure_revokes_provider_and_allows_another_attempt(market):
    provider,buyer,evm,_=market
    graduate(provider,buyer)
    body={"projection_id":"use","request":{"task_id":"switch-one","skill":"add","payload":{"a":1,"b":1}}}
    offer=buyer.payment_coordination.quote(body)
    row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"first"})
    plan_id=row["record"]["plan_id"]
    buyer.payment_coordination.command("/v1/payment-coordination/plans/"+plan_id+"/cancel",{})
    assert provider.store.get("payment_acceptances",plan_id)["state"]=="REVOKED"
    assert not evm.broadcasts
    offer2=buyer.payment_coordination.quote(body)
    row2=buyer.payment_coordination.prepare({"offer_id":offer2["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"second"})
    assert row2["intent_id"]!=row["intent_id"]
    assert len(buyer.payments.order(offer["trade_uid"])["attempts"])==2


def test_signed_bilateral_refund_is_independent_actual_reverse_payment(market):
    provider,buyer,evm,executed=market
    graduate(provider,buyer)
    row=complete_paid(provider,buyer,evm)
    uid=row["record"]["offer"]["trade_uid"]
    dispute=buyer.resolutions.open("use","for-refund","requester","我觉得质量不够好")
    proposal=buyer.resolutions.post(dispute["id"],kind="PROPOSAL",body={"action":"REFUND","text":"双方约定退一部分",
        "amount_minor":1234,"currency":"ETH"},command_id="refund-offer")
    buyer.resolution_delivery.drain()
    buyer.resolutions.accept(dispute["id"],proposal["message_id"],command_id="refund-buyer")
    buyer.resolution_delivery.drain()
    provider.resolutions.accept(dispute["id"],proposal["message_id"],command_id="refund-provider")
    provider.resolution_delivery.drain()
    before=evm.chain.get_balance(buyer.payment_coordination.signer.address)
    body={"proposal_id":proposal["message_id"],"fee_cap_minor":"1000000000000000"}
    result=provider.payment_coordination.refund(body)
    assert result["state"]=="UNKNOWN" and evm.broadcasts==2
    evm.chain.mine_blocks(2)
    assert provider.payment_coordination.refund(body,reconcile=True)["state"]=="CONFIRMED"
    assert evm.chain.get_balance(buyer.payment_coordination.signer.address)==before+1234
    provider.payment_coordination.refund(body)
    assert evm.broadcasts==2 and len(executed)==11
    for n in (provider,buyer):
        facts=n.trade_facts.get(uid)
        assert facts["payment"]=="CONFIRMED" and facts["refund"]=="CONFIRMED" and facts["execution"]=="DELIVERED"


@pytest.mark.parametrize("lost_settlement_response",[False,True])
def test_x402_market_executes_once_recovers_delivery_and_rejects_new_nonce(market,lost_settlement_response):
    from .test_x402_module import fixture_http,config
    from a2n_sdk.x402.protocol import encode_header
    import json
    provider,buyer,evm,executed=market
    with fixture_http() as (state,facilitator,ledger):
        for node in (provider,buyer):
            cfg=copy.deepcopy(node.payment_coordination.config)
            cfg["x402"]=config(state["url"]+"/rpc")
            if node is provider:cfg["facilitator_url"]=state["url"]
            node.payment_coordination.configure({"config":cfg})
            limits=copy.deepcopy(node.risk.policy()["limits"])
            limits["USDC"]={k:100000 for k in ("per_trade","total_exposure","daily_spend","per_counterparty")}
            node.risk.configure(limits,expected_revision=node.risk.policy()["revision"])
        provider.runtime.bindings.get("paid-add").source_card["x-a2n"]["price_book"]["add"]["USDC"]={"dimensions":[{"key":"call_count","amount":10000,"per":1}]}
        buyer.runtime.import_agent(provider.runtime.project_binding("paid-add"),projection_id="use-x402")
        # Graduate through the existing signed free invocation path.
        graduate(provider,buyer)
        offer=buyer.payment_coordination.quote({"projection_id":"use-x402","request":{"task_id":"paid-x402","skill":"add","payload":{"a":4,"b":8}}})
        index=next(i for i,o in enumerate(offer["options"]) if o["method"]=="x402/2")
        row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":index,"fee_cap_minor":0,"command_id":"approve-x402"})
        facilitator.lose_settle_response=lost_settlement_response
        result=buyer.payment_coordination.execute(row["record"]["plan_id"])
        if lost_settlement_response:
            assert result["state"]=="DELIVERY_UNKNOWN"
            recovered=buyer.payment_coordination.reconcile_x402(row["record"]["plan_id"])
            assert recovered["payment"]["state"]=="CONFIRMED" and recovered["delivery"]
            result=buyer.calls.get("use-x402","paid-x402").to_dict()
        assert result["ok"] and result["result"]=={"sum":12}
        assert result["metadata"]["verified_delivery"] and len(executed)==11
        assert facilitator.settles==facilitator.verifies==1
        buyer.payment_coordination.execute(row["record"]["plan_id"])
        assert len(executed)==11 and facilitator.settles==1
        original=buyer.store.get("x402_plans",row["intent_id"])
        payload=buyer.payment_coordination.signer.sign(original["required"],original["accepted"])
        body=json.loads(base64.b64decode(original["body_base64"]))
        with pytest.raises(ValueError,match="PAYMENT_ORDER_LOCKED"):
            provider.payment_coordination.public("execute",body,signature=encode_header(payload),replay_token=original["replay_token"])
        # A delayed revocation cannot undo an authorization already exposed to the gate,
        # even before its payment observation has updated the acceptance row.
        from a2n_sdk.experience import signed
        from a2n_node.feedback_identity import signer_for
        accepted=provider.store.get("payment_acceptances",row["record"]["plan_id"])
        provider.store.put("payment_acceptances",row["record"]["plan_id"],{**accepted,"state":"ACCEPTED"})
        revocation=signed({"v":"a2n-payment-revocation/1","author_did":buyer.identity.did,
            "plan_id":row["record"]["plan_id"]},signer_for(buyer.identity))
        with pytest.raises(ValueError,match="PAYMENT_ORDER_LOCKED"):
            provider.payment_coordination.public("revoke",{"record":revocation})
        provider.store.put("payment_acceptances",row["record"]["plan_id"],accepted)
        for n in (provider,buyer):
            assert n.trade_facts.get(offer["trade_uid"])["payment"]=="CONFIRMED"
        dispute=buyer.resolutions.open("use-x402","paid-x402","requester","refund requested")
        proposal=buyer.resolutions.post(dispute["id"],kind="PROPOSAL",body={"action":"REFUND","text":"agreed partial refund",
            "amount_minor":1000,"currency":"USDC"},command_id="x402-refund-offer")
        buyer.resolution_delivery.drain()
        buyer.resolutions.accept(dispute["id"],proposal["message_id"],command_id="x402-refund-buyer")
        buyer.resolution_delivery.drain()
        provider.resolutions.accept(dispute["id"],proposal["message_id"],command_id="x402-refund-seller")
        provider.resolution_delivery.drain()
        facilitator.lose_settle_response=False
        assert provider.payment_coordination.refund({"proposal_id":proposal["message_id"],"fee_cap_minor":0})["state"]=="CONFIRMED"
        assert len(executed)==11 and facilitator.settles==2
        provider.payment_coordination.refund({"proposal_id":proposal["message_id"],"fee_cap_minor":0})
        assert facilitator.settles==2
        for n in (provider,buyer):
            assert n.trade_facts.get(offer["trade_uid"])["refund"]=="CONFIRMED"


@pytest.mark.parametrize("rotate_channels", [False, True])
def test_actual_eip3009_contract_payment_and_reverse_refund_over_http(market, rotate_channels):
    from .payment_evm_support import TestFacilitator,facilitator_http
    provider,buyer,evm,executed=market
    facilitator=TestFacilitator(evm)
    with facilitator_http(facilitator) as url:
        for node in (provider,buyer):
            cfg=copy.deepcopy(node.payment_coordination.config)
            native=cfg['native'][0]
            cfg['x402']={'allow_http':True,'assets':[{'currency':'USDC','network':native['network'],'asset':facilitator.token,
                'name':'A2N Test Token','version':'1','rpc_url':native['rpc_url'],'confirmations':3}]}
            if node is provider:cfg['facilitator_url']=url
            node.payment_coordination.configure({'config':cfg})
            limits=copy.deepcopy(node.risk.policy()['limits']);limits['USDC']={k:100000 for k in ('per_trade','total_exposure','daily_spend','per_counterparty')}
            node.risk.configure(limits,expected_revision=node.risk.policy()['revision'])
        provider.runtime.bindings.get('paid-add').source_card['x-a2n']['price_book']['add']['USDC']={'dimensions':[{'key':'call_count','amount':10000,'per':1}]}
        buyer.runtime.import_agent(provider.runtime.project_binding('paid-add'),projection_id='actual-x402')
        graduate(provider,buyer)
        offer=buyer.payment_coordination.quote({'projection_id':'actual-x402','request':{'skill':'add','task_id':'chain-x402','payload':{'a':3,'b':7}}})
        index=next(i for i,o in enumerate(offer['options']) if o['method']=='x402/2')
        row=buyer.payment_coordination.prepare({'offer_id':offer['offer_id'],'option_index':index,'fee_cap_minor':0,'command_id':'chain-approve'})
        paid=buyer.payment_coordination.execute(row['record']['plan_id'])
        assert paid['ok'] and paid['result']=={'sum':10}
        assert buyer.payments.get(row['intent_id'])['state']=='CONFIRMED'
        assert facilitator.balance(buyer.payment_coordination.signer.address)==990000
        assert facilitator.balance(provider.payment_coordination.signer.address)==1010000
        original_buyer=buyer.payment_coordination.signer.address
        original_provider=provider.payment_coordination.signer.address
        if rotate_channels:
            for i,node in enumerate((provider,buyer),start=2):
                node.payment_coordination.configure({'config':{'allow_http':True,'native':[]},
                    'keystore':Account.encrypt(evm.keys[i].to_bytes(),'rotation',kdf='pbkdf2',iterations=1000),'password':'rotation'})
            assert buyer.payment_coordination.signer.address!=original_buyer
            assert provider.payment_coordination.signer.address!=original_provider
        dispute=buyer.resolutions.open('actual-x402','chain-x402','requester','partial refund')
        proposal=buyer.resolutions.post(dispute['id'],kind='PROPOSAL',body={'action':'REFUND','text':'agreed','amount_minor':1000,'currency':'USDC'},command_id='chain-refund')
        buyer.resolution_delivery.drain();buyer.resolutions.accept(dispute['id'],proposal['message_id'],command_id='chain-buyer-accept');buyer.resolution_delivery.drain()
        provider.resolutions.accept(dispute['id'],proposal['message_id'],command_id='chain-seller-accept');provider.resolution_delivery.drain()
        facilitator.lose_response=True
        refund=provider.payment_coordination.refund({'proposal_id':proposal['message_id'],'fee_cap_minor':0})
        if refund['state']=='UNKNOWN':
            refund=provider.payment_coordination.refund({'proposal_id':proposal['message_id']},reconcile=True)
        assert refund['state']=='CONFIRMED'
        assert facilitator.balance(original_buyer)==991000
        assert facilitator.balance(original_provider)==1009000
        assert facilitator.settles==2 and len(executed)==11
        assert buyer.trade_facts.get(offer['trade_uid'])['refund']=='CONFIRMED'


def test_lost_acceptance_is_visible_and_recovers_same_terms_without_payment(market):
    import time
    import re
    from urllib.request import urlopen
    provider,buyer,evm,executed=market
    with urlopen(buyer.runtime.local_base_url+"/console") as response:
        html=response.read().decode()
    scripts=re.findall(r'<script src="([^"]+)"',html)
    assert "/console/payment.js" in scripts
    for path in scripts:
        with urlopen(buyer.runtime.local_base_url+path) as response:
            assert response.status==200 and response.headers.get_content_type()=="text/javascript" and response.read()
    graduate(provider,buyer)
    service=buyer.payment_coordination
    offer=service.quote({"projection_id":"use","request":{"skill":"add","task_id":"lost-accept","payload":{"a":1,"b":2}}})
    body={"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"lost-confirmation"}
    for bad_index in (-1,100,True,"0",None):
        with pytest.raises(ValueError,match="PAYMENT_OPTION_UNAVAILABLE"):
            service.prepare({**body,"option_index":bad_index})
    remote=service._remote
    def lose_ack(base,action,payload):
        result=remote(base,action,payload)
        if action=="accept":raise ConnectionError("acceptance response lost")
        return result
    service._remote=lose_ack
    with pytest.raises(ConnectionError):service.prepare(body)
    pending=service.status()["proposals"]
    assert len(pending)==1 and not buyer.payments.store.items("payment_intents") and evm.broadcasts==0
    service._remote=remote
    provider.payment_coordination.coordinator.now=lambda:int(time.time())+601
    service.coordinator.now=lambda:int(time.time())+601
    row=service.activate(pending[0]["plan_id"],"recover-original")
    assert row["state"]=="ACCEPTED" and service.status()["proposals"]==[]
    assert len(provider.store.items("payment_acceptances"))==1
    assert service.activate(row["record"]["plan_id"],"repeat")==row
    with pytest.raises(ValueError,match="EXPIRED"):
        service.pay(row["record"]["plan_id"])
    service.command("/v1/payment-coordination/plans/"+row["record"]["plan_id"]+"/cancel",{})
    assert evm.broadcasts==0 and len(executed)==10
