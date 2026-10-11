"""Lost first replies recover authenticated original tasks without redispatch."""
import base64
from contextlib import ExitStack
import os

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.receipt import verify
from a2n_node.relay_transport import RelayA2AUpstream
from a2n_sdk.ports import CallRequest, CallResponse
from a2n_sdk.upstream import A2AUpstream
from tests.test_selection_integration import wait_for


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_lost_initial_reply_recovers_same_signed_task(tmp_path, monkeypatch, sealed, legacy):
    with ExitStack() as stack:
        protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())

        def node(name, **kwargs):
            value = Daemon(tmp_path / name, port=0, protector=protector, **kwargs).start()
            stack.callback(value.stop)
            value.management.discovery_public_base = value.runtime.local_base_url
            return value

        relay = node("relay") if sealed else None
        if relay:
            relay.management.command("/v1/public-service", {"enabled": True})
        provider = node("provider", **({"relay_node": relay.runtime.local_base_url} if relay else {}))
        actual = []
        provider.runtime.mount_callable({"name": "Echo", "version": "1", "skills": [{"id": "echo"}]},
            lambda payload: actual.append(payload) or {"echo": payload}, service_id="svc")
        if relay:
            provider.relay_provider.request_refresh()
            card = wait_for(lambda: relay.relay_service.card(provider.identity.did, "svc"))
        else:
            card = provider.runtime.project_binding("svc")
        caller = node("caller")
        caller.runtime.import_agent(card, projection_id="use")
        upstream = RelayA2AUpstream if sealed else A2AUpstream
        original = upstream._request
        lost = []

        def lose_first_response(self, rpc, headers, deadline):
            response = original(self, rpc, headers, deadline)
            if rpc["method"] == "message/send" and not lost:
                lost.append(True)
                return CallResponse.failure("Initial reply lost after dispatch", state="DELIVERY_UNKNOWN",
                    metadata={"remote_effect_unknown": True, "replay_safe": False})
            return response

        monkeypatch.setattr(upstream, "_request", lose_first_response)
        request = CallRequest(task_id="original", skill="echo", payload={"value": 42})
        outcome = caller.calls.invoke("use", request)
        assert not outcome.ok and outcome.state == "DELIVERY_UNKNOWN"
        wait_for(lambda: len(actual) == 1)
        assert outcome.metadata["a2a_task_id"] == "original"
        if legacy:
            recorded = caller.store.task("use", "original")["outcome"]
            recorded["metadata"].pop("a2a_task_id")
            caller.store.finish("use", "original", recorded)
        recovered = caller.calls.get("use", "original", refresh_remote=True)
        assert recovered.ok and recovered.result == {"echo": {"value": 42}}, recovered.error
        assert verify(recovered.receipt)[0] and recovered.receipt["caller_did"] == caller.identity.did
        assert recovered.metadata["original_task_query"]
        assert caller.calls.invoke("use", request).result == recovered.result
        assert actual == [{"value": 42}] and len(provider.trials.samples("svc")) == 1
        assert not caller.store.items("points_transactions")
