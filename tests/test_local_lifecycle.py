"""Local-node resource lifecycle stays consistent across memory, disk and platform."""
from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from a2n_sdk.management import RuntimeManagement
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
