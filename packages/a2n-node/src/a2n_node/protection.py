"""Deployment-specific protection adapters; cryptography stays outside the SDK."""
from __future__ import annotations

import base64
import os

from a2n_sdk.protection import WindowsProtector


class EnvironmentProtector:
    """Containers receive a 32-byte key via a secret environment injection."""

    def __init__(self, encoded_key: str):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except ValueError:
            raise ValueError("A2N_STORAGE_KEY 必须是 32 字节密钥的 base64 编码") from None
        if len(key) != 32:
            raise ValueError("A2N_STORAGE_KEY 必须是 32 字节密钥的 base64 编码")
        self._cipher = AESGCM(key)

    def seal(self, raw: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, raw, b"a2n-local-store-v1")

    def open(self, sealed: bytes) -> bytes:
        return self._cipher.decrypt(sealed[:12], sealed[12:], b"a2n-local-store-v1")


def system_protector():
    if os.environ.get("A2N_STORAGE_KEY"):
        return EnvironmentProtector(os.environ["A2N_STORAGE_KEY"])
    if os.name == "nt":
        return WindowsProtector()
    raise RuntimeError("请通过容器 secret 注入 A2N_STORAGE_KEY；节点不会明文保存凭据")
