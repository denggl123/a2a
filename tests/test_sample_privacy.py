"""样品公开口径（用户裁决 2026-10-06：前 10 次免费 = 一定公开为样品）。

这些断言守的是新口径的**两条边界**，不是"要不要公开"：
  · 一定公开：没有"缺声明就藏起来"这一档，摘要与预览都出得来；
  · 公开的是**可公开投影**：脱敏照旧，媒体只走节点有界编码器生成的缩略图。
"""
from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook


def test_sample_is_published_without_any_consent_declaration():
    """不需要任何 consent 声明，样品照样公开 —— 免费的履历不能白做。"""
    book = TrialBook(LocalStore())
    sample = book.record("svc", "one", {"ok": True, "state": "COMPLETED", "result": "未被识别的客户计划"},
                         request={"payload": "客户的私有计划书"})
    assert sample["slot"] == 1 and book.status("svc")["completed"] == 1
    assert sample["preview"] == '{"ok":true,"state":"COMPLETED","result":"未被识别的客户计划"}' or \
        "未被识别的客户计划" in sample["preview"]
    assert sample["summary"] == "客户的私有计划书"
    assert sample["publication_policy"] == "ALWAYS_PUBLIC"
    assert book.verify(sample)
    # 声明只作为事实记录，不参与是否公开的判定。
    assert sample["buyer_declared_public"] is False
    assert sample["seller_declared_safe_output"] is False


def test_publication_is_not_gated_by_consent_but_scrubbing_still_applies():
    """买方声明与否都不改变"公开"这件事；脱敏在任何情况下都照做。"""
    book = TrialBook(LocalStore())
    for task, consent in (("with", {"input_public": True, "output_public": True}), ("without", {})):
        request = {"payload": "公开需求", "metadata": {"a2nSampleConsent": consent}}
        outcome = {"ok": True, "state": "COMPLETED", "result": "公开结果 buyer@example.com"}
        sample = book.record("svc", task, outcome, request=request)
        assert sample["preview"], f"{task}: 无论是否声明，样品都应公开"
        assert "公开结果" in sample["preview"], task
        assert "buyer@example.com" not in sample["preview"], f"{task}: 脱敏必须照做"
        assert "邮箱" in sample["redactions"], task
        assert book.verify(sample), f"{task}: 指纹应自洽"


def test_media_result_is_a_placeholder_never_raw_payload():
    """媒体结果只留位置说明 —— 原始文件/URL/base64 不直接进公开样品。"""
    book = TrialBook(LocalStore())
    sample = book.record("svc", "media", {"ok": True, "state": "COMPLETED",
                                          "result": {"files": [{"path": "/srv/private/master.psd"}]}},
                         request={"payload": "做一张海报"})
    assert "媒体占位" in sample["hidden_reason"]
    assert sample["publication_policy"] == "PLACEHOLDER"
    assert "/srv/private/master.psd" not in sample["preview"]
    assert sample["slot"] == 1, "占位也占名额：这一次交付确实发生过"


def test_free_quota_is_ten_and_samples_stop_after_it():
    """裁决里的"前 10 次"落成可执行事实：第 11 次不再进首批样品。"""
    book = TrialBook(LocalStore())
    assert book.status("svc")["cap"] == 10
    for i in range(10):
        assert book.record("svc", f"t{i}", {"ok": True, "state": "COMPLETED", "result": f"成品{i}"},
                           request={"payload": f"需求{i}"}) is not None, f"第 {i + 1} 次应成样品"
    assert book.status("svc")["ended"] is True
    assert book.record("svc", "t10", {"ok": True, "state": "COMPLETED", "result": "成品10"},
                       request={"payload": "需求10"}) is None
    assert book.status("svc")["completed"] == 11
    assert len(book.samples("svc")) == 10


def test_nested_media_is_projected_at_every_level_and_normal_text_survives():
    book = TrialBook(LocalStore())
    sample = book.record("svc", "nested", {"ok": True, "state": "COMPLETED", "result": [
        {"deliverable": "成品说明", "attachment": {"url": "https://private.invalid/output",
            "base64": "PRIVATE_RAW_BYTES", "file": {"path": "/private/image.png"}}}]},
        request={"payload": {"text": "画一个标志", "files": [{"path": "/private/request.png"}]}})
    assert "成品说明" in sample["preview"] and sample["summary"] == "画一个标志"
    for private in ("private.invalid", "PRIVATE_RAW_BYTES", "/private/"):
        assert private not in sample["preview"] and private not in sample["summary"]
    assert sample["publication_policy"] == "PLACEHOLDER"
    assert book.verify(sample)


def test_obsolete_publication_metadata_cannot_break_free_sample_accounting():
    book = TrialBook(LocalStore())
    sample = book.record("svc", "malformed", {"ok": True, "state": "COMPLETED", "result": "成品",
        "metadata": {"sample_policy": "old-format"}},
        request={"payload": "需求", "metadata": {"a2nSampleConsent": "old-format"}})
    assert sample["preview"] == "成品" and sample["slot"] == 1 and book.verify(sample)


def test_media_read_boundary_excludes_metadata_and_extra_identity_fields():
    import base64
    import hashlib
    import io
    from PIL import Image, PngImagePlugin
    from a2n_sdk.privacy import public_sample_media

    def thumbnail(info=None):
        output = io.BytesIO()
        Image.new("RGB", (32, 20), (24, 60, 100)).save(output, format="PNG", pnginfo=info)
        raw = output.getvalue()
        return {"mime_type": "image/png", "base64": base64.b64encode(raw).decode(),
                "sha256": hashlib.sha256(raw).hexdigest(), "width": 32, "height": 20}

    safe = thumbnail()
    assert public_sample_media([{**safe, "owner_did": "private-owner", "url": "private-url"}]) == [safe]
    info = PngImagePlugin.PngInfo()
    info.add_text("private", "PRIVATE_IMAGE_METADATA")
    assert public_sample_media([thumbnail(info)]) == []
    assert public_sample_media([{**safe, "sha256": "wrong"}]) == []
    assert public_sample_media([{**safe, "base64": "arbitrary payload"}]) == []
