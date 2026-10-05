"""Signed node cards. Structural rules live in the SDK; identity proof in P2P."""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from a2n_p2p import Identity
# 以下名字是**转出口**（口径在别处，这里保留历史 import 路径）。
from a2n_p2p.attest import (SOV, card_body, card_did, card_pub_raw,  # noqa: F401
                            did_from_pub, pub_b64, pub_unb64, sovereign_ext)
from a2n_sdk.cards import validate_card
from a2n_kernel.hashing import canonical_json, sha256
from a2n_p2p.attest import verify_selfproof


def card_hash(card: dict) -> str:
    return sha256(canonical_json(card))


def verify_card(card: dict, *, require_endpoint: bool = False) -> tuple[bool, str]:
    try:
        validate_card(card)
    except (ValueError, TypeError, AttributeError) as exc:
        return False, f"卡形状不合规：{exc}"
    ok, why = verify_selfproof(card)
    if not ok:
        return False, why
    if require_endpoint and not card.get("url"):
        return False, "卡没有可直连地址"
    return True, "ok"


def sign_card(identity: Identity, card: dict, *, port: int = 0,
              p2p_port: int = 0) -> dict:
    """以当前节点身份签一张完整卡，供 SDK 的投影签名器直接注入。

    原卡若带别人的 sovereign 块会先被替换。签名域仍由 ``card_body`` 唯一实现，
    不在 SDK 里复制加密口径。
    """
    out = copy.deepcopy(card)
    ext = out.setdefault("x-a2n", {})
    ext[SOV] = {"did": identity.did, "pub": pub_b64(identity.pub_raw),
                "port": int(port or 0), "p2p_port": int(p2p_port or 0), "sig": ""}
    ext[SOV]["sig"] = identity.sign(card_body(out))
    return out


def build_card(identity: Identity, *, name: str, skills: list[Any],
               host: str = "127.0.0.1", port: int = 0, p2p_port: int = 0,
               region: str = "self-hosted", description: str = "",
               metering: list[dict] | None = None) -> dict:
    """造一张自证的卡。

    node_id 固定为身份指纹，不是随机 mint 的 —— 日后接入平台时，
    agent_id 与这里的身份是同一个，迁移不需要网络重新认识一个人。
    """
    norm: list[dict] = []
    for s in skills:
        norm.append({"id": s, "name": s} if isinstance(s, str) else dict(s))
    endpoint = f"http://{host}:{port}" if port else ""
    card: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "description": description or f"{name}（自持节点）",
        "url": endpoint,
        "skills": norm,
        "x-a2n": {
            "uid": str(uuid.uuid4()),
            "node_id": identity.node_id,
            "deployment": {"region": region},
            # direct：节点自己守门。平台模式下 direct 节点验 X-A2N-Call（共享密钥），
            # 无托管模式下验的是调用方的 DID 签名 —— 更硬，且不需要任何共享秘密。
            "connection": {"mode": "direct", "url": endpoint},
            "metering": {"dimensions": metering or [
                {"key": "call_count", "unit": "call", "verifiable": True}]},
            SOV: {"did": identity.did, "pub": pub_b64(identity.pub_raw),
                  "port": port, "p2p_port": p2p_port, "sig": ""},
        },
    }
    return sign_card(identity, card, port=port, p2p_port=p2p_port)


def card_endpoint(card: dict) -> str | None:
    """该打哪里调用它。自持模式不留空：没有中继可退，留空就是不可调。"""
    return card.get("url") or None


def card_skills(card: dict) -> list[str]:
    return [s.get("id") for s in (card.get("skills") or []) if s.get("id")]


def offers(card: dict, skill: str) -> bool:
    return skill in card_skills(card)


def dump_card(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False, indent=2)
