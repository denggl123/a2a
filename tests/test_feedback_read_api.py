"""R2 三个只读接口（FEEDBACK-API §3）—— 真实 HTTP：`GET /v1/feedback`、
`GET /v1/feedback/{id}/versions`、`GET /v1/feedback/summary`。

它们是给**别的 AI / 工作台**读的门（不经过控制台），所以必须在真实网关上验：
① 受管理面同样保护（没配对拿不到）；② 形状是 `FeedbackView`（键始终存在）；
③ 分页游标能翻到底；④ **只读** —— 只给事实计数与各维平均，绝不出现信誉分/等级。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector

PROJ = "proj_read"
TASKS = ("t1", "t2", "t3")


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def _seed_projection_and_tasks(daemon, count=3):
    """一个本机投影 + 若干已交付任务（只读接口要有东西可读）。"""
    daemon.store.put("projections", PROJ, {"network_card": {"x-a2n": {"projection": {
        "node_did": "did:a2n:ag_seller", "service_id": "svc_a"}}}})
    for tid in TASKS[:count]:
        daemon.store.claim(PROJ, tid, fingerprint=f"{PROJ}:{tid}")
        daemon.store.finish(PROJ, tid, {"state": "COMPLETED", "metadata": {}})


class _Console:
    """拿到「本机控制台」会话（cookie + Referer），等价于人在浏览器里打开 /console。"""

    def __init__(self, base):
        self.base = base
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def open(self):
        with self.opener.open(self.base + "/console", timeout=5) as r:
            assert r.status == 200
        return self

    def get(self, path):
        req = urllib.request.Request(self.base + path,
                                     headers={"Referer": self.base + "/console"})
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def plain_get(self, path):
        """不带 cookie/Referer 的裸 GET（应当被管理面拒绝）。"""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(self.base + path)
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


def _write(daemon, task_id, dims=None, note=""):
    status, rec = daemon.management.command("/v1/feedback/open", {
        "scope": PROJ, "task_id": task_id, "dimensions": dims or {"quality": 5},
        "note": note})
    assert status == 201, rec
    return rec


def test_read_list_shape_pagination_and_auth(tmp_path, protector):
    node = Daemon(tmp_path / "n", port=0, protector=protector).start()
    try:
        _seed_projection_and_tasks(node)
        for tid in TASKS:
            _write(node, tid, {"quality": 5, "punctual": 4})
        base = node.runtime.local_base_url
        con = _Console(base).open()

        # 管理面同款保护：没配对拿不到（本机也一样）。
        assert con.plain_get("/v1/feedback")[0] == 401

        status, body = con.get("/v1/feedback?limit=2")
        assert status == 200
        assert body["count"] == 2 and body["total"] == 3
        assert body["next_cursor"]                       # 还有下一页
        item = body["feedback"][0]
        # FeedbackView：签名反馈 + 两个标记**键始终存在**。
        assert "verified" in item and isinstance(item["verified"], bool)
        assert "self_source" in item and isinstance(item["self_source"], bool)
        assert item["source"] == "self"
        assert item["direction"] == "buyer_to_seller"
        assert item["v"] == "a2n-feedback/1"

        status, page2 = con.get("/v1/feedback?limit=2&cursor=" + body["next_cursor"])
        assert status == 200 and page2["count"] == 1 and page2["next_cursor"] == ""
        seen = {r["feedback_id"] for r in (*body["feedback"], *page2["feedback"])}
        assert len(seen) == 3                            # 三页不重不漏

        # 过滤：按 task_id / direction。
        status, f = con.get("/v1/feedback?task_id=t2")
        assert status == 200 and [r["task_id"] for r in f["feedback"]] == ["t2"]
        status, f = con.get("/v1/feedback?direction=seller_to_buyer")
        assert status == 200 and f["count"] == 0

        # 坏游标按第一页处理，不 500。
        status, f = con.get("/v1/feedback?limit=1&cursor=!!!not-base64!!!")
        assert status == 200 and f["count"] == 1
    finally:
        node.stop()


def test_versions_chain_is_read_only_and_ordered(tmp_path, protector):
    node = Daemon(tmp_path / "n", port=0, protector=protector).start()
    try:
        _seed_projection_and_tasks(node)
        rec = _write(node, "t1", {"quality": 5}, note="第一版")
        _status, v2 = node.management.command("/v1/feedback/revise", {
            "feedback_id": rec["feedback_id"], "dimensions": {"quality": 3}, "note": "改判"})
        assert v2["revision"] == 2
        base = node.runtime.local_base_url
        con = _Console(base).open()

        status, body = con.get(f"/v1/feedback/{rec['feedback_id']}/versions")
        assert status == 200 and body["count"] == 2
        revs = [v["revision"] for v in body["versions"]]
        assert revs == [1, 2]                             # 升序，最新在最后
        # 旧版只读：v1 的内容还在，没被 v2 覆盖。
        assert body["versions"][0]["dimensions"] == {"quality": 5}
        assert body["versions"][0]["note"] == "第一版"
        assert body["versions"][1]["note"] == "改判"

        # 不存在的 feedback_id → 404（不是空 200）。
        assert con.get("/v1/feedback/fb_does_not_exist/versions")[0] == 404
    finally:
        node.stop()


def test_summary_gives_facts_only_never_a_score(tmp_path, protector):
    node = Daemon(tmp_path / "n", port=0, protector=protector).start()
    try:
        _seed_projection_and_tasks(node)
        _write(node, "t1", {"quality": 5, "punctual": 4})
        _write(node, "t2", {"quality": 3})
        base = node.runtime.local_base_url
        con = _Console(base).open()

        status, s = con.get("/v1/feedback/summary")
        assert status == 200
        assert s["total"] == 2 and s["self_written"] == 2 and s["received"] == 0
        # 各维平均**注明样本数**（quality: 5 与 3 → 均值 4，样本 2）。
        assert s["dimensions"]["quality"] == {"count": 2, "avg": 4.0}
        assert s["dimensions"]["punctual"] == {"count": 1, "avg": 4.0}
        assert s["latest_at"] and "T" in s["latest_at"]   # ISO 时间，不是裸浮点
        assert s["kind"] == "bilateral_feedback_not_reputation_score"
        # 只发行情/事实：绝不是信誉分、没有等级。
        for forbidden in ("score", "reputation", "rating", "grade", "level", "rank"):
            assert forbidden not in s
        assert "信誉分" in s["notice"] or "不是信誉分" in s["notice"]

        # counterparty 过滤：指向别人 → 一条都没有（本机这两条 provider_did=seller）。
        status, s2 = con.get("/v1/feedback/summary?counterparty=did:a2n:ag_other")
        assert status == 200 and s2["total"] == 0
        status, s3 = con.get("/v1/feedback/summary?provider_did=did:a2n:ag_seller")
        assert status == 200 and s3["total"] == 2         # provider_did 是 counterparty 的别名
    finally:
        node.stop()
