"""C0 身份测试 —— 协调层三类对象的签名 / 验签与严格解析。

守的是 ``a2n_node.coord_identity``：NodeRecord 自签、Referral 引荐签名（含目标
记录自签与哈希绑定）、信封签名（含收件人绑定与时效）、严格 JSON。

这些测试**不验证"目标在线"或"质量合格"** —— 签名只证明"这是谁发的、内容没被改"。
"""
from __future__ import annotations

import pytest

from a2n_node import coord_identity as ci
from a2n_p2p import Identity

ROUTES_B = [{"channel_type": "direct_https", "endpoint": "https://b.example/public/v1/coord"}]
ROUTES_C = [{"channel_type": "direct_https", "endpoint": "https://c.example/public/v1/coord"}]


@pytest.fixture()
def peers():
    return Identity.generate(), Identity.generate(), Identity.generate()


# ---------------------------------------------------------------- NodeRecord


def test_node_record_self_signs_and_verifies(peers):
    b, _c, _d = peers
    rec = ci.node_record(b, coord_routes=ROUTES_B)
    assert rec["node_did"] == b.did
    assert ci.verify_node_record(rec) is True
    assert set(rec["operations"]) == set(ci.OPERATIONS)


def test_node_record_tamper_is_rejected(peers):
    b, _c, _d = peers
    rec = ci.node_record(b, coord_routes=ROUTES_B)
    assert ci.verify_node_record(dict(rec, operations=["HELLO"])) is False


def test_node_record_did_must_match_public_key(peers):
    """拿自己的签名冒充别人的 did（did 与公钥指纹不符）必须拒绝。"""
    b, _c, d = peers
    rec = ci.node_record(b, coord_routes=ROUTES_B)
    rec["node_did"] = d.did
    assert ci.verify_node_record(rec) is False


def test_node_record_expiry_boundary(peers):
    """过期边界：now >= expires_at 即无效（COORDINATION-API §1.1）。"""
    b, _c, _d = peers
    rec = ci.node_record(b, coord_routes=ROUTES_B, ttl=10, now=1000)
    assert ci.verify_node_record(rec, now=1009) is True
    assert ci.verify_node_record(rec, now=1010) is False  # 边界即失效


# ---------------------------------------------------------------- Referral


def test_referral_roundtrip_and_target_self_signature(peers):
    b, c, _d = peers
    target = ci.node_record(c, coord_routes=ROUTES_C)
    ref = ci.referral(b, target, skill_hint=["video-edit"])
    assert ref["introducer_did"] == b.did
    assert ref["target_record"]["node_did"] == c.did
    assert ci.verify_referral(ref) is True


def test_referral_rejects_unsigned_target(peers):
    """引荐的目标记录必须**自签**；引荐者签名不替目标背书。"""
    b, c, _d = peers
    target = dict(ci.node_record(c, coord_routes=ROUTES_C), proof={})
    with pytest.raises(ValueError):
        ci.referral(b, target)


def test_referral_rejects_swapped_target_record(peers):
    b, c, d = peers
    ref = ci.referral(b, ci.node_record(c, coord_routes=ROUTES_C))
    ref["target_record"] = ci.node_record(d, coord_routes=ROUTES_C)  # 换了目标，哈希对不上
    assert ci.verify_referral(ref) is False
    ref2 = ci.referral(b, ci.node_record(c, coord_routes=ROUTES_C))
    ref2["target_record_hash"] = "deadbeef"
    assert ci.verify_referral(ref2) is False


def test_referral_tamper_and_expiry(peers):
    b, c, _d = peers
    ref = ci.referral(b, ci.node_record(c, coord_routes=ROUTES_C), ttl=5, now=2000)
    assert ci.verify_referral(dict(ref, observation="trust_me")) is False
    assert ci.verify_referral(ref, now=2004) is True
    assert ci.verify_referral(ref, now=2005) is False


# ---------------------------------------------------------------- 信封


