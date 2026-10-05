"""A locally approved route plan affects calls and cannot move an existing task."""
import copy
import time

import pytest

from a2n_sdk.adapters import FallbackTransport
from a2n_sdk.ports import AgentTarget, CallRequest, CallResponse
from a2n_sdk.runtime import NodeRuntime


def card(endpoint):
    return {"name": "test", "url": endpoint, "skills": [{"id": "test"}],
            "x-a2n": {"uid": "11111111-1111-4111-8111-111111111111",
                      "peer_protocol": "a2n-bilateral-a2a/1",
                      "projection": {"role": "supply", "node_did": "did:a2n:ag_provider", "service_id": "svc_test"}}}


class Transport:
    def __init__(self, failure=""):
        self.failure, self.sent, self.controlled = failure, [], []
    def invoke(self, target, request):
        self.sent.append(target.route)
        if self.failure:
            return CallResponse.failure("failed", state=self.failure)
        return CallResponse.success("pending", state="WORKING", metadata={"a2a_task_id": "remote"})
    def get_task(self, target, task_id, **kwargs):
        self.controlled.append((target.route, task_id))
        return CallResponse.success("complete")


def routes():
    return [{"route_id": "direct", "channel_type": "direct_a2a", "card": card("https://direct.example/a2a/svc_test"), "expires_at": int(time.time()) + 120},
            {"route_id": "relay", "channel_type": "sealed_relay_a2a", "card": card("https://relay.example/a2a/svc_test"), "expires_at": int(time.time()) + 120}]


@pytest.mark.parametrize("failure,retried", [("SIGNED_UNREACHABLE", True), ("TIMED_OUT", False), ("INVALID", False)])
def test_retry_only_before_remote_effects_on_an_approved_channel(failure, retried):
    direct, relay = Transport(failure), Transport()
    transport = FallbackTransport([("sealed-relay", relay), ("signed-a2a", direct)])
    choices = routes()
    target = AgentTarget("svc_test", choices[0]["card"], choices[0]["card"]["url"], metadata={"route_choices": choices})
    result = transport.invoke(target, CallRequest(skill="test"))
    assert direct.sent == ["https://direct.example/a2a/svc_test"]
    assert bool(relay.sent) is retried
    assert result.metadata["transport_route"] == ("sealed-relay" if retried else "signed-a2a")
    if retried:
        assert result.metadata["transport_target"]["route"] == "https://relay.example/a2a/svc_test"


def test_refresh_cannot_change_the_endpoint_of_an_accepted_task():
    direct = Transport()
    runtime = NodeRuntime("did:a2n:ag_buyer", transport=FallbackTransport([("signed-a2a", direct)]))
    runtime.start_gateway(port=0)
    choices = routes()[:1]
    try:
        item = runtime.import_agent(choices[0]["card"], route_choices=choices)
        request = CallRequest(skill="test", payload="work")
        current = runtime.invoke_projection(item.projection_id, request)
        fresh = copy.deepcopy(choices)
        fresh[0]["card"]["url"] = "https://new.example/a2a/svc_test"
        runtime.import_agent(fresh[0]["card"], projection_id=item.projection_id, route_choices=fresh)
        runtime.refresh_remote_task(item.projection_id, request, current)
        assert direct.controlled == [("https://direct.example/a2a/svc_test", "remote")]
    finally:
        runtime.stop()
