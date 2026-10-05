"""Bound legacy adapters and explicit neighbour maintenance at real I/O seams."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from a2n_node.card import build_card, card_hash
from a2n_node.coord_neighbors import CoordinationNeighbors
from a2n_node.coord_network import CoordinationNetwork
from a2n_node.p2p_service import ADVERT_PROTOCOL, P2PDiscoveryService
from a2n_node.public_directory import PublicDirectoryClient
from a2n_p2p import Envelope, Identity, OFFER, P2PNode
from a2n_sdk.client import Client


def udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_neighbour_maintenance_refreshes_only_configured_roots_in_small_batches(monkeypatch):
    now, calls = [100.0], []
    monkeypatch.setattr('a2n_node.coord_neighbors.time.monotonic', lambda: now[0])
    network = SimpleNamespace(handshake=lambda source, cap, timeout: calls.append((source, cap, timeout)))
    roots = [f'https://n{i}.example' for i in range(35)]
    service = CoordinationNeighbors(network, lambda: roots)
    for _ in range(11):
        before = len(calls)
        service.step()
        assert len(calls) - before <= 3
    assert len(calls) == 32
    assert all(c[1:] == (16384, 1.5) for c in calls)
    service.step()
    assert len(calls) == 32
    now[0] += 45
    service.step()
    assert len(calls) == 35
    service.stop()
    service.step()
    assert len(calls) == 35


def test_bounded_udp_query_is_one_hop_to_one_peer_and_accepts_one_offer():
    identity, peer, other = Identity.generate(), Identity.generate(), Identity.generate()
    p2p = P2PNode(identity, port=udp_port(), beacon=False)
    p2p.table.upsert(peer.did, '127.0.0.1', 9701, peer.pub_raw)
    p2p.table.upsert(other.did, '127.0.0.1', 9702, other.pub_raw)
    sent = []
    def send(address, request):
        sent.append((address, request.ttl))
        for _ in range(10):
            p2p._on_offer(Envelope(peer.did, OFFER, {'query_id': request.msg_id, 'did': peer.did}), address)
        return True
    p2p.send = send
    assert len(p2p.query('report', .001, peer_did=peer.did, offer_limit=1)) == 1
    assert sent == [(('127.0.0.1', 9701), 1)]
    assert not p2p._offers and not p2p._offer_caps


def test_compatibility_discovery_fetches_only_one_card_and_rotates_peers():
    provider, consumer, other = Identity.generate(), Identity.generate(), Identity.generate()
    cards = [build_card(provider, name=str(i), skills=['report'], port=12345 + i) for i in range(4)]
    reads, queried = [], []
    discovery = P2PDiscoveryService(consumer, port=udp_port(), beacon=False,
        fetcher=lambda endpoint, timeout: reads.append(endpoint) or cards[0])
    discovery.p2p.table.upsert(provider.did, '127.0.0.1', 9701, provider.pub_raw)
    discovery.p2p.table.upsert(other.did, '127.0.0.1', 9702, other.pub_raw)
    def query(skill, timeout, **kwargs):
        queried.append(kwargs)
        return [{'did': provider.did, '_source_host': '127.0.0.1', 'advert': {
            'protocol': ADVERT_PROTOCOL, 'cards': [{'service_id': str(i), 'endpoint': c['url'],
                'card_hash': card_hash(c), 'skills': ['report']} for i, c in enumerate(cards)]}}]
    discovery.p2p.query = query
    try:
        assert len(discovery.discover('report', timeout=1, coordination_budget=65536)) == 1
        discovery.discover('report', timeout=1, coordination_budget=65536)
        assert reads == [cards[0]['url']] * 2
        assert [q['peer_did'] for q in queried] == [provider.did, other.did]
        assert all(q['offer_limit'] == 1 for q in queried)
        assert discovery._fetch_pool is None
    finally:
        discovery.stop()


@pytest.mark.parametrize('source', ['directory', 'p2p'])
def test_compatibility_http_read_rejects_oversized_wire_body(source):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '4096')
            self.end_headers()
            self.wfile.write(b' ' * 4096)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        if source == 'directory':
            with pytest.raises(ValueError, match='响应过大'):
                PublicDirectoryClient([base], timeout=1)._fetch(base, 'report', 3, max_response_bytes=1024)
        else:
            assert P2PDiscoveryService._fetch_card(base, 1, '127.0.0.1', max_bytes=1024) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_empty_udp_table_does_not_create_a_fake_discovery_root():
    network = CoordinationNetwork(Identity.generate(), SimpleNamespace(known_records=lambda: []),
        roots=lambda: [], legacy=lambda *args: [], legacy_available=lambda: False)
    assert network.roots('report') == []
