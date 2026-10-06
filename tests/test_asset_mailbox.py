import base64
import os
import time

import pytest

from a2n_node.asset_mailbox import PREFIX, VERSION, AUTHOR_BYTES, PublicAssetMailbox
from a2n_node.asset_service import envelope
from a2n_node.daemon import Daemon
from a2n_node.metadata_mailbox import exchange, message
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.assets import CHUNK
from a2n_sdk.client import NodeClient
from a2n_sdk.ports import CallRequest


def test_nat_file_delivery_uses_outbound_encrypted_mailbox_without_reinvoking_agent(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / "root", port=0, protector=protector, coord_allow_networks=["127.0.0.0/8"]).start()
    provider = buyer = None
    try:
        api = NodeClient(root.runtime.local_base_url, token=root.runtime.management_token)
        api.request("POST", "/v1/public-services", {"services": {"blob_cache": True}})
        provider = Daemon(tmp_path / "provider", port=0, protector=protector,
            discovery_public_base="http://127.0.0.1:18991", coord_mailbox_nodes=[root.runtime.local_base_url]).start()
        buyer = Daemon(tmp_path / "buyer", port=0, protector=protector, coord_allow_networks=["127.0.0.0/8"]).start()
        buyer.public_directories.add(root.runtime.local_base_url)
        raw = b"private file content not visible to relay\0" + os.urandom(CHUNK * 2)
        ref = provider.assets.upload(len(raw), "application/octet-stream", [raw[i:i + CHUNK] for i in range(0, len(raw), CHUNK)])
        executions = []
        def agent(payload):
            executions.append(payload)
            return {"assets": [ref]}
        provider.runtime.mount_callable({"name": "file", "version": "1", "skills": [{"id": "file"}]}, agent, service_id="file")
        buyer.runtime.import_agent(provider.runtime.project_binding("file", public_base=provider.runtime.local_base_url), projection_id="use")
        assert buyer.calls.invoke("use", CallRequest(task_id="file-trade", payload="deliver")).ok
        facts = buyer.trade_facts.for_call("use", "file-trade")
        until = time.monotonic() + 8
        while not provider.asset_mailbox.lease and time.monotonic() < until:
            time.sleep(.05)
        assert provider.asset_mailbox.lease, provider.asset_mailbox.last_error
        actual_request = buyer.coord_network._request
        def outbound_only(endpoint, *args, **kwargs):
            if endpoint.startswith(provider.runtime.local_base_url + "/public/v1/assets/"):
                raise PermissionError("Test firewall denies direct inbound file requests")
            return actual_request(endpoint, *args, **kwargs)
        buyer.coord_network._request = outbound_only
        responses = []
        actual_handle = provider.assets.handle
        def inspect(action, request):
            answer = actual_handle(action, request)
            responses.append(answer[1])
            return answer
        provider.assets.handle = inspect
        cached = buyer.assets.fetch(facts["trade_uid"], ref["asset_id"])
        assert b"".join(buyer.assets.book.chunks(cached["asset_id"])) == raw
        assert len(responses) == 3 and len(executions) == 1
        assert buyer.assets.fetch(facts["trade_uid"], ref["asset_id"]) == cached
        assert b"private file content" not in str(responses).encode()
        with pytest.raises(ValueError, match="AUTHENTICATION_FAILED"):
            root.assets.inbox.open(responses[0])
        assert not root.store.items("assets") and not root.store.recent()
        assert not root.public_asset_mailbox._boxes[provider.identity.did]["pending"]
        # File queue saturation cannot consume metadata or mandatory coordination.
        root.public_asset_mailbox._rates.extend([(time.monotonic(), buyer.identity.did)] * 512)
        relay = {"endpoint": root.runtime.local_base_url + PREFIX.rstrip("/"), "relay_did": root.identity.did}
        inner = envelope(buyer.identity, "READ_RANGE", provider.identity.did,
            {"asset_id": ref["asset_id"], "trade_uid": facts["trade_uid"], "start": 0, "end": 9, "recipient_key": buyer.assets.inbox.public_key})
        with pytest.raises(ValueError, match="RATE_LIMITED"):
            exchange(buyer.coord_network, buyer.identity, relay, "forward", {"request": inner}, version=VERSION)
        assert buyer.coord_network.handshake({"endpoint": root.runtime.local_base_url + "/public/v1/coord"}, 16384, 2)[0]["node_did"] == root.identity.did
        api.request("POST", "/v1/public-services", {"services": {"blob_cache": False}})
        with pytest.raises(ValueError, match="OPTIONAL_SERVICE_DISABLED"):
            exchange(buyer.coord_network, buyer.identity, relay, "lookup", {"target_did": provider.identity.did}, version=VERSION)
        assert root.management.public_services["discovery"] is True
    finally:
        if buyer: buyer.stop()
        if provider: provider.stop()
        root.stop()


def test_file_mailbox_enforces_range_bytes_and_rejects_other_domains(nodes, monkeypatch):
    provider, buyer, root = nodes
    mailbox = PublicAssetMailbox(root.identity, endpoint=lambda: root.runtime.local_base_url, enabled=lambda: True)
    inner = envelope(buyer.identity, "READ_RANGE", provider.identity.did,
        {"asset_id": "as_x", "trade_uid": "tr_x", "start": 0, "end": CHUNK - 1, "recipient_key": buyer.assets.inbox.public_key})
    monkeypatch.setattr("a2n_node.asset_mailbox.AUTHOR_BYTES", CHUNK)
    mailbox.reserve(inner)
    with pytest.raises(ValueError, match="BYTE_BUDGET"):
        mailbox.reserve(inner)
    assert AUTHOR_BYTES == 16 * 1024 * 1024
    bad = {**inner["body"], "end": CHUNK}
    with pytest.raises(ValueError, match="RANGE_LIMIT"):
        mailbox.reserve(envelope(buyer.identity, "READ_RANGE", provider.identity.did, bad))
    unsupported = envelope(buyer.identity, "RUN_AGENT", provider.identity.did, {})
    request = message(buyer.identity, "FORWARD", root.identity.did, {"request": unsupported}, version=VERSION)
    assert mailbox.handle("forward", request)[1]["body"]["code"] == "UNSUPPORTED_METADATA_DOMAIN"
    request = message(buyer.identity, "FORWARD", root.identity.did, {"request": "malformed"}, version=VERSION)
    assert mailbox.handle("forward", request)[0] == 400
    # Metadata protocol cannot be used as a substitute file transport.
    request = message(buyer.identity, "FORWARD", root.identity.did, {"request": inner})
    assert root.public_metadata_mailbox.handle("forward", request)[1]["body"]["code"] == "UNSUPPORTED_METADATA_DOMAIN"


@pytest.fixture
def nodes(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    running = [Daemon(tmp_path / str(i), port=0, protector=protector).start() for i in range(3)]
    try:
        yield running
    finally:
        for node in reversed(running):
            node.stop()
