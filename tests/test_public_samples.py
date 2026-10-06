"""买方读取入口：公开只读样品面（`docs/VISION.md` §6.4）。

调用前就该能看到"这个供给真实交付过什么"。样品只能从**卖方节点的公开面**读到，
且必须受公益开关、条数上限、分页与"只对自己的公开供给"约束 —— 公开面是看履历，
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


def test_samples_entry_is_gated_and_reports_only_real_public_supply(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
        daemon.management.discovery_public_base = base
        daemon.runtime.mount_callable(
            _card(), lambda payload: {"deliverable": "成品海报"},
            service_id="svc_poster")
        _call(base, "svc_poster", "做一张夏季促销海报")

        # 样品默认公开，但用户明确关闭后买方读不到；其他公共服务独立开关。
        assert http(base, "/v1/public-services", {"services": {"samples": False}}, token)[0] == 200
        assert http(base, "/public/v1/samples?service_id=svc_poster")[0] == 403
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
