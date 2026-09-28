"""「这单我不认」—— 自持模式的本机记录（`a2n_sdk.disputes`）。

要守的三条，缺一条这个功能就会变成谎言：

1. **只能对真有的一笔说不认** —— 对查无此单的任务开单等于伪造无对象的指控。
2. **指纹防篡改** —— 记录能被改的凭证等于没有凭证；撤回是"新发生的事实"，
   不改变"当初不认的是什么"，所以指纹在撤回前后应保持不变。
3. **撤回留痕，不抹记录** —— 抹掉 = 事后翻脸无据。而"没有仲裁员"也不许变成
   额外承诺：记录里 `kind` 必须如实写明它**不是**仲裁。
"""
from __future__ import annotations

import base64
import json
import os

import pytest

from a2n_sdk.disputes import KIND, STATE_OPEN, STATE_WITHDRAWN, DisputeBook
from a2n_sdk.storage import LocalStore


def _book_with_task(scope="svc_a", task_id="t1"):
    store = LocalStore()
    store.claim(scope, task_id, fingerprint=f"{scope}:{task_id}")
    store.finish(scope, task_id, {"state": "COMPLETED", "metadata": {}})
    return DisputeBook(store), store


def test_open_requires_a_real_local_call():
    book, _ = _book_with_task()
    d = book.open("svc_a", "t1", "requester", "结果不是我想要的")
    assert d["state"] == STATE_OPEN and d["kind"] == KIND
    assert d["digest"] and book.verify(d) is True
    # 查无此单 → 拒绝
    with pytest.raises(ValueError):
        book.open("svc_a", "never-called", "requester", "瞎说不认")


def test_open_validates_side_and_reason():
    book, _ = _book_with_task()
    with pytest.raises(ValueError):
        book.open("svc_a", "t1", "audience", "申诉方不对")
    with pytest.raises(ValueError):
        book.open("svc_a", "t1", "requester", "   ")
    with pytest.raises(ValueError):
        book.open("svc_a", "t1", "requester", "x" * 501)


def test_open_is_idempotent_per_task_and_side():
    book, _ = _book_with_task()
    first = book.open("svc_a", "t1", "requester", "不满意")
    again = book.open("svc_a", "t1", "requester", "不满意（又说一遍）")
    assert again["id"] == first["id"], "同一笔同一方不重复堆指控"
    other = book.open("svc_a", "t1", "node", "对方没付")
    assert other["id"] != first["id"], "另一方是另一条记录"


def test_digest_detects_tampering_but_survives_withdraw():
    book, store = _book_with_task()
    d = book.open("svc_a", "t1", "requester", "时长对不上", details={"observed": 9})
    assert book.verify(d)
    # 直接改库里的理由 → 指纹对不上
    tampered = dict(d, reason="我改主意了，改成别的理由")
    store.put("disputes", d["id"], tampered)
    assert book.verify(book.get(d["id"])) is False
    # 复原后撤回：事实变了，指纹不变
    store.put("disputes", d["id"], d)
    w = book.withdraw(d["id"], note="对方解释了，算了")
    assert w["state"] == STATE_WITHDRAWN and w["withdrawn_at"]
    assert w["digest"] == d["digest"], "撤回不改「当初不认的是什么」"
    assert book.verify(w)


def test_withdraw_keeps_the_record_and_is_idempotent():
    book, _ = _book_with_task()
    d = book.open("svc_a", "t1", "requester", "不对")
    first = book.withdraw(d["id"])
    second = book.withdraw(d["id"], note="再点一次")
    assert first["state"] == STATE_WITHDRAWN
    assert second["state"] == STATE_WITHDRAWN and second["note"] == first["note"]
    assert book.get(d["id"]) is not None, "撤回不是删除"


def test_list_filters_and_counts():
    book, store = _book_with_task()
    store.claim("svc_b", "t2", fingerprint="svc_b:t2")
    a = book.open("svc_a", "t1", "requester", "a")
    b = book.open("svc_b", "t2", "requester", "b")
    book.withdraw(b["id"])
    assert [r["id"] for r in book.list(state=STATE_OPEN)] == [a["id"]]
    assert [r["id"] for r in book.list(scope="svc_b")] == [b["id"]]
    assert [r["id"] for r in book.list(task_id="t1")] == [a["id"]]
    counts = book.counts()
    assert counts == {"total": 2, "open": 1, "withdrawn": 1}


def test_open_for_only_reports_open_and_never_fabricates():
    book, _ = _book_with_task()
    assert book.open_for("svc_a", "t1") is None
    d = book.open("svc_a", "t1", "requester", "不对")
    assert book.open_for("svc_a", "t1")["id"] == d["id"]
    book.withdraw(d["id"])
    assert book.open_for("svc_a", "t1") is None
    assert book.open_for("svc_z", "nope") is None


def test_withdraw_unknown_id_refuses():
    book, _ = _book_with_task()
    with pytest.raises(ValueError):
        book.withdraw("ds_does_not_exist")


# ---------------- 端到端：节点 HTTP 面上的那条路 ----------------

from a2n_node.daemon import Daemon  # noqa: E402
from a2n_node.protection import EnvironmentProtector  # noqa: E402


def http(base, path, body=None, token=""):
    import urllib.error
    import urllib.request
    headers = {"Content-Type": "application/json", "X-A2N-Local-Token": token}
    req = urllib.request.Request(base + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_node_console_can_record_and_withdraw_a_refusal(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base, token = daemon.runtime.local_base_url, daemon.runtime.management_token
        store = daemon.management.store
        store.claim("svc_ocr", "task-1", fingerprint="svc_ocr:task-1")
        store.finish("svc_ocr", "task-1", {"state": "COMPLETED", "metadata": {}})

        status, rec = http(base, "/v1/disputes/open", {
            "scope": "svc_ocr", "task_id": "task-1", "side": "requester",
            "reason": "交付的表格少了一列"}, token)
        assert status == 201 and rec["kind"] == KIND
        assert daemon.management.disputes.verify(rec)

        # 列表与计数
        status, listing = http(base, "/v1/disputes", token=token)
        assert status == 200 and listing["counts"]["open"] == 1
        assert [d["id"] for d in listing["disputes"]] == [rec["id"]]

        # 详情页会带上这笔的不认记录
        status, detail = http(base, "/v1/calls/detail?scope=svc_ocr&task_id=task-1",
                              token=token)
        assert status == 200 and detail["disputes"][0]["id"] == rec["id"]

        # 快照按钮计数
        snap = daemon.management.snapshot()
        assert snap["dispute_counts"]["open"] == 1

        # 撤回 → 仍在，只是标为已撤回
        status, out = http(base, "/v1/disputes/withdraw", {"dispute_id": rec["id"]}, token)
        assert status == 200 and out["state"] == STATE_WITHDRAWN
        assert daemon.management.snapshot()["dispute_counts"] == {
            "total": 1, "open": 0, "withdrawn": 1}

        # 对不存在的调用说不认 → 400，且不写库
        status, err = http(base, "/v1/disputes/open", {
            "scope": "svc_ocr", "task_id": "never", "reason": "x"}, token)
        assert status == 400 and "没有这条调用记录" in err["error"]
    finally:
        daemon.stop()


def test_refusal_requires_management_token(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        base = daemon.runtime.local_base_url
        status, _ = http(base, "/v1/disputes/open", {"scope": "s", "task_id": "t",
                                                     "reason": "x"})
        assert status == 401
        status, _ = http(base, "/v1/disputes")
        assert status == 401
    finally:
        daemon.stop()
