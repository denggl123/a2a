"""FIND preserves exact signed declarations through lease refresh, within bounds."""
import copy
import base64
import os
import time
import uuid
from contextlib import ExitStack

import pytest

from a2n_p2p import Identity
from a2n_node.card import build_card, card_hash, sign_card, verify_card
from a2n_node.coord_identity import coord_envelope
from a2n_node.coord_service import PublicCoordination
from a2n_sdk.coordination import CoordFailure, query_fingerprint, Budget, SearchSpec
from a2n_sdk.storage import LocalStore


def setup_public():
    provider, caller = Identity.generate(), Identity.generate()
    card = build_card(provider, name="Inspector", skills=["inspect"], host="127.0.0.1", port=9001)
    card['x-a2n']['projection'] = {'role': 'supply', 'node_did': provider.did, 'service_id': 'inspector'}
    card['x-a2n']['experience'] = {'lease_revision': 1}
    cards = [sign_card(provider, card)]
    public = PublicCoordination(provider, LocalStore(), endpoint=lambda: 'http://127.0.0.1:9001',
                                cards=lambda: cards, services=lambda: {'discovery': True})
    def request(op, body):
        return public.handle(op, coord_envelope(caller, op, body, recipient_did=provider.did,
                             request_id=uuid.uuid4().hex))['body']
    return provider, cards, public, request


def find(request):
    return request('FIND', {'skill': 'inspect', 'page_size': 10,
                   'query_fingerprint': query_fingerprint('inspect', {}, 10)})['offers'][0]


def test_find_serves_exact_signed_card_after_lease_refresh_and_rejects_wrong_key():
    provider, cards, public, request = setup_public()
    original = copy.deepcopy(cards[0])
    offer = find(request)
    cards[0]['x-a2n']['experience']['lease_revision'] = 2
    cards[0] = sign_card(provider, cards[0])
    assert card_hash(cards[0]) != offer['card_hash']
    key = {'provider_did': provider.did, 'service_id': 'inspector'}
    served = request('GET_CARD', {'key': key, 'card_hash': offer['card_hash']})
    assert served['card'] == original and verify_card(served['card'])[0]
    assert served['card_hash'] == card_hash(original)
    with pytest.raises(CoordFailure) as failure:
        request('GET_CARD', {'key': {**key, 'service_id': 'another'}, 'card_hash': offer['card_hash']})
    assert failure.value.status == 404


def test_pinned_cards_expire_and_cache_has_byte_bound(monkeypatch):
    provider, cards, public, request = setup_public()
    offer = find(request)
    digest = offer['card_hash']
    cards[0]['x-a2n']['experience']['lease_revision'] = 2
    cards[0] = sign_card(provider, cards[0])
    public._snapshot_cards[digest]['expires'] = time.time() - 1
    with pytest.raises(CoordFailure):
        request('GET_CARD', {'key': {'provider_did': provider.did, 'service_id': 'inspector'}, 'card_hash': digest})
    monkeypatch.setattr('a2n_node.coord_service.MAX_SNAPSHOT_CARD_BYTES', 4000)
    for index in range(10):
        cards[0]['x-a2n']['experience']['lease_revision'] = index + 3
        cards[0] = sign_card(provider, cards[0])
        find(request)
    assert public._snapshot_card_bytes <= 4000
    assert digest not in public._snapshot_cards
    assert len(public._snapshot_cards) < 10


def test_expired_remote_cursor_restarts_same_source_without_agent_calls(tmp_path):
    from a2n_node.daemon import Daemon
    from a2n_node.protection import EnvironmentProtector
    with ExitStack() as stack:
        def node(path, roots=()):
            protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
            daemon = Daemon(path, port=0, protector=protector, public_nodes=roots,
                            coord_allow_networks=('127.0.0.0/8',)).start()
            daemon.management.discovery_public_base = daemon.runtime.local_base_url
            stack.callback(daemon.stop)
            return daemon
        seller = node(tmp_path / 'seller')
        buyer = node(tmp_path / 'buyer', roots=[seller.runtime.local_base_url])
        executed = []
        for index in range(15):
            seller.runtime.mount_callable({'name': str(index), 'version': '1', 'skills': [{'id': 'inspect'}]},
                lambda payload: executed.append(payload), service_id='inspector-' + str(index))
        original = seller.public_coordination._dispatch
        expired = []
        def dispatch(op, body, sender):
            if op == 'FIND' and body.get('cursor') and not expired:
                snapshot_id = body['cursor'].rsplit(':', 1)[0]
                seller.public_coordination._snapshots[snapshot_id]['expires'] = time.time() - 1
                expired.append(snapshot_id)
            return original(op, body, sender)
        monkey = pytest.MonkeyPatch()
        stack.callback(monkey.undo)
        monkey.setattr(seller.public_coordination, '_dispatch', dispatch)
        search = buyer.coordination.start(SearchSpec('inspect', preferences={'min_candidates': 256},
            round_budget=Budget(64, 4194304, 10000, 3, 256, 1, 2)), 'cursor-expiry')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snap = buyer.coordination.get(search.search_id)
            if snap.state != 'RUNNING':
                break
            time.sleep(.025)
        page = buyer.coordination.candidates(search.search_id, limit=100)
        assert expired and snap.state == 'FRONTIER_EXHAUSTED'
        assert len(page['items']) == 15
        assert executed == []
        assert snap.budget_used['remote_operations'] > 20
        assert any(e['error'].startswith('CURSOR_EXPIRED') for e in page['errors'])
