"""反馈的签名 / 验签适配：把节点 ed25519 身份接成 `FeedbackBook` 要的一对函数。

为什么放 `a2n_node` 而不是 `a2n_sdk`：SDK 声明**零第三方依赖**（`dependencies = []`），
而 ed25519 走 `cryptography`。签名是**节点装配进来的能力**，SDK 只接受一对可注入的
`signer` / `verifier`（`tests/test_package_layers.py` 也要求 SDK 不反向依赖密码学库）。

口径与协调层一致：`proof = {pub, sig}`，无填充 URL-safe Base64；`did:a2n:ag_*`
必须等于 `pub` 的公钥指纹 —— 换把钥匙冒充别人的 did，验签直接不通过。
"""
from __future__ import annotations

import base64
from typing import Any, Callable

from a2n_p2p import fingerprint_of
from a2n_p2p.envelope import verify_pub


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def did_of(pub_raw: bytes) -> str:
    """公钥 → did。指纹口径是 `a2n_p2p.identity.fingerprint_of`（全项目唯一源）。"""
    return "did:a2n:ag_" + fingerprint_of(pub_raw)


def signer_for(identity) -> Callable[[dict], dict]:
    """`identity` 为 `a2n_p2p.Identity`：返回 `core -> Proof`。"""
    def sign(core: dict) -> dict[str, Any]:
        return {"pub": _b64(identity.pub_raw), "sig": identity.sign(core)}

    return sign


def verifier_for() -> Callable[[Any, dict], bool]:
    """返回 `(proof, core) -> bool`：验签，并核对 did 与公钥指纹一致。"""
    def verify(proof: Any, core: Any) -> bool:
        if not isinstance(proof, dict) or not isinstance(core, dict):
            return False
        pub = str(proof.get("pub") or "")
        sig = str(proof.get("sig") or "")
        did = str(core.get("author_did") or "")
        if not pub or not sig or not did:
            return False
        try:
            pub_raw = base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
        except (ValueError, TypeError):
            return False
        if did_of(pub_raw) != did:
            return False  # did 与公钥指纹不符：换钥匙冒充，拒绝
        return verify_pub(pub_raw, core, sig)

    return verify


def offer_feedback(book, *, task_id: str, direction: str, counterparty_did="", provider_did="") -> dict | None:
    """本节点对这**一笔任务**某方向**自己写的**当前反馈（若已写），供随终结消息捎带。

    只回本机自写（`source=self`）且通过签名自检的那一份；没写过就回 `None` ——
    绝不凭空造一份，也不把收到的对方反馈原样回传（那是二次传播，不是我的评价）。
    """
    if book is None or not task_id or not direction:
        return None
    try:
        rows = book.list(task_id=task_id, direction=direction, source="self", limit=10)
    except Exception:
        return None
    rows = [r for r in rows if (not counterparty_did or r.get("counterparty_did") == counterparty_did)
            and (not provider_did or r.get("provider_did") == provider_did) and book.verify_record(r)]
    return rows[0] if rows else None


def absorb_feedback(book, payload) -> dict | None:
    """接收端：从随终结消息捎带的载荷里取出 `feedback`，**验签后原样留存**。

    `payload` 可以是 metadata 字典（取其中的 `feedback` 键），也可以直接是那份反馈对象。
    验签通过 → `verified=true` 留存；失败 → `ingest` 自己只留痕 `verified=false`，
    这里不抛（一次坏捎带不许把整通调用带崩）。取不到有效对象 → 回 `None`。
    """
    if book is None or not isinstance(payload, dict):
        return None
    signed = payload.get("feedback") if "feedback" in payload else payload
    if not isinstance(signed, dict) or not signed.get("feedback_id"):
        return None
    try:
        return book.ingest(signed)
    except Exception:
        return None
