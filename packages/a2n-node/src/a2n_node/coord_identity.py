"""协调层身份适配：用节点 ed25519 身份签 / 验 ``a2n-coord/1`` 的三类对象。

为什么放 ``a2n_node`` 而不是 ``a2n_sdk``：SDK 声明**零第三方依赖**，而 ed25519 走
``cryptography``。签名是**节点装配进来的能力**，SDK 只定义形状与 ``Protocol``
（``a2n_sdk.coordination``）；``tests/test_package_layers.py`` 守着这条线。

三类对象、三个签名域，口径与全仓一致（复用 ``a2n_p2p.envelope.verify_pub`` +
``fingerprint_of``，Proof 为无填充 URL-safe Base64）：

* ``NodeRecord`` —— 节点自签的协调入口声明，域 ``a2n-coord-node/1``。
* ``Referral``   —— 引荐者签名的观察，域 ``a2n-coord-referral/1``；目标记录仍须**自签**，
  引荐者的签名只覆盖"我观察到它"，不替目标背书。
* 信封（envelope）—— 六项逻辑操作，域 ``a2n-coord/1``；签名覆盖整份封套去 proof。

``sender_did`` / ``node_did`` / ``introducer_did`` 一律要求等于 ``proof.pub`` 的指纹
（``did:a2n:ag_<指纹>``）—— 换把钥匙冒充别人的 did，验签直接不通过。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from a2n_p2p.envelope import verify_pub

from a2n_sdk.coordination import (
    DOMAIN,
    ENVELOPE_TYPES,
    ENVELOPE_V,
    NODE_RECORD_DOMAIN,
    OPERATIONS,
    REFERRAL_DOMAIN,
    SUPPORTED_VERSIONS,
    CoordRoute,
    NodeRecord,
    Referral,
    record_hash,
)

from .feedback_identity import did_of

#: 可分离声明的可选公共服务（迁移规则 §15.2）。基本发现随入网启用，不能与它们
#: 搅成一个总开关：见证、任务中继、大文件缓存各自声明能力与额度。
OPTIONAL_SERVICES = ("witness", "task_relay", "blob_cache")

#: HELLO 应答里的 ``limits`` 建议初值（接受大小与速率）。**配置化，非实测容量**。
DEFAULT_LIMITS: dict[str, Any] = {
    "max_request_bytes": 65_536,
    "max_response_bytes": 65_536,
    "rate_per_second": 1,
    "burst": 4,
    "local_rate_per_second": 8,
    "local_burst": 16,
}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pub_raw(proof: Any) -> bytes | None:
    if not isinstance(proof, dict):
        return None
    pub = str(proof.get("pub") or "")
    if not pub:
        return None
    try:
        return base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
    except (ValueError, TypeError):
        return None


def _sign_core(identity, core: dict[str, Any]) -> dict[str, str]:
    return {"pub": _b64(identity.pub_raw), "sig": identity.sign(core)}


def _verify_core(proof: Any, core: dict[str, Any], expect_did: str) -> bool:
    """验一段带域的签名：did 必须等于 pub 指纹，且签名覆盖整份 core。"""
    pub_raw = _pub_raw(proof)
    if pub_raw is None or not expect_did or not isinstance(core, dict):
        return False
    if did_of(pub_raw) != expect_did:
        return False  # 换钥匙冒充别人的 did：拒绝
    return verify_pub(pub_raw, core, str(proof.get("sig") or ""))


# ---------------------------------------------------------------- NodeRecord


def node_record(identity, *, coord_routes: list[dict[str, Any] | CoordRoute],
                operations: list[str] | None = None,
                versions: list[str] | None = None, ttl: int = 600,
                now: float | None = None) -> dict[str, Any]:
    """构造并自签一份 ``NodeRecord``（返回可直接上网的 dict）。"""
    issue = int(now if now is not None else time.time())
    routes = [r if isinstance(r, CoordRoute) else CoordRoute(**r) for r in coord_routes]
    record = NodeRecord(
        node_did=identity.did,
        versions=list(versions or SUPPORTED_VERSIONS),
        coord_routes=routes,
        operations=list(operations or OPERATIONS),
        issued_at=issue,
        expires_at=issue + max(1, int(ttl)),
    )
    record.proof = _sign_core(identity, record.proof_core())
    return record.to_dict()


def verify_node_record(record: Any, *, now: float | None = None) -> bool:
    """验一份 NodeRecord：自签有效，且（给了 now 时）未过期。"""
    if not isinstance(record, dict):
        return False
    try:
        parsed = NodeRecord.from_dict(record)
    except (ValueError, TypeError):
        return False
    if not _verify_core(parsed.proof, parsed.proof_core(), parsed.node_did):
        return False
    if now is not None and float(now) >= parsed.expires_at:
        return False
    return True


# ---------------------------------------------------------------- Referral


def referral(identity, target_record: dict[str, Any], *, skill_hint: list[str] | None = None,
             observation: str = "recently_connected", ttl: int = 60,
             now: float | None = None) -> dict[str, Any]:
    """构造并自签一份 ``Referral``：绑定目标记录**及其哈希**。

    二次转发必须原样保留该签名与目标记录，不能重签后抹掉原始声明者
    （规则 §4：引荐线索类型须区分"我直接观察过"和"我转收到"）。
    """
    if not verify_node_record(target_record):
        raise ValueError("引荐的 target_record 未通过自签校验，不能引荐")
    issue = int(now if now is not None else time.time())
    parsed_target = NodeRecord.from_dict(target_record)
    item = Referral(
        introducer_did=identity.did,
        target_record=parsed_target,
        target_record_hash=record_hash(parsed_target),
        skill_hint=list(skill_hint or []),
        observation=str(observation),
        observed_at=issue,
        expires_at=issue + max(1, int(ttl)),
    )
    item.proof = _sign_core(identity, item.proof_core())
    return item.to_dict()


def verify_referral(item: Any, *, now: float | None = None) -> bool:
    """验一份 Referral：引荐者签名有效 + 目标记录自签有效 + 哈希对得上 + 未过期。

    注意：通过 ≠ 目标在线，也 ≠ 目标质量合格 —— 只证明引荐者声明过这次观察。
    """
    if not isinstance(item, dict):
        return False
    try:
        parsed = Referral.from_dict(item)
    except (ValueError, TypeError):
        return False
    if not _verify_core(parsed.proof, parsed.proof_core(), parsed.introducer_did):
        return False
    target = parsed.target_record.to_dict()
    if not verify_node_record(target, now=now):
        return False
    if record_hash(target) != parsed.target_record_hash:
        return False  # target_record 被换过（哈希对不上）
    if parsed.introducer_did == parsed.target_record.node_did:
        return False
    if now is not None and float(now) >= parsed.expires_at:
        return False
    return True


# ---------------------------------------------------------------- 信封


def coord_envelope(identity, type: str, body: dict[str, Any], *, recipient_did: str = "",
                   request_id: str | None = None, in_reply_to: str = "",
                   issued_at: int | None = None, ttl: int = 5) -> dict[str, Any]:
    """构造并签一份协调信封。``domain`` / ``type`` / 时效 / body 每一字段都被签名覆盖。"""
    if type not in ENVELOPE_TYPES:
        raise ValueError(f"未知协调消息类型：{type}")
    issue = int(issued_at if issued_at is not None else time.time())
    envelope = {
        "v": ENVELOPE_V,
        "domain": DOMAIN,
        "type": type,
        "request_id": request_id or f"req_{issue}_{identity.did[-8:]}",
        "in_reply_to": in_reply_to,
        "sender_did": identity.did,
        "recipient_did": recipient_did or None,
        "issued_at": issue,
        "expires_at": issue + max(1, int(ttl)),
        "body": body if isinstance(body, dict) else {},
    }
    core = {k: v for k, v in envelope.items() if k != "proof"}
    envelope["proof"] = _sign_core(identity, core)
    return envelope


def verify_coord_envelope(envelope: Any, *, now: float | None = None,
                          recipient_did: str | None = None,
                          skew: int = 300) -> bool:
    """验一份协调信封：结构 + 签名 + 时效 + （给了时）收件人绑定。

    ``skew`` 是允许的时钟超前量（秒）：显著超前于本机时间的消息不予采信。
    """
    if not isinstance(envelope, dict):
        return False
    if envelope.get("v") != ENVELOPE_V or envelope.get("domain") != DOMAIN:
        return False
    if envelope.get("type") not in ENVELOPE_TYPES:
        return False
    sender = str(envelope.get("sender_did") or "")
    core = {k: v for k, v in envelope.items() if k != "proof"}
    if not _verify_core(envelope.get("proof"), core, sender):
        return False
    if recipient_did is not None:
        if str(envelope.get("recipient_did") or "") != recipient_did:
            return False
    if now is not None:
        current = float(now)
        try:
            issued = float(envelope.get("issued_at") or 0)
            expires = float(envelope.get("expires_at") or 0)
        except (TypeError, ValueError):
            return False
        if current >= expires or issued > current + skew:
            return False
    return True


# ---------------------------------------------------------------- 严格解析


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"重复的 JSON 键：{key}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"不接受非有限数值：{name}")


def loads_strict(text: str | bytes) -> dict[str, Any]:
    """严格 JSON 解析：拒绝重复键、NaN / Infinity（API §4.2 要求）。

    这些是"能无损往返"的前提；坏输入必须被挡在验签之前，而不是验完才发现。
    """
    obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    if not isinstance(obj, dict):
        raise ValueError("协调报文顶层必须是对象")
    return obj


# ---------------------------------------------------------------- 能力声明


def capability_declaration(identity, *, coord_routes: list[dict[str, Any] | CoordRoute],
                           discovery: bool = True, witness: bool = False,
                           task_relay: bool = False, blob_cache: bool = False,
                           limits: dict[str, Any] | None = None, ttl: int = 600,
                           now: float | None = None) -> dict[str, Any]:
    """节点能力声明：基本发现随入网启用；可选服务**各自声明**，不并成一个总开关。

    返回本机自述（供 HELLO 应答与本地诊断）；``record`` 是可上网的自签 NodeRecord，
    ``services`` 是迁移规则 §15.2 要求的可分离能力位。
    """
    record = node_record(identity, coord_routes=coord_routes, ttl=ttl, now=now)
    return {
        "node_did": identity.did,
        "versions": list(SUPPORTED_VERSIONS),
        "operations": list(OPERATIONS),
        "record": record,
        "services": {
            "discovery": bool(discovery),
            "witness": bool(witness),
            "task_relay": bool(task_relay),
            "blob_cache": bool(blob_cache),
        },
        "limits": {**DEFAULT_LIMITS, **(limits or {})},
    }


__all__ = [
    "OPTIONAL_SERVICES", "DEFAULT_LIMITS",
    "node_record", "verify_node_record", "referral", "verify_referral",
    "coord_envelope", "verify_coord_envelope", "loads_strict",
    "capability_declaration",
]
