"""Persistent-runtime to P2P discovery integration tests."""
from __future__ import annotations

import copy
import json
import socket
import time

import pytest

from a2n_node.card import card_did, card_hash, sign_card, verify_card
from a2n_node.p2p_service import ADVERT_PROTOCOL, P2PDiscoveryService
from a2n_p2p import Envelope, Identity, OFFER
from a2n_sdk import NodeRuntime


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.03)
    return False


def _source(name: str, skill: str) -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "url": "http://source.invalid/a2a",
        "skills": [{"id": skill, "name": skill}],
    }


def _runtime_card(identity: Identity, name: str, skill: str, service_id: str):
    runtime = NodeRuntime(identity.did,
                          signer=lambda card: sign_card(identity, card))
    runtime.start_gateway(host="127.0.0.1")
    runtime.mount_callable(_source(name, skill), lambda payload: payload,
                           service_id=service_id)
    return runtime, runtime.project_binding(service_id)


def test_two_real_local_nodes_discover_each_others_signed_projection_cards():
    """Bootstrap + real UDP query + real HTTP card fetch + signature verification."""
    alice_id, bob_id = Identity.generate(), Identity.generate()
    alice_runtime, alice_card = _runtime_card(alice_id, "Alice 翻译", "translate", "alice_translate")
    bob_runtime, bob_card = _runtime_card(bob_id, "Bob OCR", "ocr", "bob_ocr")
    alice_port, bob_port = _free_udp_port(), _free_udp_port()
    while bob_port == alice_port:
        bob_port = _free_udp_port()
    bob = P2PDiscoveryService(bob_id, port=bob_port, beacon=False,
                              host="127.0.0.1", advertise_host="127.0.0.1")
    alice = P2PDiscoveryService(alice_id, port=alice_port, beacon=False,
                                host="127.0.0.1", advertise_host="127.0.0.1",
                                bootstrap=[("127.0.0.1", bob_port)])
    try:
        bob.advertise([bob_card])
        alice.advertise([alice_card])
        bob.start()
        alice.start()
        assert _wait_for(lambda: len(alice.p2p.table.alive()) == 1
                         and len(bob.p2p.table.alive()) == 1)

        by_alice = alice.discover("ocr", timeout=0.45)
        by_bob = bob.discover("translate", timeout=0.45)
        assert by_alice == [bob_card]
        assert by_bob == [alice_card]
        assert card_did(by_alice[0]) == bob_id.did
        assert card_did(by_bob[0]) == alice_id.did
        assert verify_card(by_alice[0], require_endpoint=True) == (True, "ok")
        assert verify_card(by_bob[0], require_endpoint=True) == (True, "ok")

        view = alice.snapshot()
        assert view["running"] is True
        assert view["network"]["mode"] == "lan-bootstrap-gossip"
        assert view["network"]["nat_traversal"] is False
        assert view["network"]["relay"] is False
        assert view["local_cards"][0]["service_id"] == "alice_translate"
        assert view["discovered"][0]["card"] == bob_card
        assert view["peers"][0]["verified_envelope_key"] is True
        assert alice.p2p.advert["protocol"] == ADVERT_PROTOCOL
        # The UDP advert is a compact index, never the complete Agent Card.
        assert "sovereign" not in str(alice.p2p.advert)
    finally:
        alice.stop()
        bob.stop()
        alice_runtime.stop()
        bob_runtime.stop()


def test_background_probe_keeps_bounded_signed_udp_latency_history():
    alice_id, bob_id = Identity.generate(), Identity.generate()
    alice_port, bob_port = _free_udp_port(), _free_udp_port()
    while bob_port == alice_port:
        bob_port = _free_udp_port()
    bob = P2PDiscoveryService(
        bob_id, port=bob_port, beacon=False, host="127.0.0.1",
        advertise_host="127.0.0.1", probe_interval=0.08, probe_timeout=0.4)
    alice = P2PDiscoveryService(
        alice_id, port=alice_port, beacon=False, host="127.0.0.1",
        advertise_host="127.0.0.1", bootstrap=[("127.0.0.1", bob_port)],
        probe_interval=0.08, probe_timeout=0.4)
    try:
        bob.start()
        alice.start()
        assert _wait_for(lambda: bool(alice.snapshot()["peers"]), timeout=2.0)
        assert _wait_for(
            lambda: alice.snapshot()["peers"][0]["rtt_ms"] is not None,
            timeout=2.0)
        peer = alice.snapshot()["peers"][0]
        assert peer["reachable"] is True
        assert peer["rtt_ms"] >= 0
        assert 1 <= len(peer["history"]) <= 30
        assert peer["history"][-1]["reachable"] is True
    finally:
        alice.stop()
        bob.stop()


def test_only_own_valid_supply_cards_can_be_advertised():
    identity = Identity.generate()
    runtime, card = _runtime_card(identity, "OCR", "ocr", "ocr")
    service = P2PDiscoveryService(identity, port=_free_udp_port(), beacon=False,
                                  host="127.0.0.1")
    try:
        tampered = copy.deepcopy(card)
        tampered["name"] = "篡改后"
        with pytest.raises(ValueError, match="自证"):
            service.advertise(tampered)

        other = Identity.generate()
        foreign = sign_card(other, copy.deepcopy(card))
        with pytest.raises(ValueError, match="其他节点"):
            service.advertise(foreign)

        consumer = copy.deepcopy(card)
        consumer["x-a2n"]["projection"]["role"] = "consumer"
        consumer = sign_card(identity, consumer)
        with pytest.raises(ValueError, match="消费投影"):
            service.advertise(consumer)

        service.advertise(card)
        assert service.cards() == [card]
        service.start().start()  # lifecycle is idempotent
        service.stop()
        service.stop()
    finally:
        service.stop()
        runtime.stop()


