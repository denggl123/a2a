"""Agent Card 的自证：验卡不需要任何机构。

平台模式下，卡的有效性由平台背书（registry 校验 + card_hash 入库）。
无托管模式下没有第三方可背书，所以背书必须**长在卡里**：

    did  = did:a2n:ag_<sha256(pub)[:24]>    身份 = 公钥指纹，不需要谁分配
    pub  = 卡自带的公钥                       验签不必先交换密钥
    sig  = 用对应私钥对"整张卡去掉签名"的签名   谁签的一目了然

于是"注册"从"申请账号"退化成"广播一张自证的卡"——**中心服务器在身份这一环的
存在理由被彻底去掉**。这与 a2n-p2p/identity 是同一个主张，这里把它落到 Agent
Card 上：网络层用 DID 找节点，业务层用同一把钥匙认这张卡。

两条纪律：

  1. card_hash 复用 a2n-registry 的同一个函数，指纹复用 a2n-p2p 的同一个函数，
     验签复用 a2n-p2p.envelope.verify_pub。**不在本包复制任何哈希/加密口径** ——
     否则同一张卡在两种模式下会有两个哈希，同一把钥匙会算出两个身份。
  2. 签名域是"整张卡去掉 sig"，不是卡的子集。少签一个字段 = 那个字段可以被人改，
     而验签照样通过。

**实现只有一份**：验卡三步与签名域后来被平台发现路也要用（P2：验不过的卡不许
出现在"可直接调用"里），于是搬到了 `a2n_registry.service.verify_card` /
`a2n_p2p.attest.card_body`，本模块只做**转出口**。两条路各写一遍，同一张卡迟早会
得到两个结论 —— 这正是要杜绝的。
"""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from a2n_p2p import Identity
# 以下名字是**转出口**（口径在别处，这里保留历史 import 路径）。
from a2n_p2p.attest import (SOV, card_body, card_did, card_pub_raw,  # noqa: F401
                            did_from_pub, pub_b64, pub_unb64, sovereign_ext)
from a2n_registry import card_hash                       # noqa: F401
from a2n_registry.service import validate_card, verify_card   # noqa: F401


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
