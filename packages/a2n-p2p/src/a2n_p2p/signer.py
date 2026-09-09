"""可注入的 ed25519 签名器：给需要"签名"但不需要"网络"的模块用。

为什么单独抽出来：共识层与凭证层都需要签名，但它们不该反过来依赖
网络层（P2PNode）或某个具体节点身份。签名是一种能力，不是一个角色。

同一套密钥材料既可以做见证人、也可以做锚签名方 —— 区别只在于
**它被登记在哪个名单里**（见证人集合 / 资金锚公钥），而不在于密钥本身。
"""
from __future__ import annotations

import base64
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    """持有私钥的一方。私钥只在进程内存里，不落盘、不出现在 repr。"""

    def __init__(self, private_key: ed25519.Ed25519PrivateKey | None = None) -> None:
        self._sk = private_key or ed25519.Ed25519PrivateKey.generate()
        self._pk = self._sk.public_key()

    @classmethod
    def generate(cls) -> "Signer":
        return cls()

    @classmethod
    def from_private_b64(cls, raw: str) -> "Signer":
        return cls(ed25519.Ed25519PrivateKey.from_private_bytes(_unb64(raw)))

    @property
    def pub(self) -> str:
        return _b64(self._pk.public_bytes(encoding=serialization.Encoding.Raw,
                                          format=serialization.PublicFormat.Raw))

    @property
    def private_b64(self) -> str:
        return _b64(self._sk.private_bytes(encoding=serialization.Encoding.Raw,
                                           format=serialization.PrivateFormat.Raw,
                                           encryption_algorithm=serialization.NoEncryption()))

    @property
    def did(self) -> str:
        import hashlib

        return "did:a2n:ag_" + hashlib.sha256(_unb64(self.pub)).hexdigest()[:24]

    def sign(self, msg: str) -> str:
        return _b64(self._sk.sign(msg.encode("utf-8")))

    def signer_fn(self) -> Callable[[str], str]:
        return self.sign

    def __repr__(self) -> str:  # 私钥绝不出现在 repr 里
        return f"<Signer {self.did}>"


def verify(pub_b64: str, msg: str, sig: str) -> bool:
    """验签。(pub, msg, sig) 三元组 —— 与共识层的 Verifier 协议一致。"""
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(_unb64(pub_b64)).verify(
            _unb64(sig), msg.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


def verifier_fn() -> Callable[[str, str, str], bool]:
    """返回共识层需要的 verifier: (pub, msg, sig) -> bool。"""
    return lambda pub, msg, sig: verify(pub, msg, sig)
