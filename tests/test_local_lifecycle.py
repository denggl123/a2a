"""Local-node resource lifecycle stays consistent across memory, disk and platform."""
from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from a2n_sdk.management import RuntimeManagement
from a2n_sdk.platform_runtime import RuntimePlatformBridge
from a2n_sdk.runtime import NodeRuntime
from a2n_sdk.storage import LocalStore


def card(name="Echo", url="http://127.0.0.1:9999/a2a/echo"):
    return {"name": name, "url": url, "version": "1.0.0",
            "skills": [{"id": "echo", "name": "Echo"}]}


def test_remove_resources_checks_account_references_and_does_not_restore():
    store = LocalStore()
    runtime = NodeRuntime("did:a2n:lifecycle")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store)
    try:
        management.command("/v1/accounts", {
            "account_id": "cloud", "label": "Cloud",
            "headers": {"Authorization": "Bearer secret"}})
        _, mounted = management.command("/v1/bindings/http", {
            "card": card("Supply"), "endpoint": card()["url"],
            "account_ref": "cloud"})
        _, projected = management.command("/v1/projections", {
            "card": card("Imported"), "account_ref": "cloud"})

        with pytest.raises(ValueError, match="仍被 Agent 使用") as error:
            management.command("/v1/accounts/remove", {"account_id": "cloud"})
        assert mounted["service_id"] in str(error.value)
        assert projected["projection_id"] in str(error.value)

        management.command("/v1/projections/remove", {
            "projection_id": projected["projection_id"]})
        assert runtime.card_for(projected["projection_id"]) is None
        assert projected["projection_id"] not in store.items("projections")

        with pytest.raises(ValueError, match=mounted["service_id"]):
            management.command("/v1/accounts/remove", {"account_id": "cloud"})
        management.command("/v1/bindings/remove", {
            "service_id": mounted["service_id"]})
        management.command("/v1/accounts/remove", {"account_id": "cloud"})
        assert not runtime.snapshot()["bindings"]
        assert not runtime.snapshot()["projections"]
        assert not runtime.snapshot()["accounts"]
        assert not store.items("bindings")
        assert not store.items("projections")
        assert not store.items("accounts")
    finally:
        runtime.stop()

    restarted = NodeRuntime("did:a2n:lifecycle")
    restarted.start_gateway()
    try:
        RuntimeManagement(restarted, store).restore()
        assert restarted.snapshot()["bindings"] == []
        assert restarted.snapshot()["projections"] == []
        assert restarted.snapshot()["accounts"] == []
    finally:
        restarted.stop()
        store.close()


def test_discovery_combines_optional_channels_but_only_import_persists_locally():
    p2p_card = card("P2P", "http://10.0.0.2:9000/a2a/p2p")
    platform_card = card("Indexed", "https://index.example/a2a/indexed")

    class Discovery:
        def discover(self, skill, timeout=2):
            assert skill == "echo" and timeout == 1.0
            return [p2p_card]

        def snapshot(self):
            return {"running": True, "peers": [], "stats": {}}

        def advertise(self, _cards):
            return []

    class Publisher:
        def search(self, skill, limit=20):
            assert skill == "echo" and limit == 12
            return [{"card": platform_card, "source": "platform",
                     "headers": {"X-Principal": "did:a2n:consumer"}}]

        def snapshot(self):
            return []

    store = LocalStore()
    runtime = NodeRuntime("did:a2n:consumer")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store, publisher=Publisher(),
                                   discovery=Discovery())
    try:
        status, result = management.command(
            "/v1/discovery/search", {"skill": "echo", "timeout": 1, "limit": 12})
        assert status == 200
        assert [item["source"] for item in result["results"]] == ["p2p", "platform"]
        assert result["count"] == 2
        assert store.items("projections") == {}

        _, imported = management.command("/v1/projections", {
            "card": result["results"][1]["card"],
            "headers": result["results"][1]["headers"],
        })
        saved = store.get("projections", imported["projection_id"])
        assert saved["headers"] == {"X-Principal": "did:a2n:consumer"}
    finally:
        runtime.stop()
        store.close()


class FakePublisher:
    def __init__(self):
        self.handles = {}
        self.unpublished = []
        self.fail_publish = False
        self.fail_unpublish = False

    def publish(self, service_id, *, agent_id=None):
        if self.fail_publish:
            raise OSError("platform offline during publish")
        handle = SimpleNamespace(service_id=service_id,
                                 agent_id=agent_id or "ag_" + service_id)
        self.handles[service_id] = handle
        return handle

    def is_published(self, service_id):
        return service_id in self.handles

    def unpublish(self, service_id, *, agent_id=None):
        if self.fail_unpublish:
            raise OSError("platform offline during unpublish")
        handle = self.handles.pop(service_id, None)
        resolved = handle.agent_id if handle else agent_id
        self.unpublished.append((service_id, resolved))
        return {"service_id": service_id, "agent_id": resolved,
                "visibility": "private"}

    def snapshot(self):
        return [{"service_id": sid, "agent_id": h.agent_id,
                 "connected": True, "last_error": None}
                for sid, h in self.handles.items()]