def test_envelope_signs_and_verifies_with_recipient_binding(peers):
    b, c, _d = peers
    env = ci.coord_envelope(b, "FIND", {"skill": "video-edit", "page_size": 10},
                            recipient_did=c.did, ttl=5, issued_at=3000)
    assert env["domain"] == ci.DOMAIN and env["v"] == 1 and env["sender_did"] == b.did
    assert ci.verify_coord_envelope(env, now=3001, recipient_did=c.did) is True
    # 收件人绑定：发给 c 的信封不能当成发给 b
    assert ci.verify_coord_envelope(env, now=3001, recipient_did=b.did) is False


def test_envelope_tamper_body_and_type_rejected(peers):
    b, c, _d = peers
    env = ci.coord_envelope(b, "FIND", {"skill": "video-edit"}, recipient_did=c.did,
                            issued_at=3000)
    assert ci.verify_coord_envelope(dict(env, body={"skill": "evil"}), now=3001) is False
    assert ci.verify_coord_envelope(dict(env, type="PROBE"), now=3001) is False
    assert ci.verify_coord_envelope(dict(env, sender_did=c.did), now=3001) is False


def test_envelope_expiry_and_future_skew_rejected(peers):
    b, c, _d = peers
    env = ci.coord_envelope(b, "HELLO", {}, recipient_did=c.did, issued_at=4000, ttl=5)
    assert ci.verify_coord_envelope(env, now=4004) is True
    assert ci.verify_coord_envelope(env, now=4005) is False       # 过期
    assert ci.verify_coord_envelope(env, now=3000) is False       # 显著超前（skew=300 挡不住 1000）
    assert ci.verify_coord_envelope(env, now=4000 - 200) is True  # 轻微超前在容差内


def test_envelope_unknown_type_refused(peers):
    b, _c, _d = peers
    with pytest.raises(ValueError):
        ci.coord_envelope(b, "TELEPORT", {})


# ---------------------------------------------------------------- 严格解析


def test_loads_strict_rejects_duplicate_keys_and_non_finite():
    with pytest.raises(ValueError):
        ci.loads_strict('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        ci.loads_strict('{"a": NaN}')
    with pytest.raises(ValueError):
        ci.loads_strict('{"a": Infinity}')
    with pytest.raises(ValueError):
        ci.loads_strict('[1, 2]')  # 顶层必须是对象
    assert ci.loads_strict('{"a": 1}') == {"a": 1}


def test_loads_strict_output_feeds_verification(peers):
    """坏输入必须挡在验签之前：解析出的对象能直接送进验签。"""
    b, c, _d = peers
    env = ci.coord_envelope(b, "HELLO", {}, recipient_did=c.did, issued_at=5000)
    import json
    parsed = ci.loads_strict(json.dumps(env, ensure_ascii=False))
    assert ci.verify_coord_envelope(parsed, now=5001, recipient_did=c.did) is True


# ---------------------------------------------------------------- 能力声明


def test_capability_declaration_separates_optional_services(peers):
    """基本发现随入网启用；见证 / 任务中继 / 大文件缓存**各自声明**，不并成一个总开关。"""
    b, _c, _d = peers
    cap = ci.capability_declaration(b, coord_routes=ROUTES_B, witness=True)
    assert cap["node_did"] == b.did
    assert cap["services"] == {"discovery": True, "witness": True,
                               "task_relay": False, "blob_cache": False}
    assert set(cap["services"]) == {"discovery", *ci.OPTIONAL_SERVICES}
    assert ci.verify_node_record(cap["record"]) is True
    assert cap["limits"]["max_response_bytes"] > 0


def test_capability_declaration_limits_override(peers):
    b, _c, _d = peers
    cap = ci.capability_declaration(b, coord_routes=ROUTES_B,
                                    limits={"rate_per_second": 3})
    assert cap["limits"]["rate_per_second"] == 3
    assert cap["limits"]["burst"] == ci.DEFAULT_LIMITS["burst"]  # 未覆盖的取默认
