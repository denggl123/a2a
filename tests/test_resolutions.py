import base64
import os

import pytest

from a2n_node.daemon import Daemon
from a2n_node.feedback_identity import signer_for
from a2n_node.resolution_gateway import envelope
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.experience import signed, unsigned
from a2n_sdk.ports import CallRequest


@pytest.fixture
def trade(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    nodes = [Daemon(tmp_path / str(i), port=0, protector=protector,
        coord_allow_networks=["127.0.0.0/8"]).start() for i in range(3)]
    provider, buyer, observer = nodes
    try:
        provider.runtime.mount_callable({"name": "real", "version": "1", "skills": [{"id": "write", "name": "write"}]},
            lambda p: "real delivery", service_id="svc")
        buyer.runtime.import_agent(provider.runtime.project_binding("svc"), projection_id="use")
        result = buyer.calls.invoke("use", CallRequest(task_id="one", skill="write", payload="actual"))
        assert result.ok
        dispute = buyer.resolutions.open("use", "one", "requester", "结果不适合")
        yield provider, buyer, observer, dispute
    finally:
        for node in reversed(nodes):
            node.stop()


def test_signed_private_proposal_delivery_reply_and_two_explicit_acceptances(trade):
    provider, buyer, _, dispute = trade
    proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "CLOSE", "text": "双方关闭异议",
        "amount_minor": 0, "currency": ""}, command_id="offer")
    buyer.resolution_delivery.drain()
    assert buyer.store.get("resolution_outbox", proposal["message_id"])["state"] == "DELIVERED"
    assert provider.store.get("resolution_messages", proposal["message_id"]) == proposal
    reply = provider.resolutions.post(dispute["id"], body={"text": "private reply"}, parent_id=proposal["message_id"], command_id="reply")
    provider.resolution_delivery.drain()
    assert buyer.store.get("resolution_messages", reply["message_id"]) == reply
    buyer.resolutions.accept(dispute["id"], proposal["message_id"], command_id="accept-buyer")
    buyer.resolution_delivery.drain()
    assert provider.store.get("resolution_agreements", proposal["message_id"])["state"] == "ONE_ACCEPTED"
    provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="accept-provider")
    provider.resolution_delivery.drain()
    for node in (buyer, provider):
        agreement = node.store.get("resolution_agreements", proposal["message_id"])
        assert agreement["state"] == "BOTH_ACCEPTED" and agreement["execution"] == "CLOSED_BILATERAL"
        assert node.store.get("disputes", dispute["id"])["state"] == "CLOSED_BILATERAL"
        assert not node.store.items("payment_intents")
        assert len(node.store.recent()) == 1
    # Messages are private business data; public experience has no projection of them.
    assert not buyer.experience.records(public=True) and not provider.experience.records(public=True)


def test_foreign_node_cannot_receive_or_change_a_private_negotiation(trade):
    provider, buyer, observer, dispute = trade
    record = buyer.resolutions.post(dispute["id"], body={"text": "private"}, command_id="message")
    forged = signed({**unsigned(record), "author_did": observer.identity.did}, signer_for(observer.identity))
    with pytest.raises(ValueError, match="PARTY_MISMATCH"):
        provider.resolutions.receive(forged)
    with pytest.raises(ValueError, match="UNVERIFIED_LOCAL_TRADE"):
        observer.resolutions.receive(record)
    message = envelope(observer.identity, "GET_DELIVERY", provider.identity.did, {"message_id": record["message_id"]})
    assert provider.public_resolution.handle("delivery", message)[0] == 400


def test_duplicate_post_is_idempotent_and_acceptance_must_bind_the_exact_proposal(trade):
    provider, buyer, _, dispute = trade
    first = buyer.resolutions.post(dispute["id"], body={"text": "one"}, command_id="once")
    assert buyer.resolutions.post(dispute["id"], body={"text": "one"}, command_id="once") == first
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        buyer.resolutions.post(dispute["id"], body={"text": "changed"}, command_id="once")
    proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "REWORK", "text": "补做",
        "amount_minor": 0, "currency": ""}, command_id="proposal")
    buyer.resolution_delivery.drain()
    acceptance = provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="accept")
    forged = signed({**unsigned(acceptance), "body": {"proposal_hash": "0" * 64}, "message_id": "different"}, signer_for(provider.identity))
    with pytest.raises(ValueError, match="PROPOSAL_MISMATCH"):
        buyer.resolutions.receive(forged)


def test_decline_is_signed_and_does_not_close_or_authorize_execution(trade):
    provider, buyer, _, dispute = trade
    proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "CLOSE", "text": "close",
        "amount_minor": 0, "currency": ""}, command_id="offer")
    buyer.resolution_delivery.drain()
    provider.resolutions.decide(dispute["id"], proposal["message_id"], accepted=False, command_id="decline")
    provider.resolution_delivery.drain()
    assert buyer.store.get("resolution_agreements", proposal["message_id"])["state"] == "DECLINED"
    assert buyer.store.get("disputes", dispute["id"])["state"] == "OPEN"
    with pytest.raises(ValueError, match="DECISION_ALREADY_FIXED"):
        provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="changed")


def test_agreed_rework_requires_explicit_start_and_new_linked_id_but_never_duplicates(trade):
    provider, buyer, _, dispute = trade
    proposal = buyer.resolutions.post(dispute["id"], kind="PROPOSAL", body={"action": "REWORK", "text": "同一版本同一输入补做一次",
        "amount_minor": 0, "currency": ""}, command_id="offer")
    buyer.resolution_delivery.drain()
    buyer.resolutions.accept(dispute["id"], proposal["message_id"], command_id="buy-accept")
    buyer.resolution_delivery.drain()
    provider.resolutions.accept(dispute["id"], proposal["message_id"], command_id="sell-accept")
    provider.resolution_delivery.drain()
    assert len(provider.store.recent()) == 1
    # Even after the normal trial has ended, this one agreed work is free.
    provider.store.put("trials", "svc", {"service_id": "svc", "cap": 10, "completed": 10})
    result = buyer.rework.execute(proposal["message_id"])
    assert result["ok"] and result["task_id"] != "one"
    assert buyer.rework.execute(proposal["message_id"]) == result
    assert len(provider.store.recent()) == 2
    for node in (buyer, provider):
        agreement = node.store.get("resolution_agreements", proposal["message_id"])
        assert agreement["execution"] == "REWORK_DELIVERED"
        assert agreement["rework_trade_uid"] != dispute["trade_uid"]