def test_published_binding_requires_downlist_and_stale_restore_cannot_revive_it():
    store, publisher = LocalStore(), FakePublisher()
    runtime = NodeRuntime("did:a2n:published")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store, publisher=publisher)
    try:
        _, mounted = management.command("/v1/bindings/http", {
            "card": card("Supply"), "endpoint": card()["url"]})
        sid = mounted["service_id"]
        _, published = management.command("/v1/publish", {"service_id": sid})
        agent_id = published["agent_id"]
        assert store.get("publications", sid) == {
            "service_id": sid, "agent_id": agent_id, "state": "published"}

        with pytest.raises(ValueError, match="仍在平台上架"):
            management.command("/v1/bindings/remove", {"service_id": sid})
        assert runtime.bindings.get(sid)
        assert store.get("binding_removals", sid) is None

        status, result = management.command("/v1/unpublish", {"service_id": sid})
        assert status == 200 and result["visibility"] == "private"
        assert publisher.unpublished == [(sid, agent_id)]
        assert store.get("publications", sid) is None
        assert runtime.bindings.get(sid)  # down-listing does not uninstall

        # A daemon restore iteration may have captured the old key immediately
        # before deletion.  It must not resurrect the listing silently.
        with pytest.raises(ValueError, match="刚刚下架"):
            management.command("/v1/publish", {"service_id": sid})
        management.command("/v1/publish", {"service_id": sid, "force": True})
        status, result = management.command("/v1/bindings/remove", {
            "service_id": sid, "unpublish": True})
        assert status == 200 and result == {
            "removed": True, "service_id": sid, "unpublished": True}
        assert not publisher.is_published(sid)
        assert store.get("publications", sid) is None
        assert store.get("bindings", sid) is None
        assert runtime.bindings.get(sid) is None
    finally:
        runtime.stop()
        store.close()


def test_publication_intent_survives_remote_failure_for_idempotent_retry():
    store, publisher = LocalStore(), FakePublisher()
    runtime = NodeRuntime("did:a2n:durable-publication")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store, publisher=publisher)
    try:
        _, mounted = management.command("/v1/bindings/http", {
            "card": card("Supply"), "endpoint": card()["url"]})
        sid = mounted["service_id"]

        publisher.fail_publish = True
        with pytest.raises(OSError, match="publish"):
            management.command("/v1/publish", {"service_id": sid})
        pending = store.get("publications", sid)
        assert pending["state"] == "publishing"
        assert pending["agent_id"].startswith("ag_")

        publisher.fail_publish = False
        management.command("/v1/publish", {"service_id": sid})
        assert store.get("publications", sid)["state"] == "published"

        publisher.fail_unpublish = True
        with pytest.raises(OSError, match="unpublish"):
            management.command("/v1/unpublish", {"service_id": sid})
        assert store.get("publications", sid)["state"] == "unpublishing"

        publisher.fail_unpublish = False
        management.command("/v1/unpublish", {"service_id": sid})
        assert store.get("publications", sid) is None
    finally:
        runtime.stop()
        store.close()


def test_pausing_a_published_binding_hides_platform_and_resume_republishes():
    store, publisher = LocalStore(), FakePublisher()
    runtime = NodeRuntime("did:a2n:pause-publication")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store, publisher=publisher)
    try:
        _, mounted = management.command("/v1/bindings/http", {
            "card": card("Supply"), "endpoint": card()["url"]})
        sid = mounted["service_id"]
        management.command("/v1/publish", {"service_id": sid})

        _, paused = management.command(
            "/v1/bindings/state", {"service_id": sid, "enabled": False})
        assert paused["platform_state"] == "paused"
        assert runtime.bindings.get(sid).enabled is False
        assert not publisher.is_published(sid)
        assert store.get("publications", sid)["state"] == "paused"
        with pytest.raises(ValueError, match="已暂停"):
            management.command("/v1/publish", {"service_id": sid})

        _, resumed = management.command(
            "/v1/bindings/state", {"service_id": sid, "enabled": True})
        assert resumed["platform_state"] == "published"
        assert runtime.bindings.get(sid).enabled is True
        assert publisher.is_published(sid)
        assert store.get("publications", sid)["state"] == "published"
    finally:
        runtime.stop()
        store.close()


