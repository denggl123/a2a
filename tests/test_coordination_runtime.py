"""Coordination acceptance: real nodes, bounded discovery, no hidden Agent calls."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import ExitStack

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.coordination import Budget, SearchSpec
from a2n_sdk.coordination_service import CoordinationService, SearchError
from a2n_sdk.ports import CallRequest
from a2n_sdk.storage import LocalStore


def wait_for(fn, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.025)
    pytest.fail("condition did not finish within its deadline")


def node(stack, home, *, public=True, roots=()):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    daemon = Daemon(home, port=0, protector=protector, public_nodes=roots,
                    coord_allow_networks=("127.0.0.0/8",)).start()
    stack.callback(daemon.stop)
    if public:
        daemon.management.discovery_public_base = daemon.runtime.local_base_url
    return daemon


def mount(daemon, executed):
    card = {"name": "report", "skills": [{"id": "report"}], "version": "1.0",
            "url": "http://127.0.0.1:9/report"}
    def call(payload):
        executed.append(payload)
        return {"report": payload}
    daemon.runtime.mount_callable(card, call, service_id="svc_report")


def http(daemon, path, body=None, *, auth=True, **headers):
    if auth:
        headers["X-A2N-Local-Token"] = daemon.runtime.management_token
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(daemon.runtime.local_base_url + path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def finished(daemon, sid):
    snap = daemon.coordination.get(sid)
    return snap if snap.state != "RUNNING" else None


def test_recursive_discovery_probe_import_and_real_signed_call(tmp_path):
    with ExitStack() as stack:
        c = node(stack, tmp_path / "c")
        b = node(stack, tmp_path / "b", roots=[c.runtime.local_base_url])
        a = node(stack, tmp_path / "a", roots=[b.runtime.local_base_url])
        executed = []
        mount(c, executed)
        wait_for(lambda: any(r["node_did"] == c.identity.did for r in b.public_coordination.known_records()))
        spec = {"skill": "report", "preferences": {"min_candidates": 1}}
        assert http(a, "/v1/coord/searches", spec, auth=False, **{"Idempotency-Key": "create"})[0] == 401
        code, started = http(a, "/v1/coord/searches", spec, **{"Idempotency-Key": "create"})
        assert code == 201
        sid = started["search_id"]
        snap = wait_for(lambda: finished(a, sid))
        assert snap.state == "SATISFIED", a.coordination.candidates(sid)
        item = a.coordination.candidates(sid)["items"][0]
        assert item["key"] == {"provider_did": c.identity.did, "service_id": "svc_report"}
        assert item["verification"] == "CARD_VERIFIED"
        assert snap.budget_used["introduction_depth"] >= 1
        assert executed == [] and c.trials.status("svc_report")["used"] == 0
        # Replay precedes revision checks and performs no second discovery.
        assert http(a, "/v1/coord/searches", spec, **{"Idempotency-Key": "create"})[1] == started
        assert http(a, "/v1/coord/searches", {**spec, "skill": "other"}, **{"Idempotency-Key": "create"})[0] == 409
        path = f"/v1/coord/searches/{sid}/route-plan"
        assert http(a, path, {"key": item["key"]}, **{"Idempotency-Key": "missing-rev"})[0] == 428
        code, plan = http(a, path, {"key": item["key"], "probe": True},
                          **{"Idempotency-Key": "plan", "If-Match": f'"{snap.revision}"'})
        assert code == 200 and plan["choices"][0]["observation"]["control_reachable"] is True, plan
        assert plan["choices"][0]["observation"]["task_reachable"] is None
        assert executed == []
        assert http(a, path, {"key": item["key"], "probe": True},
                    **{"Idempotency-Key": "plan", "If-Match": '"0"'})[1] == plan
        code, imported = http(a, "/v1/projections", {"search_id": sid, "key": item["key"]})
        assert code == 201, imported
        result = a.runtime.invoke_projection(imported["projection_id"], CallRequest(skill="report", payload="hello"))
        assert result.ok and result.metadata["transport_route"] == "signed-a2a", result
        assert len(executed) == 1
        assert c.trials.status("svc_report")["used"] == 1
        assert result.metadata["transport_target"]["card"]["url"].startswith(c.runtime.local_base_url)
        # A fresh read renews the route lease even when the signed card hash is unchanged.
        row = a.store.get(a.coordination.NS, sid)
        for candidate in row["candidates"].values():
            for route in candidate["routes"]:
                route["expires_at"] = int(time.time()) - 1
        a.store.put(a.coordination.NS, sid, row)
        s = a.coordination.get(sid)
        a.coordination.control(sid, "resume", "renew", s.revision, {"preferences": {"min_candidates": 2}})
        wait_for(lambda: finished(a, sid))
        assert a.coordination.selected_routes(sid, item["key"])


def test_outbound_mailbox_discovery_does_not_require_task_relay(tmp_path):
    with ExitStack() as stack:
        b = node(stack, tmp_path / "b")
        c = node(stack, tmp_path / "c", public=False, roots=[b.runtime.local_base_url])
        a = node(stack, tmp_path / "a", roots=[b.runtime.local_base_url])
        executed = []
        mount(c, executed)
        wait_for(lambda: c.coord_mailbox.registered)
        assert c.public_coordination.record()["coord_routes"][0]["channel_type"] == "coord_mailbox"
        budget = Budget(**{**Budget.defaults().to_dict(), "duration_ms": 15000})
        sid = a.coordination.start(SearchSpec("report", preferences={"min_candidates": 1}, round_budget=budget), "mail-search").search_id
        snap = wait_for(lambda: finished(a, sid), 20)
        assert snap.state == "SATISFIED", a.coordination.candidates(sid)
        assert a.coordination.candidates(sid)["items"][0]["key"]["provider_did"] == c.identity.did
        assert not b.management.public_services["task_relay"]
        assert not c.management.public_services["task_relay"]
        assert executed == []


class SlowSource:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
    def roots(self, skill): return [{"endpoint": "fake"}]
    def cost(self, action): return 1
    def perform(self, action, skill, byte_cap, timeout):
        self.entered.set()
        self.release.wait(2)
        return {"bytes": 32}
    def describe_card(self, card, **kwargs): raise AssertionError("no cards")


def test_pause_resume_restart_keeps_authorized_total(tmp_path):
    store = LocalStore(tmp_path / "runtime.db", EnvironmentProtector(base64.b64encode(os.urandom(32)).decode()))
    source = SlowSource()
    service = CoordinationService(store, source)
    total = Budget(**{**Budget.defaults().to_dict(), "remote_operations": 1})
    try:
        sid = service.start(SearchSpec("report", total_budget=total), "new").search_id
        assert source.entered.wait(2)
        snap = service.get(sid)
        paused = service.pause(sid, "pause", snap.revision)
        with pytest.raises(SearchError) as exc:
            service.resume(sid, "early", paused.revision)
        assert exc.value.code == "BUSY"
        source.release.set()
        wait_for(lambda: not store.get(service.NS, sid).get("inflight"))
        service.close()
        restored = CoordinationService(store, source)
        service = restored
        restored.resume(sid, "resume", restored.get(sid).revision)
        wait_for(lambda: restored.get(sid).state != "RUNNING")
        row = store.get(service.NS, sid)
        assert row["snapshot"]["state"] == "BUDGET_REACHED"
        assert row["total_ledger"]["used"]["remote_operations"] == 1
        assert row["total_ledger"]["used"]["received_bytes"] == 32
    finally:
        source.release.set()
        service.close()
        store.close()


def test_interrupted_reservation_is_retained_and_requeued(tmp_path):
    store = LocalStore(tmp_path / "runtime.db", EnvironmentProtector(base64.b64encode(os.urandom(32)).decode()))
    source = SlowSource()
    source.release.set()
    service = CoordinationService(store, source)
    try:
        sid = service.start(SearchSpec("report"), "new").search_id
        wait_for(lambda: service.get(sid).state != "RUNNING")
        service.close()
        row = store.get(service.NS, sid)
        row["snapshot"]["state"] = "RUNNING"
        row["inflight"] = {"kind": "find", "source": {"endpoint": "fake"}, "depth": 0, "cursor": ""}
        row["ledger"]["used"].update(remote_operations=2, received_bytes=65568)
        store.put(service.NS, sid, row)
        service = CoordinationService(store, source)
        assert service.get(sid).state == "PAUSED"
        row = store.get(service.NS, sid)
        assert row["frontier"][0]["kind"] == "find" and not row.get("inflight")
        assert row["ledger"]["used"]["received_bytes"] == 65568
        assert row["ledger"]["used"]["remote_operations"] == 2
    finally:
        service.close()
        store.close()


def test_auto_continue_requires_and_respects_total_budget(tmp_path):
    class Pages(SlowSource):
        def perform(self, action, skill, byte_cap, timeout):
            return {"bytes": 20, "source": action["source"], "next_cursor": str(int(action["cursor"] or 0) + 1)}
    store = LocalStore(tmp_path / "runtime.db", EnvironmentProtector(base64.b64encode(os.urandom(32)).decode()))
    service = CoordinationService(store, Pages())
    try:
        round_budget = Budget(**{**Budget.defaults().to_dict(), "remote_operations": 1})
        total = Budget(**{**Budget.defaults().to_dict(), "remote_operations": 3})
        spec = SearchSpec("report", round_budget=round_budget, auto_continue=True, total_budget=total)
        sid = service.start(spec, "auto").search_id
        snap = wait_for(lambda: service.get(sid) if service.get(sid).state != "RUNNING" else None)
        assert snap.state == "BUDGET_REACHED" and snap.round == 3
        assert store.get(service.NS, sid)["total_ledger"]["used"]["remote_operations"] == 3
    finally:
        service.close()
        store.close()


def test_referral_cannot_grant_private_network_access(tmp_path):
    with ExitStack() as stack:
        a = node(stack, tmp_path / "a")
        a.coord_network.allowed_networks = ()
        with pytest.raises(PermissionError):
            a.coord_network._request("http://127.0.0.1:9/public/v1/coord/hello", {})


def test_mandatory_discovery_optional_service_switches(tmp_path):
    with ExitStack() as stack:
        a = node(stack, tmp_path / "a")
        code, _ = http(a, "/v1/public-services", {"services": {"discovery": False}})
        assert code == 400
        code, out = http(a, "/v1/public-services", {"services": {"samples": False, "witness": False, "task_relay": False}})
        assert code == 200 and out["services"]["discovery"] is True
        request = a.coord_network._envelope("PROBE", {"nonce": "ping"}, a.identity.did)
        code, response = http(a, "/public/v1/coord/probe", request, auth=False)
        assert code == 200 and response["body"]["nonce"] == "ping"
