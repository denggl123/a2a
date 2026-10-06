import base64
import hashlib
import io
import os
import urllib.error
import urllib.request

import pytest
from PIL import Image, PngImagePlugin

from a2n_node.asset_service import envelope
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.assets import AssetBook, CHUNK
from a2n_sdk.ports import CallRequest
from a2n_sdk.storage import LocalStore


def test_interrupted_upload_does_not_publish_or_leak_capacity_and_ranges_are_exact():
    store = LocalStore()
    book = AssetBook(store, node_did="node")
    with pytest.raises(ValueError, match="LENGTH_MISMATCH"):
        book.upload(10, "application/octet-stream", [b"short"])
    assert not store.items("assets") and not store.items("asset_chunks")
    raw = os.urandom(CHUNK * 2 + 1)
    ref = book.upload(len(raw), "application/octet-stream", [raw[i:i + CHUNK] for i in range(0, len(raw), CHUNK)])
    assert b"".join(book.chunks(ref["asset_id"], CHUNK - 2, CHUNK + 2)) == raw[CHUNK - 2:CHUNK + 3]
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="RANGE_INVALID"):
        list(book.chunks(ref["asset_id"], 10, len(raw)))
    store.close()


@pytest.fixture
def nodes(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    nodes = [Daemon(tmp_path / str(i), port=0, protector=protector, coord_allow_networks=["127.0.0.0/8"]).start() for i in range(3)]
    try:
        yield nodes
    finally:
        for node in reversed(nodes):
            node.stop()


def test_real_file_delivery_private_signed_cross_node_fetch_and_http_range(nodes):
    provider, buyer, observer = nodes
    raw = os.urandom(CHUNK + 41)
    headers = {"X-A2N-Local-Token": provider.runtime.management_token, "Content-Type": "application/octet-stream"}
    request = urllib.request.Request(provider.runtime.local_base_url + "/v1/assets/upload", raw, headers, method="POST")
    import json
    with urllib.request.urlopen(request) as response:
        ref = json.loads(response.read())
    provider.runtime.mount_callable({"name": "file", "version": "1", "skills": [{"id": "file"}]},
        lambda p: {"assets": [ref]}, service_id="file")
    buyer.runtime.import_agent(provider.runtime.project_binding("file"), projection_id="use")
    outcome = buyer.calls.invoke("use", CallRequest(task_id="file-one", payload="give file"))
    assert outcome.ok
    facts = buyer.trade_facts.for_call("use", "file-one")
    cached = buyer.assets.fetch(facts["trade_uid"], ref["asset_id"])
    assert b"".join(buyer.assets.book.chunks(cached["asset_id"])) == raw
    assert buyer.assets.fetch(facts["trade_uid"], ref["asset_id"]) == cached
    foreign = envelope(observer.identity, "READ_RANGE", provider.identity.did, {"asset_id": ref["asset_id"],
        "trade_uid": facts["trade_uid"], "start": 0, "end": 20})
    assert provider.assets.handle("read", foreign)[0] == 403
    public = urllib.request.Request(provider.runtime.local_base_url + f'/v1/assets/{ref["asset_id"]}/content')
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(public)
    assert error.value.code == 401
    partial = urllib.request.Request(provider.runtime.local_base_url + f'/v1/assets/{ref["asset_id"]}/content',
        headers={**headers, "Range": "bytes=65534-65539"})
    with urllib.request.urlopen(partial) as response:
        assert response.status == 206 and response.read() == raw[65534:65540]
    assert not provider.trials.sample("file", "file-one")["media_preview"]


def test_public_thumbnail_is_bounded_reencoded_and_strips_original_identity(nodes):
    """样品一定公开，所以缩略图**不再等 consent**；但它仍必须由节点有界编码器
    重新编码 —— 原始 PNG 的文本块（所有者邮箱）、asset_id、买方 did 都不许带出去。"""
    provider, buyer, _ = nodes
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("private", "private-owner@example.invalid")
    Image.new("RGB", (800, 500), (24, 60, 100)).save(output, format="PNG", pnginfo=info)
    raw = output.getvalue()
    ref = provider.assets.upload(len(raw), "image/png", [raw])
    provider.runtime.mount_callable({"name": "poster", "version": "1", "skills": [{"id": "poster"}],
        "x-a2n": {"public_sample_policy": {"safe_output": True}}}, lambda p: {"assets": [ref]}, service_id="poster")
    buyer.runtime.import_agent(provider.runtime.project_binding("poster"), projection_id="use")
    # 第一单不带任何 consent 声明：按新口径也必须出缩略图。
    for i in range(2):
        assert buyer.calls.invoke("use", CallRequest(task_id=f"poster-{i}", payload="poster")).ok
    provider.management.discovery_public_base = provider.runtime.local_base_url
    samples = provider.management.public_samples("poster")["samples"]
    thumbnail = samples[0]["media_preview"][0]
    png = base64.b64decode(thumbnail["base64"])
    assert len(png) <= 8192 and b"private-owner" not in png
    assert ref["asset_id"] not in str(samples) and buyer.identity.did not in str(samples)
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG" and max(image.size) <= 192 and not image.info
