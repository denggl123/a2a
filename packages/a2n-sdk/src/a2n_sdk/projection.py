"""Agent Card 投影。

投影不是“改原卡地址后继续沿用原签名”。那样签名必然失效。这里始终生成一张新卡：

* 原卡保持原样，只留下哈希与原始声明者 DID 作为来源引用；
* 新卡的 URL 指向当前节点或本机 SDK；
* 新卡由投影者重新签名（签名器通过依赖注入提供）；
* 投影链有上限且禁止同一节点/角色重复，避免两个代理相互转发成环。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable
import uuid


MAX_PROJECTION_HOPS = 8
Signer = Callable[[dict[str, Any]], dict[str, Any]]


class ProjectionError(ValueError):
    pass


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def card_hash(card: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(card).encode("utf-8")).hexdigest()


def _source_did(card: dict[str, Any]) -> str | None:
    ext = card.get("x-a2n") or {}
    sov = ext.get("sovereign") or {}
    projection = ext.get("projection") or {}
    return sov.get("did") or projection.get("node_did")


def _projection_chain(source_card: dict[str, Any], node_did: str,
                      role: str, service_id: str) -> list[dict[str, str]]:
    ext = source_card.get("x-a2n") or {}
    previous = copy.deepcopy((ext.get("projection") or {}).get("chain") or [])
    if len(previous) >= MAX_PROJECTION_HOPS:
        raise ProjectionError(f"投影链超过 {MAX_PROJECTION_HOPS} 跳")
    hop_key = (node_did, role)
    if any((str(h.get("node_did")), str(h.get("role"))) == hop_key for h in previous):
        raise ProjectionError(f"检测到投影环：{node_did} 已经做过 {role} 投影")
    previous.append({"node_did": node_did, "role": role,
                     "service_id": service_id})
    return previous


def stable_service_id(node_did: str, source_card: dict[str, Any],
                      hint: str = "") -> str:
    seed = f"{node_did}\n{card_hash(source_card)}\n{hint}".encode("utf-8")
    return "svc_" + hashlib.sha256(seed).hexdigest()[:24]


def stable_projection_id(node_did: str, source_card: dict[str, Any],
                         target_ref: str) -> str:
    seed = f"{node_did}\n{card_hash(source_card)}\n{target_ref}".encode("utf-8")
    return "proj_" + hashlib.sha256(seed).hexdigest()[:24]


def _project(source_card: dict[str, Any], *, node_did: str, service_id: str,
             url: str, role: str, source_kind: str, target_ref: str | None,
             signer: Signer | None) -> dict[str, Any]:
    if not isinstance(source_card, dict) or not source_card.get("name"):
        raise ProjectionError("原始 Agent Card 缺少 name")
    if not source_card.get("skills"):
        raise ProjectionError("原始 Agent Card 缺少 skills")
    if not url.startswith(("http://", "https://")):
        raise ProjectionError("投影 URL 必须是 http:// 或 https://")

    original_hash = card_hash(source_card)
    original_did = _source_did(source_card)
    card = copy.deepcopy(source_card)
    ext = copy.deepcopy(card.get("x-a2n") or {})

    # 原始 sovereign 签名覆盖的是原卡。地址改变后继续携带它只会制造“看似有签名”的假象。
    original_sov = ext.pop("sovereign", None)
    chain = _projection_chain(source_card, node_did, role, service_id)
    ext["origin"] = {
        "card_hash": original_hash,
        "did": original_did,
        "self_attested": bool(original_sov and original_sov.get("sig")),
    }
    projection: dict[str, Any] = {
        "version": 1,
        "role": role,
        "node_did": node_did,
        "service_id": service_id,
        "source_kind": source_kind,
        "attested": signer is not None,
        "chain": chain,
    }
    if signer is None:
        projection["reason"] = "未注入节点签名器；只可作为开发或兼容投影"
    if target_ref:
        projection["target_ref"] = target_ref
    ext["projection"] = projection
    # 同一原始卡可被不同节点代理，uid 必须属于“节点 + 服务”，不能沿用原 uid 冲突。
    ext["uid"] = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                f"a2n:{node_did}:{role}:{service_id}"))
    ext["node_id"] = "ag_" + hashlib.sha256(ext["uid"].encode()).hexdigest()[:24]
    # 原始路由、认证说明属于上游；重新发布时不能泄露或要求买家配置上游密钥。
    ext["connection"] = {"mode": "direct", "url": url.rstrip("/")}
    for key in ("relay", "headers", "account_ref"):
        ext.pop(key, None)
    for key in ("additionalInterfaces", "security", "securitySchemes", "authentication"):
        card.pop(key, None)
    card["x-a2n"] = ext
    card["url"] = url.rstrip("/")
    card["preferredTransport"] = "JSONRPC"
    card.setdefault("capabilities", {})["streaming"] = False

    if signer:
        signed = signer(copy.deepcopy(card))
        if not isinstance(signed, dict):
            raise ProjectionError("签名器必须返回 Agent Card")
        sovereign = ((signed.get("x-a2n") or {}).get("sovereign") or {})
        if not sovereign.get("sig"):
            raise ProjectionError("签名器没有返回节点签名")
        card = signed
    return card


def supply_projection(source_card: dict[str, Any], *, node_did: str,
                      public_url: str, service_id: str | None = None,
                      source_kind: str = "local", signer: Signer | None = None) -> dict[str, Any]:
    """真实 Agent → 由当前节点负责交付的网络 Agent。"""
    sid = service_id or stable_service_id(node_did, source_card)
    return _project(source_card, node_did=node_did, service_id=sid,
                    url=public_url, role="supply", source_kind=source_kind,
                    target_ref=None, signer=signer)


def local_projection(network_card: dict[str, Any], *, node_did: str,
                     local_url: str, target_ref: str,
                     projection_id: str | None = None,
                     signer: Signer | None = None) -> tuple[str, dict[str, Any]]:
    """网络 Agent → 可复制进当前电脑 AI 工作台的 localhost Agent。"""
    pid = projection_id or stable_projection_id(node_did, network_card, target_ref)
    card = _project(network_card, node_did=node_did, service_id=pid,
                    url=local_url, role="consumer", source_kind="network",
                    target_ref=target_ref, signer=signer)
    return pid, card
