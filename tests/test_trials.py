"""试用期 + 样品的硬口径（`docs/VISION.md` §1.2 主张 #12 / §6.4）。

要点：
* 前 cap 次**完成**调用免费，并自动沉淀成样品；失败/取消不占名额；
* 同一 task 重试/轮询只计一次（幂等键）；
* 样品锚定具体调用与版本，删不掉、也不许拿旧样品冒充新版本；
* 公开预览做脱敏投影（隐去密钥），移掉了什么要如实记下。
"""
from __future__ import annotations

import pytest

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


def test_free_text_secrets_are_scrubbed_even_in_summary():
    """自由文本里的邮箱/手机号/密钥也要隐去 —— 只按字段名会漏（§6.4 公开前脱敏）。"""
    book = TrialBook(_store(), cap=3)
    s = book.record(
        "svc_1", "t0",
        _done(result={"deliverable": "成品请联系 zhangsan@example.com 领取",
                      "note": "token=abc1234567890xyz"}),
        request={"payload": "把海报发到 buyer@corp.com，手机 13800138000"},
        version="1.0.0")
    assert "buyer@corp.com" not in s["summary"], "摘要里的邮箱要隐去"
    assert "13800138000" not in s["summary"], "摘要里的手机号要隐去"
    assert "zhangsan@example.com" not in s["preview"]
    assert "abc1234567890xyz" not in s["preview"]
    assert {"邮箱", "手机号", "键值密钥"} <= set(s["redactions"])
    assert "成品请联系" in s["preview"], "非敏感内容要保留"
    assert book.verify(s), "脱敏后样品指纹仍自洽"


def test_clean_text_is_not_over_redacted():
    book = TrialBook(_store(), cap=3)
    s = book.record("svc_1", "t0", _done(result={"deliverable": "广州夏季促销海报"}),
                    request={"payload": "做一张夏季促销海报，主题清凉"}, version="1.0.0")
    assert s["summary"] == "做一张夏季促销海报，主题清凉"
    assert s["preview"] == '{"deliverable": "广州夏季促销海报"}'
    assert s["redactions"] == []


def test_notice_is_available_before_calling():
    book = TrialBook(_store(), cap=10)
    notice = book.status("svc_1")["notice"]
    assert "10" in notice and "样品" in notice


def test_store_tx_rolls_back_as_one_unit():
    """`LocalStore.tx()`：中途抛错整段回滚，不留半截账（§原子性）。"""
    store = _store()
    store.put("ns", "a", {"v": 1})
    with pytest.raises(RuntimeError):
        with store.tx():
            store.put("ns", "a", {"v": 2})
            store.put("ns", "b", {"v": 9})
            raise RuntimeError("boom")
    assert store.get("ns", "a") == {"v": 1}, "回滚后不该看到半截写"
    assert store.get("ns", "b") is None


def test_concurrent_completions_do_not_lose_updates():
    """并发完成调用：计数与样品一起落库，不丢更新（旧的三次独立 put 会丢）。"""
    import threading

    book = TrialBook(_store(), cap=1000)
    n = 120

    def work(i):
        book.record("svc_1", f"t{i}",
                    _done(result=f"成品{i}"), request="需求", version="1.0.0")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert book.status("svc_1")["completed"] == n, "并发完成被写丢了"
    assert book.counts()["samples"] == n, "每笔完成都该有一条样品"
