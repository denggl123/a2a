"""双边互签收据：**没有中心公证人时的刻章**。

平台模式下，"这件事发生过"由平台的凭证链背书（notary 写 receipts 表）。
无托管模式下没有第三方，于是背书必须由**交易的双方互相提供**：

    provider 交付后签一份收据 R   （证明：我按这个输入产出了这个输出）
    caller  收到后签一份回执 A(R) （证明：我收到了**这一份** R，没有别的）

闭环的结果是：**每一方都握着"带对方签名"的证据**。任何一方事后单独改写历史
（否认交付过、否认收到过），对方手里的那份副本立刻成为反证。这就是两方情形下
可达到的最强保证——它不等于全局共识（三方以上需要见证人，见 a2n-consensus），
但它不需要任何中心。

三条设计纪律：

  1. **签名覆盖整份 body**：少签一个字段，那个字段就可以被改而验签照样过
     （与 card.py 同一条纪律）。篡改检测不靠"挑几个关键字段比一比"。
  2. **验签用收据自带的公钥，但必须与 did 指纹对上**：否则任何人都能拿自己的
     钥匙签一份写着别人 did 的收据。自证的第一步永远是身份自洽。
  3. 回执签的是"**这一份收据的签名**"，不是把内容复述一遍。复述会把回执变成
     第二份事实（两份事实 = 谁都可以说自己那份是真的），引用则是单向依赖：
     回执只对那一份收据成立，换个收据就验不过。
"""
from __future__ import annotations

import copy

from a2n_kernel.hashing import canonical_json, sha256
from a2n_p2p import Identity, verify_pub

from .card import did_from_pub, pub_b64, pub_unb64

V = 1
_SIG_FIELDS = ("by", "pub", "sig")


def hash_payload(obj) -> str:
    """输入/输出的指纹。口径与全项目一致（canonical_json → sha256）。"""
    return sha256(canonical_json(obj if obj is not None else {}))


def make_body(*, task_id: str, caller_did: str, provider_did: str, skill: str,
              input_hash: str, output_hash: str, usage: dict | None = None,
              state: str = "accepted", ts: str = "") -> dict:
    return {
        "v": V,
        "task_id": task_id,
        "caller_did": caller_did,
        "provider_did": provider_did,
        "skill": skill,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "usage": usage or {"call_count": 1},
        "state": state,
        "ts": ts,
    }


def body_of(receipt: dict) -> dict:
    """从收据里剥出被签名的 body。验签必须用**原始 body** 重算，不做任何补全。"""
    return {k: v for k, v in (receipt or {}).items() if k not in _SIG_FIELDS}


def sign(identity: Identity, body: dict) -> dict:
    """provider（或 caller）对一份 body 签收据。签名域 = body 本身。"""
    r = dict(body)
    r["by"] = identity.did
    r["pub"] = pub_b64(identity.pub_raw)
    r["sig"] = identity.sign(body)
    return r


def verify(receipt: dict) -> tuple[bool, str]:
    """验一份收据：①字段齐 ②签名者身份自洽（did 由 pub 派生）③签名有效。"""
    if not isinstance(receipt, dict):
        return False, "收据必须是 JSON 对象"
    missing = [k for k in ("task_id", "caller_did", "provider_did", "skill",
                           "input_hash", "output_hash", "by", "pub", "sig")
               if not receipt.get(k)]
    if missing:
        return False, "收据缺字段：" + "/".join(missing)
    try:
        pub_raw = pub_unb64(receipt["pub"])
    except (ValueError, TypeError):
        return False, "收据里的公钥无法解码"
    if did_from_pub(pub_raw) != receipt["by"]:
        return False, "收据的 by 与公钥指纹不符（不是它的钥匙签的）"
    if not verify_pub(pub_raw, body_of(receipt), receipt["sig"]):
        return False, "收据签名无效（内容被改过）"
    if receipt["by"] not in (receipt["caller_did"], receipt["provider_did"]):
        return False, "签名者既不是调用方也不是供给方（第三方不得代签）"
    return True, "ok"


def signer_did(receipt: dict) -> str | None:
    return (receipt or {}).get("by")


def is_from(receipt: dict, did: str) -> bool:
    return signer_did(receipt) == did


def _ack_domain(of_sig: str, task_id: str, ack_by: str) -> dict:
    """回执的签名域。**只有这一个函数能构造它** —— 签与验各写一份的话，
    两份迟早会漂移，而漂移的表现是"回执忽然验不过"，最难查。"""
    return {"v": V, "of": of_sig, "task_id": task_id, "ack_by": ack_by}


def ack(identity: Identity, receipt: dict) -> dict:
    """收到方回执：确认收到**这一份**收据。

    签名域引用收据的 sig 字段（而不是复述内容）—— 引用是单向依赖，
    回执只对这一份收据成立；复述会造出第二份事实。
    """
    return {"v": V, "task_id": receipt.get("task_id"), "of": receipt.get("sig"),
            "by": identity.did, "pub": pub_b64(identity.pub_raw),
            "sig": identity.sign(_ack_domain(receipt.get("sig"), receipt.get("task_id"),
                                            identity.did))}


def verify_ack(ack: dict, receipt: dict) -> tuple[bool, str]:
    """验回执：必须确实指向**手上这一份收据**，且签名有效。"""
    if not isinstance(ack, dict) or not all(
            ack.get(key) for key in ("task_id", "of", "by", "pub", "sig")):
        return False, "回执必须是带签名的 JSON 对象"
    receipt_ok, receipt_why = verify(receipt)
    if not receipt_ok:
        return False, f"原收据无效：{receipt_why}"
    if receipt.get("by") != receipt.get("provider_did"):
        return False, "回执只能确认供给方签发的交付收据"
    if ack.get("of") != (receipt or {}).get("sig"):
        return False, "回执指向的不是这份收据（引用对不上）"
    if ack.get("task_id") != receipt.get("task_id"):
        return False, "回执任务与原收据不一致"
    if ack.get("by") != receipt.get("caller_did"):
        return False, "只有原调用方可以确认收到这份收据"
    try:
        pub_raw = pub_unb64(ack["pub"])
    except (ValueError, TypeError):
        return False, "回执里的公钥无法解码"
    if did_from_pub(pub_raw) != ack["by"]:
        return False, "回执的 by 与公钥指纹不符"
    domain = _ack_domain(ack.get("of"), ack.get("task_id"), ack["by"])
    if not verify_pub(pub_raw, domain, ack["sig"]):
        return False, "回执签名无效"
    return True, "ok"


def fingerprint(receipt: dict) -> str:
    """收据自身的指纹，用于本地链引用（把本地章与这份收据钉在一起）。"""
    return sha256(canonical_json(copy.deepcopy(receipt or {})))
