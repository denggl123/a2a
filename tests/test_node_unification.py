"""Business migration and local control against the actual node assembly."""
import base64
import copy
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.client import NodeClient, NodeRequestError
from a2n_sdk.cards import validate_card
from a2n_sdk.pricing import is_free, quote
from a2n_sdk.trials import summarize

def card():
    return {"name": "one", "skills": [{"id": "one"}], "x-a2n": {"uid": "11111111-1111-4111-8111-111111111111"}}

@pytest.fixture
def node(tmp_path):
    instance = Daemon(tmp_path / "node", port=0,
        protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())).start()
    yield instance
    instance.stop()

def client(node):
    return NodeClient(node.runtime.local_base_url, token=node.runtime.management_token)

def test_single_listing_lifecycle_and_legacy_publication_archive(node):
    api = client(node)
    mounted = api.mount(card(), "http://127.0.0.1:9/invoke", listed=False)
    sid = mounted["service_id"]
    assert not api.snapshot()["published"]
    assert api.publish(sid)["listed"]
    assert len(api.snapshot()["published"]) == 1
    assert api.publish(sid)["service_id"] == sid
    api.unpublish(sid)
    assert not api.snapshot()["published"]
    # Old publication intent is retained as provenance and translated once.
    old = {"agent_id": "retired-platform-id", "state": "published"}
    node.store.put("publications", sid, old)
    home, protector = node.home, node.store.protector
    node.stop()
    restored = Daemon(home, port=0, protector=protector).start()
    try:
        assert restored.runtime.bindings.get(sid).metadata["listed"] is True
        assert not restored.store.items("publications")
        assert restored.store.get("migration_archive", "publication:" + sid) == old
    finally:
        restored.stop()

def test_machine_client_executes_and_replays_the_same_local_task(node):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            hits.append(1)
            self.send_response(200); self.send_header("Content-Length", "12"); self.end_headers()
            self.wfile.write(b'{"value":42}')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = client(node)
        sid = api.mount(card(), f"http://127.0.0.1:{server.server_port}/") ["service_id"]
        one = api.call(sid, "one", {}, task_id="stable")
        two = api.call(sid, "one", {}, task_id="stable")
        assert one["id"] == two["id"] == "stable" and hits == [1]
        assert api.get_task(sid, "stable")["status"]["state"] == "completed"
        assert isinstance(api.request("GET", "/v1/accounts"), list)
        assert api.request("GET", "/v1/quality?scope=" + sid)["sample_count"] == 1
    finally:
        server.shutdown(); server.server_close()

def test_protected_stop_and_rotating_control_secret(node):
    with pytest.raises(NodeRequestError) as exc:
        NodeClient(node.runtime.local_base_url).request("POST", "/v1/node/stop", {})
    assert exc.value.status == 401
    assert not node.stop_requested.is_set()
    assert node.store.get("runtime_control", "access")["token"] == node.runtime.management_token
    assert client(node).request("POST", "/v1/node/stop", {})["stopping"]
    assert node.stop_requested.is_set()

@pytest.mark.parametrize("url", ["https://remote.example", "http://127.0.0.1@remote.example", "http://localhost/redirect", "http://127.0.0.1?token=abc"])
def test_machine_control_stays_loopback(url):
    with pytest.raises(ValueError): NodeClient(url)

def test_integer_price_preserves_large_values_and_explicit_free():
    assert quote([{"key": "call_count", "amount": 1}], {"call_count": 9007199254740993})["amount_minor"] == 9007199254740993
    c = card()
    assert not is_free(c, "one")
    c["x-a2n"]["price_book"] = {"one": {"CNY": {"dimensions": [{"key": "call_count", "amount": 0}]}}}
    validate_card(c)
    assert is_free(c, "one")
    with pytest.raises(ValueError): quote([{"key": "call_count", "amount": 3}], {"call_count": float("nan")})

def test_sample_redacts_structured_keys_before_serialization_and_clipping():
    removed = []
    summary = summarize({"token": "arbitrary-credential", "email": "test@example.com", "work": "ok"}, removed)
    assert "arbitrary-credential" not in summary and "test@example.com" not in summary
    assert "token" in removed and "email" in removed

def test_aggregation_never_retries_an_ambiguous_remote_failure():
    from a2n_sdk.aggregate import Aggregator
    class Broken:
        def __init__(self): self.calls = []
        def call_agent(self, agent_id, **kwargs):
            self.calls.append(agent_id)
            raise TimeoutError("remote effect unknown")
    broken = Broken()
    aggregator = Aggregator(broken, [{"agent_id": "a"}, {"agent_id": "b"}])
    with pytest.raises(TimeoutError): aggregator.call("one", {})
    assert broken.calls == ["a"]

def test_retired_points_are_never_interpreted_as_cny_prices():
    from a2n_sdk.pricing import price_book
    with pytest.raises(ValueError, match="积分"):
        price_book({"x-a2n": {"price_hint": {"one": {"amount": 1, "unit": "point_per_call"}}}})
    assert price_book({"x-a2n": {"price_hint": {"one": {"amount": 1, "unit": "fen_per_call"}}}})["one"]["CNY"][0]["amount"] == 1

def test_migrated_media_precision_and_registration():
    from a2n_sdk.media import MEDIA, UnknownMedium, money, register_medium, to_minor
    try:
        register_medium({"code": "unit-test", "currency": "TEST", "exponent": 2})
        assert to_minor("1.005", "unit-test") == 101
        assert money(9007199254740993, "unit-test")["display"] == "90071992547409.93 TEST"
        with pytest.raises(UnknownMedium): to_minor(1, "missing")
    finally:
        MEDIA.pop("unit-test", None)
