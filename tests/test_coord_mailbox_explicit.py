"""A LAN-visible node must still answer public discovery through an outbound mailbox."""
import base64
import os
import time

from a2n_node.daemon import Daemon
from a2n_node.coord_identity import verify_coord_envelope
from a2n_node.protection import EnvironmentProtector


def test_explicit_mailbox_with_private_public_entry(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / 'root', port=0, protector=protector,
                  coord_allow_networks=['127.0.0.1/32']).start()
    child = None
    try:
        child = Daemon(tmp_path / 'child', port=0, protector=protector,
                       discovery_public_base='http://127.0.0.1:18991',
                       coord_mailbox_nodes=[root.runtime.local_base_url]).start()
        deadline = time.monotonic() + 8
        while not child.coord_mailbox.registered and time.monotonic() < deadline:
            time.sleep(.05)
        assert child.coord_mailbox.registered, child.coord_mailbox.last_error
        record = child.public_coordination.record()
        assert record['coord_routes'][0]['channel_type'] == 'coord_mailbox'
        assert root.store.get('coord_nodes', child.identity.did)['node_did'] == child.identity.did
        session = root.coord_network.connect(record, time.monotonic() + 3)
        request = root.coord_network._envelope('PROBE', {'nonce': 'explicit-mailbox'}, child.identity.did)
        answer = root.coord_network.exchange(session, request, 65536)['envelope']
        assert answer['sender_did'] == child.identity.did
        assert answer['type'] == 'PROBE_RESULT'
        assert answer['body']['nonce'] == 'explicit-mailbox'
        missing = root.coord_network._envelope('GET_CARD', {
            'key': {'provider_did': child.identity.did, 'service_id': 'svc_missing'},
            'card_hash': '0' * 64}, child.identity.did)
        error = root.public_coordination.mailbox_submit(child.identity.did, missing, 3)
        assert error['type'] == 'ERROR' and error['body']['code'] == 'NOT_FOUND'
        assert error['in_reply_to'] == missing['request_id']
        assert verify_coord_envelope(error, now=time.time(), recipient_did=root.identity.did)
        assert child.coord_mailbox.registered
        assert child.public_coordination.record()['coord_routes'][0]['channel_type'] == 'coord_mailbox'
        session = root.coord_network.connect(record, time.monotonic() + 3)
        probe = root.coord_network._envelope('PROBE', {'nonce': 'after-error'}, child.identity.did)
        assert root.coord_network.exchange(session, probe, 65536)['envelope']['body']['nonce'] == 'after-error'
    finally:
        if child:
            child.stop()
        root.stop()
