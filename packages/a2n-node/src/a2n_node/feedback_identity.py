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
