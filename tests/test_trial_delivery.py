"""R1：一次真实交付 → 试用计数 + 样品（端到端接线）。

把 `docs/VISION.md` §1.2 #12 的行为钉在**真实链路**上：起一个常驻节点、挂一份供给、
真的发一次 A2A 调用，然后看试用账本与样品是否如实长出来。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
import uuid

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def _http(base, path, body=None, token=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-A2N-Local-Token"] = token
    req = urllib.request.Request(base + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _card(skill="poster"):
    return {"name": "海报工坊", "url": "http://127.0.0.1:9/a2a/poster",
            "x-a2n": {"public_sample_policy": {"safe_output": True}, "price_book": {skill: {"CNY": {"dimensions": [{"key": "call_count", "amount": 0, "per": 1}]}}}},
            "skills": [{"id": skill, "name": "海报工坊"}], "version": "1.0.0"}


def _call(base, service_id, text):
    body = {"jsonrpc": "2.0", "id": "1", "method": "message/send",
            "params": {"metadata": {"a2nSampleConsent": {"input_public": True, "output_public": True}},
                       "message": {"role": "user", "messageId": "msg_" + uuid.uuid4().hex,
                                   "parts": [{"kind": "text", "text": text}]}}}
    return _http(base, f"/a2a/{service_id}", body)


def test_real_delivery_grows_trial_and_sample(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        daemon.runtime.mount_callable(
            _card(), lambda payload: {"deliverable": "海报成品", "topic": str(payload)},
            service_id="svc_poster")
        st, resp = _call(base, "svc_poster", "做一张夏季促销海报")
        assert st == 200 and (resp.get("result") or {}).get("status", {}).get("state") == "completed"

        status = daemon.trials.status("svc_poster")
        assert status["completed"] == 1 and status["remaining"] == 9 and not status["ended"]
        samples = daemon.trials.samples("svc_poster")
        assert len(samples) == 1
        s = samples[0]
        assert s["summary"] == "做一张夏季促销海报"
        assert "海报成品" in s["preview"]
        assert daemon.trials.verify(s)
    finally:
        daemon.stop()


def test_retry_same_task_counts_once_and_failed_calls_do_not(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        daemon.runtime.mount_callable(_card(), lambda payload: {"deliverable": "ok"},
                                      service_id="svc_poster")
        # 死卡：上游永远连不上 → 失败不占名额
        daemon.runtime.mount_callable(_card("dead"),
                                      lambda payload: (_ for _ in ()).throw(
                                          RuntimeError("boom")),
                                      service_id="svc_dead")
        seen = []
        body = {"jsonrpc": "2.0", "id": "1", "method": "message/send",
                "params": {"message": {"role": "user", "messageId": "same-task",
                                       "parts": [{"kind": "text", "text": "重试"}]}}}
        for _ in range(3):
            st, resp = _http(base, "/a2a/svc_poster", body)
            seen.append(resp)
        assert daemon.trials.status("svc_poster")["completed"] == 1, "同一 task 只算一次"

        st, resp = _call(base, "svc_dead", "会失败")
        assert (resp.get("result") or resp.get("error")) is not None
        assert daemon.trials.status("svc_dead")["completed"] == 0, "失败不占名额"
        assert daemon.trials.samples("svc_dead") == []
    finally:
        daemon.stop()


def test_cap_then_graduation_and_persistence_across_restart(tmp_path, protector):
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        daemon.runtime.mount_callable(_card(), lambda payload: {"deliverable": "ok"},
                                      service_id="svc_poster")
        for i in range(11):
            _call(base, "svc_poster", f"第 {i} 单")
        status = daemon.trials.status("svc_poster")
        assert status["completed"] == 11 and status["ended"] is True
        assert len(daemon.trials.samples("svc_poster")) == 10, "首批样品固定前 10 次"
    finally:
        daemon.stop()

    restarted = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        status = restarted.trials.status("svc_poster")
        assert status["completed"] == 11, "计数重启后连续"
        assert len(restarted.trials.samples("svc_poster")) == 10, "样品重启后仍在"
    finally:
        restarted.stop()


def test_card_carries_the_notice_before_calling(tmp_path, protector):
    """调用前就该看到"前 N 次免费、交付默认成公开样品"（§6.4）。"""
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        daemon.runtime.mount_callable(_card(), lambda payload: {"deliverable": "ok"},
                                      service_id="svc_poster")
        projected = daemon.runtime.project_binding("svc_poster")
        trial = (projected.get("x-a2n") or {}).get("trial") or {}
        assert trial.get("cap") == 10
        assert "样品" in trial.get("notice", "")
        assert trial.get("policy") == "first_n_free_calls_become_public_samples"
    finally:
        daemon.stop()
