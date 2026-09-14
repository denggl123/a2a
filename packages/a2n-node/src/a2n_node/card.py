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
"""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from a2n_p2p import Identity, verify_pub
# 公钥解码 / 指纹推导 / 身份槽位**只有一份实现**（a2n-p2p.attest）：
# 无托管模式的卡自证与平台模式的计量验签共用同一处 —— 各自复制一遍，
# 同一把钥匙迟早会在两种模式下算出两个身份。
from a2n_p2p.attest import (SOV, card_did, card_pub_raw, did_from_pub, pub_b64,
                            pub_unb64, sovereign_ext)
from a2n_registry import card_hash as _registry_card_hash
from a2n_registry.service import validate_card


def card_body(card: dict) -> dict:
    """签名域：整张卡，只去掉 sov.sig 本身。"""
    c = copy.deepcopy(card or {})
    ((c.get("x-a2n") or {}).get(SOV) or {}).pop("sig", None)
    return c


def card_hash(card: dict) -> str:
    """与平台同一个卡哈希函数（含签名，即"最终背书的这张卡"）。"""
    return _registry_card_hash(card)


def verify_card(card: dict, *, require_endpoint: bool = False) -> tuple[bool, str]:
    """验卡三步：①形状 ②身份自洽 ③签名。

    第 ② 步是自证的命门：**卡不能自称一个不属于这把钥匙的身份**。
    没有这一步，任何人都能用自己的钥匙签一张写着别人 did 的卡，
    而"验签通过"会变成一句讽刺。
    """
    if not isinstance(card, dict):
        return False, "卡必须是 JSON 对象"
    try:
        validate_card(card)
    except Exception as e:  # noqa: BLE001 - 形状错照实说，不吞
        return False, f"卡形状不合规：{e}"

    sov = sovereign_ext(card)
    missing = [k for k in ("did", "pub", "sig") if not sov.get(k)]
    if missing:
        return False, "卡没有自证信息：x-a2n.sovereign 缺 " + "/".join(missing)

    pub_raw = card_pub_raw(card)
    if pub_raw is None:
        return False, "卡里的公钥无法解码"
    if did_from_pub(pub_raw) != sov["did"]:
        return False, "卡的 did 与公钥指纹不符（这张卡不是它的钥匙签的）"
    if not verify_pub(pub_raw, card_body(card), sov["sig"]):
        return False, "卡签名无效（内容被改过）"
    if require_endpoint and not card.get("url"):
        return False, "卡没有可直连地址（url 为空）：无托管模式没有中继可退"
    return True, "ok"


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
    card["x-a2n"][SOV]["sig"] = identity.sign(card_body(card))
    return card


def card_endpoint(card: dict) -> str | None:
    """该打哪里调用它。自持模式不留空：没有中继可退，留空就是不可调。"""
    return card.get("url") or None


def card_skills(card: dict) -> list[str]:
    return [s.get("id") for s in (card.get("skills") or []) if s.get("id")]


def offers(card: dict, skill: str) -> bool:
    return skill in card_skills(card)


def dump_card(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False, indent=2)