def test_failed_platform_pause_keeps_durable_hidden_intent_and_local_pause():
    store, publisher = LocalStore(), FakePublisher()
    runtime = NodeRuntime("did:a2n:pause-recovery")
    runtime.start_gateway()
    management = RuntimeManagement(runtime, store, publisher=publisher)
    try:
        _, mounted = management.command("/v1/bindings/http", {
            "card": card("Supply"), "endpoint": card()["url"]})
        sid = mounted["service_id"]
        management.command("/v1/publish", {"service_id": sid})
        publisher.fail_unpublish = True
        with pytest.raises(OSError, match="unpublish"):
            management.command(
                "/v1/bindings/state", {"service_id": sid, "enabled": False})
        assert runtime.bindings.get(sid).enabled is False
        assert store.get("bindings", sid)["enabled"] is False
        assert store.get("publications", sid)["state"] == "pausing"
    finally:
        runtime.stop()
        store.close()


def test_platform_bridge_downlists_before_stopping_tunnel():
    clients = []

    class FakeClient:
        def __init__(self, base_url, principal=None):
            self.base_url, self.principal = base_url, principal
            self.node_id = None
            self.listings = []
            clients.append(self)

        def register(self, _card, _visibility, _limit):
            self.node_id = "ag_supply"
            return {"agent_id": self.node_id}

        def set_listing(self, agent_id, visibility=None, discover_limit=None):
            self.listings.append((agent_id, visibility))
            return {"agent_id": agent_id, "visibility": visibility}

        def heartbeat(self, _report):
            return {"ok": True}

    class FakeTunnel:
        def __init__(self, client, on_task, local_base=None, attest_fn=None):
            self.client, self.on_task, self.local_base = client, on_task, local_base
            self.connected = threading.Event()
            self.last_error = None
            self.stopped = False

        def start(self):
            self.connected.set()

        def stop(self):
            self.stopped = True
            self.connected.clear()

    runtime = NodeRuntime("did:a2n:bridge-lifecycle")
    bridge = RuntimePlatformBridge(runtime, client_factory=FakeClient,
                                   tunnel_factory=FakeTunnel)
    try:
        runtime.mount_callable(card("Supply"), lambda payload: payload,
                               service_id="supply")
        handle = bridge.publish("supply")
        result = bridge.unpublish("supply")
        assert result == {"service_id": "supply", "agent_id": "ag_supply",
                          "visibility": "private"}
        assert clients[0].listings == [("ag_supply", "private")]
        assert handle.tunnel.stopped
        assert bridge.snapshot() == []

        # A persisted publication can still be down-listed when its old tunnel
        # is not active (for example directly after a restart).
        bridge.unpublish("old", agent_id="ag_old")
        assert clients[-1].listings == [("ag_old", "private")]
    finally:
        bridge.stop()
        runtime.stop()


def test_platform_bridge_recovers_deterministic_publish_after_crash():
    events = []

    class RecoveringClient:
        exists = False

        def __init__(self, _base_url, principal=None):
            self.node_id = None

        def update_card(self, agent_id, _card):
            events.append(("update", agent_id))
            if not self.exists:
                raise RuntimeError("PUT /card -> 404: missing")
            return {"agent_id": agent_id}

        def register(self, card, _visibility, _limit):
            type(self).exists = True
            self.node_id = card["x-a2n"]["node_id"]
            events.append(("register", self.node_id))
            return {"agent_id": self.node_id}

        def set_listing(self, agent_id, visibility=None, discover_limit=None):
            events.append(("listing", agent_id, visibility))
            return {"agent_id": agent_id}

        def heartbeat(self, _report):
            return {"ok": True}

    class FakeTunnel:
        def __init__(self, *_args, **_kwargs):
            self.connected = threading.Event()
            self.last_error = None

        def start(self):
            self.connected.set()

        def stop(self):
            self.connected.clear()

    runtime = NodeRuntime("did:a2n:recover-publish")
    runtime.start_gateway()
    runtime.mount_callable(card("Supply"), lambda value: value,
                           service_id="supply")
    expected = runtime.project_binding("supply")["x-a2n"]["node_id"]
    first = RuntimePlatformBridge(runtime, client_factory=RecoveringClient,
                                  tunnel_factory=FakeTunnel)
    second = None
    try:
        assert first.publish("supply", agent_id=expected).agent_id == expected
        first.stop()
        second = RuntimePlatformBridge(runtime, client_factory=RecoveringClient,
                                       tunnel_factory=FakeTunnel)
        assert second.publish("supply", agent_id=expected).agent_id == expected
        assert events == [
            ("update", expected), ("register", expected),
            ("update", expected), ("listing", expected, "public")]
    finally:
        first.stop()
        if second:
            second.stop()
        runtime.stop()
