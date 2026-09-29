"""试用期 + 样品的硬口径（`docs/VISION.md` §1.2 主张 #12 / §6.4）。

要点：
* 前 cap 次**完成**调用免费，并自动沉淀成样品；失败/取消不占名额；
* 同一 task 重试/轮询只计一次（幂等键）；
* 样品锚定具体调用与版本，删不掉、也不许拿旧样品冒充新版本；
* 公开预览做脱敏投影（隐去密钥），移掉了什么要如实记下。
"""
from __future__ import annotations

from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook


def _store() -> LocalStore:
    return LocalStore(":memory:")


def _done(result=None, ok=True, state="COMPLETED") -> dict:
    return {"ok": ok, "state": state, "result": result, "task_id": "t"}


def test_first_cap_completions_become_samples():
    book = TrialBook(_store(), cap=3)
    for i in range(3):
        s = book.record("svc_1", f"t{i}", _done(result={"deliverable": f"成品{i}"}),
                        request={"payload": f"需求{i}"}, version="1.0.0")
        assert s is not None, f"第 {i + 1} 次完成应生成样品"
        assert book.verify(s), "样品指纹应自洽"
    st = book.status("svc_1")
    assert (st["completed"], st["used"], st["remaining"], st["ended"]) == (3, 3, 0, True)
    assert len(book.samples("svc_1")) == 3
    # 第 4 次完成：仍计数，但不再进首批样品
    s4 = book.record("svc_1", "t3", _done(result={"deliverable": "成品3"}),
                     request={"payload": "需求3"}, version="1.0.0")
    assert s4 is None
    assert book.status("svc_1")["completed"] == 4
    assert len(book.samples("svc_1")) == 3


def test_failed_calls_do_not_consume_the_quota():
    book = TrialBook(_store(), cap=3)
    assert book.record("svc_1", "t0", _done(ok=False, state="FAILED", result=None)) is None
    assert book.record("svc_1", "t1", _done(ok=False, state="DELIVERY_UNKNOWN")) is None
    assert book.record("svc_1", "t2", _done(ok=False, state="CANCELED")) is None
    st = book.status("svc_1")
    assert st["completed"] == 0 and st["remaining"] == 3
    assert book.samples("svc_1") == []


def test_same_task_is_counted_once():
    book = TrialBook(_store(), cap=5)
    book.record("svc_1", "same", _done(result="A"), request="x", version="1")
    book.record("svc_1", "same", _done(result="A"), request="x", version="1")
    assert book.status("svc_1")["completed"] == 1
    assert len(book.samples("svc_1")) == 1


def test_sample_redacts_secrets_and_reports_them():
    book = TrialBook(_store(), cap=3)
    s = book.record(
        "svc_1", "t0",
        _done(result={"deliverable": "成品", "api_key": "sk-live-123",
                      "buyer": {"email": "a@b.com", "name": "张三"}}),
        request={"payload": "做一份海报"}, version="1.0.0")
    assert "sk-live-123" not in s["preview"], "密钥不许进公开预览"
    assert "a@b.com" not in s["preview"]
    assert set(s["redactions"]) >= {"api_key", "email"}
    assert "成品" in s["preview"], "非敏感内容要保留"
    assert s["summary"] == "做一份海报"
    assert s["hidden_reason"] == ""


def test_sample_anchors_version_and_cannot_be_replaced():
    book = TrialBook(_store(), cap=3)
    book.record("svc_1", "t0", _done(result="旧版成品"), request="x", version="1.0.0")
    book.record("svc_1", "t1", _done(result="新写法成品"), request="x", version="2.0.0")
    samples = book.samples("svc_1")
    assert [s["version"] for s in samples] == ["1.0.0", "2.0.0"]
    # 没有"删掉不理想样品"的接口：样品只能被读，不能被移除
    assert not hasattr(book, "remove") and not hasattr(book, "delete")
    assert book.verify(samples[0]) and book.verify(samples[1])


def test_notice_is_available_before_calling():
    book = TrialBook(_store(), cap=10)
    notice = book.status("svc_1")["notice"]
    assert "10" in notice and "样品" in notice
