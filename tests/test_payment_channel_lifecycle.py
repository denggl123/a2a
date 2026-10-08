"""Original payment channels survive owner changes and process restarts."""
import copy
from urllib.parse import urlsplit

import pytest
from eth_account import Account
from a2n_node.daemon import Daemon
from .test_paid_market import market, graduate, complete_paid


def rotate(node, evm, key_index, *, native=True):
    config = copy.deepcopy(node.payment_coordination.config)
    config.pop("x402", None)
    config.pop("facilitator_url", None)
    if not native:
        config["native"] = []
    else:
        config["native"][0]["confirmations"] = 4
    return node.payment_coordination.configure({"config":config,
        "keystore":Account.encrypt(evm.keys[key_index].to_bytes(),"test",kdf="pbkdf2",iterations=1000),"password":"test"})


def test_unpaid_expired_incoming_plan_does_not_lock_settings_or_claim_payment_failure(market):
    provider,buyer,evm,executed=market
    graduate(provider,buyer)
    offer=buyer.payment_coordination.quote({"projection_id":"use","request":{"task_id":"unpaid-expiry","skill":"add","payload":{"a":2,"b":3}}})
    row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"unpaid-expiry"})
    provider.payment_coordination.coordinator.now=lambda:row["record"]["expires_at"]+1
    status=rotate(provider,evm,2,native=False)
    incoming=next(r for r in status["incoming"] if r["plan_id"]==row["record"]["plan_id"])
    assert incoming["state"]=="ACCEPTED" and incoming["authorization_state"]=="EXPIRED"
    assert incoming["original_channel_retained"] and not status["settings_locked"]
    assert status["native"]==[] and evm.broadcasts==0 and len(executed)==10
    buyer.payment_coordination.command("/v1/payment-coordination/plans/"+row["record"]["plan_id"]+"/cancel",{})
    assert provider.store.get("payment_acceptances",row["record"]["plan_id"])["state"]=="REVOKED"


def test_unknown_payment_keeps_original_wallet_driver_and_recovers_after_config_change_and_restart(market):
    provider,buyer,evm,executed=market
    graduate(provider,buyer)
    buyer.management.command("/v1/projections", {"card":provider.runtime.project_binding("paid-add"),"projection_id":"use"})
    offer=buyer.payment_coordination.quote({"projection_id":"use","request":{"task_id":"unknown-channel","skill":"add","payload":{"a":5,"b":8}}})
    row=buyer.payment_coordination.prepare({"offer_id":offer["offer_id"],"option_index":0,"fee_cap_minor":"1000000000000000","command_id":"unknown-channel"})
    assert buyer.payment_coordination.pay(row["record"]["plan_id"])["state"]=="UNKNOWN"
    original_driver=buyer.payments.get(row["intent_id"])["driver_id"]
    original_wallet=buyer.payment_coordination.signer.address
    rotate(buyer,evm,2,native=False)
    assert buyer.payments.drivers[original_driver].signer.address==original_wallet
    assert buyer.payment_coordination.pay(row["record"]["plan_id"])["state"]=="UNKNOWN"
    port=urlsplit(buyer.runtime.local_base_url).port
    home,protector=buyer.home,buyer.store.protector
    buyer.stop()
    restarted=Daemon(home,port=port,protector=protector,beacon=False,coord_allow_networks=["127.0.0.0/8"]).start()
    try:
        assert restarted.payments.drivers[original_driver].signer.address==original_wallet
        assert restarted.payment_coordination.signer.address!=original_wallet
        evm.chain.mine_blocks(2)
        assert restarted.payment_coordination.pay(row["record"]["plan_id"],reconcile=True)["state"]=="CONFIRMED"
        outcome=restarted.payment_coordination.execute(row["record"]["plan_id"])
        assert outcome["result"]=={"sum":13}, outcome
        assert evm.broadcasts==1 and len(executed)==11
    finally:
        restarted.stop()


def test_refund_after_seller_wallet_change_spends_original_wallet_only(market):
    provider,buyer,evm,executed=market
    graduate(provider,buyer)
    row=complete_paid(provider,buyer,evm,task_id="refund-old-wallet")
    original_wallet=provider.payment_coordination.signer.address
    rotate(provider,evm,2,native=False)
    dispute=buyer.resolutions.open("use","refund-old-wallet","requester","agree a partial refund")
    proposal=buyer.resolutions.post(dispute["id"],kind="PROPOSAL",body={"action":"REFUND","text":"agreed","amount_minor":1234,"currency":"ETH"},command_id="refund-old-wallet")
    buyer.resolution_delivery.drain()
    buyer.resolutions.accept(dispute["id"],proposal["message_id"],command_id="buyer-refund-old")
    buyer.resolution_delivery.drain()
    provider.resolutions.accept(dispute["id"],proposal["message_id"],command_id="seller-refund-old")
    provider.resolution_delivery.drain()
    result=provider.payment_coordination.refund({"proposal_id":proposal["message_id"],"fee_cap_minor":"1000000000000000"})
    evm.chain.mine_blocks(2)
    result=provider.payment_coordination.refund({"proposal_id":proposal["message_id"]},reconcile=True)
    assert result["state"]=="CONFIRMED"
    tx=evm.call("eth_getTransactionByHash",[result["reference"]])
    assert tx["from"].lower()==original_wallet.lower()
    assert tx["from"].lower()!=provider.payment_coordination.signer.address.lower()
    assert evm.broadcasts==2 and len(executed)==11
