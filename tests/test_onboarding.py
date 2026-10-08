import base64
import os
import time

import pytest
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector


def test_first_connection_verifies_node_creates_outbound_routes_and_survives_restart(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / "root", port=0, protector=protector, beacon=False, coord_allow_networks=["127.0.0.0/8"]).start()
    node = Daemon(tmp_path / "new", port=0, protector=protector, beacon=False).start()
    try:
        root.management.discovery_public_base = root.runtime.local_base_url
        root.management.public_services["task_relay"] = True
        did = node.identity.did
        status, result = node.management.command("/v1/onboarding/connect", {"address": root.runtime.local_base_url})
        assert status == 200 and result["connected"] and result["node_did"] == root.identity.did
        assert node.relay_provider and node.management.relay_provider is node.relay_provider
        until = time.monotonic() + 8
        while not node.settlement_mailbox.lease and time.monotonic() < until:
            time.sleep(.05)
        assert node.settlement_mailbox.lease, node.settlement_mailbox.last_error
        assert node.onboarding.status()["outbound_settlement"]
        assert not node.points_node.book.summary()["acceptance"]
        node.stop()
        node = Daemon(tmp_path / "new", port=0, protector=protector, beacon=False).start()
        assert node.identity.did == did and node.relay_provider.relay_node == root.runtime.local_base_url
        until = time.monotonic() + 8
        while not node.settlement_mailbox.lease and time.monotonic() < until:
            time.sleep(.05)
        assert node.settlement_mailbox.lease
    finally:
        node.stop()
        root.stop()


def test_failed_first_connection_is_not_persisted_as_a_connected_node(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(tmp_path / "new", port=0, protector=protector, beacon=False).start()
    try:
        with pytest.raises(Exception):
            node.onboarding.connect({"address": "http://127.0.0.1:19999"})
        assert not node.public_directories.bases
        assert not node.store.get("node_settings", "public_nodes")
        assert not node.store.get("node_settings", "outbound_coordination_node")
    finally:
        node.stop()
