"""买方读取入口：公开只读样品面（`docs/VISION.md` §6.4）。

调用前就该能看到"这个供给真实交付过什么"。样品只能从**卖方节点的公开面**读到，
且必须受条数上限、分页与"只对自己的真实履历"约束 —— 公开面是看履历，
不是发现源，不能被拉爆。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.runtime import _public_samples_url


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def http(base, path, body=None, token=""):
    headers = {"Content-Type": "application/json", "X-A2N-Local-Token": token}
    req = urllib.request.Request(
        base + path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _card(name="海报工坊", skill="poster"):
    return {"name": name, "url": "http://127.0.0.1:9/a2a/" + skill,
            "x-a2n": {"public_sample_policy": {"safe_output": True}, "price_book": {skill: {"CNY": {"dimensions": [{"key": "call_count", "amount": 0, "per": 1}]}}}},
            "skills": [{"id": skill, "name": name}], "version": "1.0.0"}


def _call(base, service_id, text, task_id=None):
    body = {"jsonrpc": "2.0", "id": "1", "method": "message/send",
            "params": {"metadata": {"a2nSampleConsent": {"input_public": True, "output_public": True}},
                       "message": {"role": "user",
                                   "messageId": task_id or ("msg_" + uuid.uuid4().hex),
                                   "parts": [{"kind": "text", "text": text}]}}}
    return http(base, f"/a2a/{service_id}", body)


def test_samples_are_mandatory_and_report_only_real_supply(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
        daemon.management.discovery_public_base = base
        daemon.runtime.mount_callable(
            _card(), lambda payload: {"deliverable": "成品海报"},
            service_id="svc_poster")
        _call(base, "svc_poster", "做一张夏季促销海报")

        # 免费采样的公开义务不能用新开关或旧总开关绕过。
        assert http(base, "/v1/public-services", {"services": {"samples": False, "witness": True}}, token)[0] == 400
        assert daemon.management.public_services["witness"] is False
        assert http(base, "/v1/public-service", {"enabled": False}, token)[0] == 200
        assert http(base, "/public/v1/samples?service_id=svc_poster")[0] == 200
        assert http(base, "/v1/public-services", {"services": {"samples": True}}, token)[0] == 200
        assert daemon.management.public_services["discovery"] is True
        assert daemon.management.public_services["task_relay"] is False
        assert daemon.management.public_services["witness"] is False

        status, out = http(base, "/public/v1/samples?service_id=svc_poster")
        assert status == 200
        assert out["samples_total"] == 1 and out["count"] == 1
        s = out["samples"][0]
        assert s["summary"] == "做一张夏季促销海报"
        assert "成品海报" in s["preview"]
        assert out["kind"] == "real_delivery_samples_not_promotion"
        assert "样品" in out["notice"]

        # 只对本节点真挂着的公开供给发样品；不存在的供给明确报错，不发空壳
        assert http(base, "/public/v1/samples?service_id=svc_missing")[0] == 400
        assert http(base, "/public/v1/samples")[0] == 400

        # 下架与卸载只停止卖方接新单，已发生的履历仍然可以读取。
        assert http(base, "/v1/unpublish", {"service_id": "svc_poster"}, token)[0] == 200
        assert http(base, "/public/v1/samples?service_id=svc_poster")[1]["samples_total"] == 1
        assert http(base, "/v1/bindings/remove", {"service_id": "svc_poster"}, token)[0] == 200
        assert http(base, "/public/v1/samples?service_id=svc_poster")[1]["samples_total"] == 1
    finally:
        daemon.stop()


def test_disabled_legacy_sample_setting_is_normalized_on_restart(tmp_path, protector):
    from a2n_sdk.storage import LocalStore

    store = LocalStore(tmp_path / "runtime.db", protector=protector)
    store.put("node_settings", "public_service_enabled", False)
    store.put("node_settings", "public_services", {
        "discovery": False, "samples": False, "witness": False,
        "task_relay": False, "blob_cache": False})
    store.close()
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        services = daemon.management.snapshot()["public_service"]["services"]
        assert services["samples"] is True and services["discovery"] is True
        assert not services["witness"] and not services["task_relay"] and not services["blob_cache"]
        assert daemon.store.get("node_settings", "public_services") == services
    finally:
        daemon.stop()


def test_legacy_sample_is_reprojected_without_changing_original_history(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        daemon.runtime.mount_callable(_card(), lambda payload: "ok", service_id="legacy")
        sample = daemon.trials.record("legacy", "old", {"ok": True, "state": "COMPLETED", "result": "ok"})
        old = {**sample, "v": 2, "summary": '{"api_key":"old-credential-value","text":"旧需求"}',
               "preview": json.dumps({"deliverable": "旧成果", "asset_id": "as_" + "a" * 32,
                   "recipient_did": "did:a2n:ag_" + "b" * 24,
                   "nested": {"url": "https://private.invalid/private-output", "base64": "PRIVATE_RAW_BYTES"}})}
        daemon.store.put("samples", "legacy::old", old)
        _, page = http(daemon.runtime.local_base_url, "/public/v1/samples?service_id=legacy")
        encoded = json.dumps(page)
        assert "旧成果" in page["samples"][0]["preview"]
        assert "旧需求" in page["samples"][0]["summary"]
        for private in ("old-credential-value", "as_" + "a" * 32, "did:a2n:ag_" + "b" * 24,
                        "private-output", "PRIVATE_RAW_BYTES"):
            assert private not in encoded
        assert daemon.trials.sample("legacy", "old") == old
    finally:
        daemon.stop()


def test_old_publication_card_format_does_not_leave_a_delivery_without_a_sample(tmp_path, protector):
    from a2n_sdk.ports import CallRequest

    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        card = _card()
        card["x-a2n"]["public_sample_policy"] = "old-format"
        daemon.runtime.mount_callable(card, lambda payload: "成品", service_id="old-card")
        result = daemon.calls.invoke("old-card", CallRequest(task_id="done", payload="需求"))
        assert result.ok and not result.metadata.get("projection_pending")
        assert daemon.trials.sample("old-card", "done")["preview"] == "成品"
        assert daemon.trials.status("old-card")["completed"] == 1
    finally:
        daemon.stop()


def test_samples_entry_is_paginated_and_capped(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        daemon.management.discovery_public_base = base
        daemon.runtime.mount_callable(
            _card(), lambda payload: {"deliverable": f"成品 {payload}"},
            service_id="svc_poster")
        for i in range(12):
            _call(base, "svc_poster", f"第 {i} 单")
        assert http(base, "/v1/public-service", {"enabled": True},
                    daemon.runtime.management_token)[0] == 200

        status, page1 = http(base, "/public/v1/samples?service_id=svc_poster&limit=5")
        assert status == 200 and page1["count"] == 5 and page1["samples_total"] == 10
        assert page1["next_cursor"], "还有更多就必须给出下一页游标"
        ids1 = {s["id"] for s in page1["samples"]}

        status, page2 = http(base, "/public/v1/samples?service_id=svc_poster&limit=5&cursor="
                             + page1["next_cursor"])
        assert status == 200 and page2["count"] == 5
        assert not page2["next_cursor"], "末页不该再给游标"
        assert not (ids1 & {s["id"] for s in page2["samples"]}), "分页不许重复"

        # 条数上限：一次要 999 也只给上限之内
        status, big = http(base, "/public/v1/samples?service_id=svc_poster&limit=999")
        assert status == 200 and big["count"] <= big["limit"] <= 20

        # 摘要优先：brief 模式不发完整预览
        status, brief = http(base, "/public/v1/samples?service_id=svc_poster&brief=1&limit=1")
        assert status == 200 and "preview" not in brief["samples"][0]
        assert brief["samples"][0]["summary"]
    finally:
        daemon.stop()


def test_samples_url_is_derived_from_the_projected_card():
    assert _public_samples_url("https://seller.example:8891/a2a/svc_abc") == \
        "https://seller.example:8891/public/v1/samples?service_id=svc_abc"
    assert _public_samples_url("") == ""
    assert _public_samples_url("http://seller/a2a") == ""      # 没有服务标识
    assert _public_samples_url("not-a-url") == ""
