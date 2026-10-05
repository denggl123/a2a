"""协调错误口径：认证后必须回**已签名** ERROR 封套，而不是裸 HTTP 码（契约 §7）。

背景（2026-10-05 收口发现）：限流与容量判断发生在验签**之后**，原本却以未签名
JSON + 裸 429/400 回给调用方 —— 对端只看得到 HTTP 码，真实的
``RATE_LIMITED`` / ``BUSY`` / ``DEADLINE_EXCEEDED`` 被吞掉，甚至会被当成身份问题。
这里锁住两条线：

* 已认证（验签通过、sender DID 可信）⇒ 失败回签名 ERROR 封套，带正确状态码；
* 未认证（验签失败）⇒ 只回最小 JSON，不泄露节点目录，也不为其签名。
"""
import base64
from collections import deque
import os
import time

import pytest

from a2n_node.coord_identity import verify_coord_envelope
from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.coordination import error_code_of, query_fingerprint


def _find_body():
    return {"skill": "echo", "coarse_requirements": {}, "page_size": 10, "cursor": None,
            "query_fingerprint": query_fingerprint("echo", {}, 10), "max_response_bytes": 65536}


@pytest.fixture()
def pair(tmp_path):
    """一个公共协调服务端 + 一个已握手的调用方，都是真实回环 HTTP。"""
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    server = Daemon(tmp_path / "server", port=0, protector=protector).start()
    client = None
    try:
        base = server.runtime.local_base_url
        client = Daemon(tmp_path / "client", port=0, protector=protector,
                        public_nodes=[base], coord_allow_networks=["127.0.0.1/32"]).start()
        endpoint = base.rstrip("/") + "/public/v1/coord"
        session, _ = client.coord_network.handshake({"endpoint": endpoint}, 16384, 3)
        yield server, client, session, endpoint
    finally:
        if client:
            client.stop()
        server.stop()


def _post(client, endpoint, suffix, request):
    return client.coord_network._request(endpoint + "/" + suffix, request, cap=65536, timeout=3)


def test_rate_limited_after_auth_is_a_signed_error(pair):
    server, client, session, endpoint = pair
    request = client.coord_network._envelope("FIND", _find_body(), session["node_did"])
    # 把该发送者的 60 秒窗口塞满 ⇒ 下一条必然限流（确定性，不靠真打 121 次）
    server.public_coordination._rates[client.identity.did] = deque([time.monotonic()] * 120)

    data, _count, status = _post(client, endpoint, "find", request)

    assert status == 429
    assert data["type"] == "ERROR"
    assert data["recipient_did"] == client.identity.did        # 绑给原请求者
    assert data["in_reply_to"] == request["request_id"]
    assert verify_coord_envelope(data, now=time.time(), recipient_did=client.identity.did)
    assert data["body"]["code"] == "RATE_LIMITED"
    assert data["body"]["message"]                              # 契约 §7 的 message
    assert data["body"]["retry_after_seconds"] >= 1

    # 调用方拿到真实原因，而不是裸 "HTTP 429"，更不是身份错误
    with pytest.raises(ValueError) as excinfo:
        client.coord_network._unwrap(data, status, request, session["node_did"])
    assert "RATE_LIMITED" in str(excinfo.value)
    assert not isinstance(excinfo.value, PermissionError)


def test_authenticated_not_found_is_a_signed_404(pair):
    server, client, session, endpoint = pair
    request = client.coord_network._envelope("GET_CARD", {
        "key": {"provider_did": "did:a2n:ag_" + "1" * 24, "service_id": "svc_missing"},
        "card_hash": "0" * 64}, session["node_did"])

    data, _count, status = _post(client, endpoint, "card", request)

    assert status == 404
    assert data["type"] == "ERROR" and data["body"]["code"] == "NOT_FOUND"
    assert verify_coord_envelope(data, now=time.time(), recipient_did=client.identity.did)


def test_unauthenticated_request_gets_minimal_unsigned_json(pair):
    server, client, session, endpoint = pair
    request = client.coord_network._envelope("FIND", _find_body(), session["node_did"])
    forged = {**request, "sender_did": "did:a2n:ag_" + "2" * 24}  # DID 与公钥不再对应

    data, _count, status = _post(client, endpoint, "find", forged)

    assert status == 401
    assert data["code"] == "UNVERIFIED_IDENTITY"
    # 未认证不许拿到签名封套（那等于用节点身份为陌生人的请求背书）
    assert data.get("type") is None and "proof" not in data


def test_error_code_extraction_only_accepts_known_codes():
    assert error_code_of(TimeoutError("RATE_LIMITED: 太快"), "DEADLINE_EXCEEDED") == "RATE_LIMITED"
    assert error_code_of(TimeoutError("BUSY: 满了"), "DEADLINE_EXCEEDED") == "BUSY"
    assert error_code_of(ValueError("NOT_FOUND: 没有"), "INVALID_REQUEST") == "NOT_FOUND"
    # 前缀不是已知码就退回 default，不把任意文本当错误码
    assert error_code_of(TimeoutError("随便一段话"), "DEADLINE_EXCEEDED") == "DEADLINE_EXCEEDED"
    assert error_code_of(ValueError("nonsense: x"), "INVALID_REQUEST") == "INVALID_REQUEST"
