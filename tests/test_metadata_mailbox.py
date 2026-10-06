import base64
import os
import time

from a2n_node.daemon import Daemon
from a2n_node.experience_gateway import ExperienceNetwork, envelope
from a2n_node.metadata_mailbox import PREFIX, exchange, message
from a2n_node.protection import EnvironmentProtector


def test_outbound_metadata_v2_is_independent_of_coordination_and_runs_no_agent(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    root = Daemon(tmp_path / "root", port=0, protector=protector,
                  coord_allow_networks=["127.0.0.0/8"]).start()
    child = observer = None
    try:
        child = Daemon(tmp_path / "child", port=0, protector=protector,
            discovery_public_base="http://127.0.0.1:18992", coord_mailbox_nodes=[root.runtime.local_base_url]).start()
        observer = Daemon(tmp_path / "observer", port=0, protector=protector,
            coord_allow_networks=["127.0.0.0/8"]).start()
        deadline = time.monotonic() + 8
        while (not child.metadata_mailbox.lease or not child.coord_mailbox.registered) and time.monotonic() < deadline:
            time.sleep(.05)
        assert child.metadata_mailbox.lease, child.metadata_mailbox.last_error
        assert child.coord_mailbox.registered
        assert "GET_FEEDBACK" not in child.public_coordination.record()["operations"]
        relay = {"endpoint": root.runtime.local_base_url + PREFIX.rstrip("/"), "relay_did": root.identity.did}
        result = ExperienceNetwork(observer.identity, observer.coord_network).fetch(
            {"node_did": child.identity.did, "endpoint": relay["endpoint"], "mailbox": relay},
            {"kind": "provider", "provider_did": child.identity.did}, timeout=3)
        assert result["items"] == [] and result["source_did"] == child.identity.did
        assert result["operations"] == 2
        assert not child.store.recent() and not observer.store.recent()
        # This inbox cannot carry Agent tasks, payments or unknown protocol domains.
        inner = envelope(observer.identity, "RUN_AGENT", child.identity.did, {})
        request = message(observer.identity, "FORWARD", root.identity.did, {"request": inner})
        status, rejected = root.public_metadata_mailbox.handle("forward", request)
        assert status == 400 and rejected["body"]["code"] == "UNSUPPORTED_METADATA_DOMAIN"
        forged = {**request, "author_did": child.identity.did}
        assert root.public_metadata_mailbox.handle("forward", forged)[0] == 401
        capability, _ = exchange(observer.coord_network, observer.identity, relay, "capabilities", {})
        assert capability["separate_queues"] and capability["domains"] == ["a2n-experience/1", "a2n-resolution/1"]
    finally:
        if observer:
            observer.stop()
        if child:
            child.stop()
        root.stop()
