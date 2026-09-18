"""Control-plane latency is measured without tasks, skill execution or charges."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from a2n_kernel.hashing import new_id
from a2n_registry import registry
from a2n_sdk.transport import TunnelClient
from a2n_store import conn
from a2n_transport import hub
from a2n_transport.hub import TunnelHub

from .test_flow import demo_card


def _pong(channel: TunnelHub, aid: str, *, forged: bool = False):
    msg = channel.poll(channel.status(aid)["tunnel_id"], wait=1)
    assert msg["type"] == "ping"
    assert set(msg) == {"type", "req_id"}
    channel.reply(aid, msg["req_id"], {"status": 200,
                  "body": {"pong": "wrong" if forged else msg["req_id"]}})


def test_ping_roundtrip_and_cache():
    channel = TunnelHub()
    channel.open("a")
    worker = threading.Thread(target=_pong, args=(channel, "a"))
    worker.start()
    result = channel.ping("a")
    worker.join(timeout=2)
    assert result["ok"] and result["rtt_ms"] >= 0
    assert result["checked_at"]
    assert channel.ping("a")["cached"] is True
    assert channel.status("a")["queued"] == 0
    assert channel.network_status("a") == result
    result["rtt_ms"] = 999999
    assert channel.network_status("a")["rtt_ms"] != 999999


def test_timeout_is_not_zero_and_cleans_up():
    channel = TunnelHub()
    channel.open("a")
    result = channel.ping("a", timeout=.05)
    assert not result["ok"] and result["rtt_ms"] is None
    assert result["state"] == "timeout"
    assert channel.status("a")["queued"] == 0
    assert not channel._pick("a").responses
    assert channel.ping("a")["cached"] is True


def test_missing_or_stale_tunnel():
    channel = TunnelHub()
    assert channel.ping("a")["state"] == "offline"
    channel.open("a")
    channel._pick("a").last_poll -= 61
    assert channel.ping("a")["rtt_ms"] is None
    assert channel.status("a")["queued"] == 0


def test_wrong_pong_is_not_a_measurement():
    channel = TunnelHub()
    channel.open("a")
    worker = threading.Thread(target=_pong, args=(channel, "a"), kwargs={"forged": True})
    worker.start()
    result = channel.ping("a")
    worker.join(timeout=2)
    assert not result["ok"] and result["rtt_ms"] is None


def test_concurrent_probes_share_one_message():
    channel = TunnelHub()
    channel.open("a")
    results = []
    workers = [threading.Thread(target=lambda: results.append(channel.ping("a"))) for _ in range(4)]
    for worker in workers:
        worker.start()
    _pong(channel, "a")
    for worker in workers:
        worker.join(timeout=2)
    assert len(results) == 4 and all(r["ok"] for r in results)
    assert len({r["checked_at"] for r in results}) == 1
    assert channel.status("a")["queued"] == 0


def test_sdk_ping_bypasses_application_and_metering():
    calls = []
    client = SimpleNamespace(node_id="node-a", _req=lambda *args: calls.append(args))
    def forbidden(*args):
        raise AssertionError("ping must not dispatch application or metering")
    sdk = TunnelClient(client, on_task=forbidden, on_execute=forbidden, attest_fn=forbidden)
    sdk._handle_ping({"type": "ping", "req_id": "ping-1"})
    assert calls == [("POST", "/v1/nodes/node-a/tunnel/up",
                      {"req_id": "ping-1", "status": 200, "body": {"pong": "ping-1"}})]


def test_sdk_dispatches_ping_in_poll_loop():
    calls = []
    def request(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/tunnel"):
            return {"tunnel_id": "tid"}
        if "/tunnel/next?" in path:
            return {"type": "ping", "req_id": "p"}
        sdk.stop()
        return {"ok": True}
    sdk = TunnelClient(SimpleNamespace(node_id="a", _req=request), on_task=lambda m: None)
    sdk._run_once()
    assert calls[-1][2]["body"] == {"pong": "p"}


def _agent(*, visibility="public"):
    owner = "acct:ping-" + new_id("")
    agent = registry.register(owner, demo_card("ping-" + new_id("")), visibility=visibility)
    aid = agent["agent_id"]
    hub.open(aid, {"local_base": "http://127.0.0.1:secret", "provider_secret": "hidden"})
    registry.heartbeat(aid, connection={"mode": "relay"})
    return owner, aid


def test_route_and_projection_do_not_create_business_facts():
    from a2n_server.app import app
    from a2n_server.routers.registry import get_agent
    owner, aid = _agent()
    tables = ("tasks", "ledger_entries", "pay_charges", "usage_reports", "settlement_orders")
    def counts():
        return {name: conn().execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables}
    before = counts()
    worker = threading.Thread(target=_pong, args=(hub, aid))
    worker.start()
    response = TestClient(app).post(f"/v1/registry/agents/{aid}/network/ping",
                                    headers={"X-Principal": "acct:viewer-ping"})
    worker.join(timeout=2)
    assert response.status_code == 200, response.text
    assert response.json()["rtt_ms"] >= 0
    network = get_agent(aid, principal="acct:viewer-ping")["network"]
    assert set(network) == {"reachable", "mode", "rtt_ms", "checked_at", "source", "state", "reason"}
    assert "secret" not in str(network) and "127.0.0.1" not in str(network)
    assert counts() == before
    hub._pick(aid).last_poll -= 61
    offline = get_agent(aid, principal=owner)["network"]
    assert offline["reachable"] is False and offline["rtt_ms"] is None


def test_private_agents_cannot_be_probed_by_others():
    from a2n_server.app import app
    _, aid = _agent(visibility="private")
    response = TestClient(app).post(f"/v1/registry/agents/{aid}/network/ping",
                                    headers={"X-Principal": "acct:other-ping"})
    assert response.status_code == 404
    assert hub.status(aid)["queued"] == 0


def test_direct_snapshot_never_probes(monkeypatch):
    from a2n_server.routers.transport import network_summary
    from a2n_registry import reachability
    def forbidden(*args, **kwargs):
        raise AssertionError("rendering must not make outbound HTTP requests")
    monkeypatch.setattr(reachability, "probe_inbound", forbidden)
    result = network_summary({"agent_id": "direct-unmeasured", "connection": {"mode": "direct"}})
    assert result["reachable"] is False and result["rtt_ms"] is None