def test_discovery_rejects_a_card_that_does_not_match_gossiped_hash():
    provider_id, consumer_id = Identity.generate(), Identity.generate()
    provider_runtime, provider_card = _runtime_card(provider_id, "OCR", "ocr", "ocr")
    provider_port, consumer_port = _free_udp_port(), _free_udp_port()
    while consumer_port == provider_port:
        consumer_port = _free_udp_port()
    provider = P2PDiscoveryService(provider_id, port=provider_port, beacon=False,
                                   host="127.0.0.1", advertise_host="127.0.0.1")
    bad = copy.deepcopy(provider_card)
    bad["name"] = "合法卡之外的内容"
    # Even a newly valid signature is insufficient when it differs from the hash
    # committed in the signed discovery envelope.
    bad = sign_card(provider_id, bad)
    consumer = P2PDiscoveryService(
        consumer_id, port=consumer_port, beacon=False, host="127.0.0.1",
        advertise_host="127.0.0.1", bootstrap=[("127.0.0.1", provider_port)],
        fetcher=lambda _endpoint, _timeout: copy.deepcopy(bad),
    )
    try:
        provider.advertise(provider_card)
        provider.start()
        consumer.start()
        assert _wait_for(lambda: len(consumer.p2p.table.alive()) == 1)
        assert consumer.discover("ocr", timeout=0.4) == []
        assert consumer.snapshot()["discovered"] == []
    finally:
        consumer.stop()
        provider.stop()
        provider_runtime.stop()


def test_default_card_fetch_is_pinned_to_signed_offer_source():
    # A peer at 127.0.0.1 must not make the node fetch a router, metadata
    # service, or any other address named in its signed advert.
    assert P2PDiscoveryService._fetch_card(
        "http://192.0.2.10/a2a/evil", 0.1, "127.0.0.1") is None


def test_many_same_skill_cards_use_truncated_mtu_safe_offer_not_full_catalog():
    identity = Identity.generate()
    runtime = NodeRuntime(identity.did, signer=lambda value: sign_card(identity, value))
    runtime.start_gateway()
    service = P2PDiscoveryService(identity, port=_free_udp_port(), beacon=False,
                                  host="127.0.0.1")
    try:
        cards = []
        for index in range(20):
            sid = f"ocr_{index}"
            runtime.mount_callable(_source(f"OCR {index}", "ocr"), lambda value: value,
                                   service_id=sid)
            cards.append(runtime.project_binding(sid))
        service.advertise(cards)
        assert service.p2p.advert == {"protocol": ADVERT_PROTOCOL, "card_count": 20}
        offer = service._offer_for_skill("ocr")
        assert offer["truncated"] is True and offer["total"] == 20
        assert offer["cards"]
        assert len(json.dumps(offer, ensure_ascii=False, separators=(",", ":")).encode()) <= 700
        envelope = Envelope(identity.did, OFFER, {
            "query_id": "msg_test", "skill": "ocr", "did": identity.did,
            "skills": ["ocr"], "advert": offer,
            "pub": service.p2p.pub_b64(),
        }, ttl=1).sign(identity)
        assert len(envelope.to_bytes()) <= 1400
        # Repeated searches rotate through the catalog; insertion order must not
        # make the same few Agents permanent winners.
        seen = {item["service_id"] for item in offer["cards"]}
        first = offer["cards"][0]["service_id"]
        second_offer = service._offer_for_skill("ocr")
        assert second_offer["cards"][0]["service_id"] != first
        seen.update(item["service_id"] for item in second_offer["cards"])
        for _ in range(19):
            next_offer = service._offer_for_skill("ocr")
            seen.update(item["service_id"] for item in next_offer["cards"])
        assert seen == {f"ocr_{index}" for index in range(20)}
    finally:
        service.stop()
        runtime.stop()


def test_discovery_timeout_is_a_total_budget_not_per_card():
    provider_id, consumer_id = Identity.generate(), Identity.generate()
    runtime, card = _runtime_card(provider_id, "Slow OCR", "ocr", "slow_ocr")

    def slow_fetch(_endpoint, _timeout):
        time.sleep(0.45)
        return card

    consumer = P2PDiscoveryService(
        consumer_id, port=_free_udp_port(), beacon=False, host="127.0.0.1",
        fetcher=slow_fetch)
    descriptor = {"service_id": "slow_ocr", "endpoint": card["url"],
                  "card_hash": card_hash(card), "skills": ["ocr"]}
    consumer.p2p.table.upsert(provider_id.did, "127.0.0.1", 9701,
                              provider_id.pub_raw)
    consumer.p2p.query = lambda _skill, timeout: [{
        "did": provider_id.did, "_source_host": "127.0.0.1",
        "advert": {"protocol": ADVERT_PROTOCOL, "cards": [descriptor]},
    }]
    try:
        started = time.monotonic()
        assert consumer.discover("ocr", timeout=0.12) == []
        assert time.monotonic() - started < 0.3
    finally:
        consumer.stop()
        runtime.stop()
