"""反馈的本机路由（`/v1/feedback/*`）—— 方向与对手方身份由**服务端事实**决定。

一道门：客户端不许自报"我评的是谁、我站在哪一边"。方向从 scope 的真实角色推出来
（投影 id = 买方评供给；本机 service_id = 卖方评买方），对手方 did 从本机记录里取。
"""
from __future__ import annotations

import pytest

from a2n_sdk.management import RuntimeManagement
from a2n_sdk.storage import LocalStore

PROJ = "proj_1"
SVC = "svc_a"
TASK = "t1"


class _Binding:
    def __init__(self, service_id):
        self.service_id = service_id
        self.enabled = True


class _Bindings:
    def __init__(self, ids):
        self._ids = set(ids)

    def get(self, sid):
        return _Binding(sid) if sid in self._ids else None

    def list(self):
        return [_Binding(sid) for sid in sorted(self._ids)]


class _Runtime:
    node_did = "did:a2n:ag_me"

    def __init__(self, bindings=()):
        self.bindings = _Bindings(bindings)

    def snapshot(self):
        return {"bindings": [], "projections": []}


def _mgmt(runtime=None):
    store = LocalStore()
    return RuntimeManagement(runtime or _Runtime(), store), store


def _claim(store, scope, task_id=TASK, state="COMPLETED", request=None):
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}", request=request)
    store.finish(scope, task_id, {"state": state, "metadata": {}})


def test_buyer_scope_resolves_direction_and_counterparty_from_projection():
    mgmt, store = _mgmt()
    store.put("projections", PROJ, {"network_card": {"x-a2n": {"projection": {
        "node_did": "did:a2n:ag_seller", "service_id": SVC}}}})
    _claim(store, PROJ)
    status, rec = mgmt.command("/v1/feedback/open", {
        "scope": PROJ, "task_id": TASK, "dimensions": {"quality": 5},
        "note": "交付符合描述"})
    assert status == 201
    assert rec["direction"] == "buyer_to_seller"        # 服务端判定，不信客户端
    assert rec["provider_did"] == "did:a2n:ag_seller"
    assert rec["author_did"] == "did:a2n:ag_me"
    assert rec["service_id"] == SVC


def test_seller_scope_takes_caller_did_from_signed_request():
    mgmt, store = _mgmt(_Runtime(bindings=[SVC]))
    _claim(store, SVC, request={"metadata": {
        "_a2n_verified_peer": {"caller_did": "did:a2n:ag_buyer"}}})
    status, rec = mgmt.command("/v1/feedback/open", {
        "scope": SVC, "task_id": TASK, "dimensions": {"on_spec": 4}})
    assert status == 201
    assert rec["direction"] == "seller_to_buyer"
    assert rec["counterparty_did"] == "did:a2n:ag_buyer"
    assert rec["provider_did"] == "did:a2n:ag_me"


def test_seller_scope_without_signature_leaves_counterparty_unknown():
    mgmt, store = _mgmt(_Runtime(bindings=[SVC]))
    _claim(store, SVC)                                   # 普通 A2A，没有签名调用
    _status, rec = mgmt.command("/v1/feedback/open", {
        "scope": SVC, "task_id": TASK, "dimensions": {"cooperative": 3}})
    assert rec["counterparty_did"] == ""                 # 拿不到就说拿不到，不猜


def test_unknown_scope_and_missing_task_are_rejected():
    mgmt, store = _mgmt(_Runtime(bindings=[SVC]))
    with pytest.raises(ValueError):
        mgmt.command("/v1/feedback/open", {"scope": "nowhere", "task_id": TASK,
                                           "dimensions": {"quality": 5}})
    _claim(store, PROJ)
    with pytest.raises(ValueError):
        mgmt.command("/v1/feedback/open", {"scope": PROJ, "task_id": "nope",
                                           "dimensions": {"quality": 5}})
    with pytest.raises(ValueError):
        mgmt.command("/v1/feedback/open", {"scope": "", "task_id": "", "dimensions": {}})


def test_quality_gate_reaches_the_route():
    mgmt, store = _mgmt()
    store.put("projections", PROJ, {"network_card": {"x-a2n": {"projection": {
        "node_did": "did:a2n:ag_seller", "service_id": SVC}}}})
    _claim(store, PROJ, state="FAILED")
    with pytest.raises(ValueError):
        mgmt.command("/v1/feedback/open", {"scope": PROJ, "task_id": TASK,
                                           "dimensions": {"quality": 5}})


def test_revise_route_and_snapshot_exposure():
    mgmt, store = _mgmt()
    store.put("projections", PROJ, {"network_card": {"x-a2n": {"projection": {
        "node_did": "did:a2n:ag_seller", "service_id": SVC}}}})
    _claim(store, PROJ)
    _status, rec = mgmt.command("/v1/feedback/open", {
        "scope": PROJ, "task_id": TASK, "dimensions": {"quality": 5}})
    status, v2 = mgmt.command("/v1/feedback/revise", {
        "feedback_id": rec["feedback_id"], "dimensions": {"quality": 3}})
    assert status == 200 and v2["revision"] == 2
    snap = mgmt.snapshot()
    assert snap["feedback_counts"]["self_written"] == 1
    assert any(f["feedback_id"] == rec["feedback_id"] for f in snap["feedback"])
    assert "reputation" not in snap["feedback_counts"]
