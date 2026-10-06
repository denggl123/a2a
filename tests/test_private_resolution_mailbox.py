import base64
import os
import time

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.secure_metadata import PrivateInbox
from a2n_node.resolution_gateway import envelope
from a2n_sdk.ports import CallRequest


def test_private_signed_negotiation_reaches_nat_node_without_revealing_message_to_relay(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / "root", port=0, protector=protector, coord_allow_networks=["127.0.0.0/8"]).start()
    provider = buyer = None
    try:
        provider = Daemon(tmp_path / "provider", port=0, protector=protector,
            discovery_public_base="http://127.0.0.1:18991", coord_mailbox_nodes=[root.runtime.local_base_url]).start()
        buyer = Daemon(tmp_path / "buyer", port=0, protector=protector, coord_allow_networks=["127.0.0.0/8"]).start()
        buyer.public_directories.add(root.runtime.local_base_url)
        provider.runtime.mount_callable({"name": "actual", "version": "1", "skills": [{"id": "write", "name": "write"}]},
            lambda p: p, service_id="svc")
        buyer.runtime.import_agent(provider.runtime.project_binding("svc", public_base=provider.runtime.local_base_url), projection_id="use")
        assert buyer.calls.invoke("use", CallRequest(task_id="trade", skill="write", payload="real")).ok
        dispute = buyer.resolutions.open("use", "trade", "requester", "不满意")
        message = buyer.resolutions.post(dispute["id"], body={"text": "private business negotiation"}, command_id="send")
        outbox = buyer.store.get("resolution_outbox", message["message_id"])
        buyer.store.put("resolution_outbox", message["message_id"], {**outbox, "endpoint": "http://127.0.0.1:18991"})
        until = time.monotonic() + 8
        while not provider.metadata_mailbox.lease and time.monotonic() < until:
            time.sleep(.05)
        assert provider.metadata_mailbox.lease, provider.metadata_mailbox.last_error
        captured = []
        original = root.public_metadata_mailbox._forward
        def inspect(author, body):
            captured.append(body)
            return original(author, body)
        root.public_metadata_mailbox._forward = inspect
        buyer.resolution_delivery.drain()
        row = buyer.store.get("resolution_outbox", message["message_id"])
        assert row["state"] == "DELIVERED" and row["route"] == "MAILBOX_ENCRYPTED"
        assert provider.store.get("resolution_messages", message["message_id"]) == message
        assert "private business negotiation" not in str(captured) and "sealed_message" in str(captured)
        assert not root.store.items("resolution_messages")
        sealed = PrivateInbox.seal(message, provider.public_resolution.inbox.public_key)
        request = envelope(buyer.identity, "DELIVER_SEALED", provider.identity.did, {"sealed_message": sealed})
        assert provider.public_resolution.inbox.open(request) == message
        with pytest.raises(ValueError, match="AUTHENTICATION_FAILED"):
            root.public_resolution.inbox.open(request)
        forged = {**request, "author_did": root.identity.did}
        with pytest.raises(ValueError, match="AUTHENTICATION_FAILED"):
            provider.public_resolution.inbox.open(forged)
    finally:
        if buyer: buyer.stop()
        if provider: provider.stop()
        root.stop()
