"""End-to-end sealed envelopes for an optional, untrusted HTTP courier."""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROTOCOL = "a2n-sealed-relay/1"
MAX_PLAINTEXT = 1_000_000


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str, *, max_chars: int = 2_000) -> bytes:
    if not isinstance(value, str) or len(value) > max_chars:
        raise ValueError("中继密文编码无效")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def valid_public(value: str) -> bool:
    try:
        return len(_unb64(value)) == 32
    except (ValueError, TypeError):
        return False


def private_from_seed(seed: bytes):
    if len(seed) != 32:
        raise ValueError("中继加密种子必须是 32 字节")
    return x25519.X25519PrivateKey.from_private_bytes(seed)


def public_b64(private) -> str:
    return _b64(private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _key(private, public_raw: bytes) -> bytes:
    public = x25519.X25519PublicKey.from_public_bytes(public_raw)
    shared = private.exchange(public)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"a2n-sealed-relay/1").derive(shared)


def _pack(value: dict) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_PLAINTEXT:
        raise ValueError("中继请求或应答过大")
    return raw


def seal_request(provider_pub: str, value: dict, *, aad: bytes) -> tuple[dict, bytes]:
    ephemeral = x25519.X25519PrivateKey.generate()
    key = _key(ephemeral, _unb64(provider_pub))
    nonce = os.urandom(12)
    return {"v": 1, "epk": public_b64(ephemeral), "nonce": _b64(nonce),
            "ciphertext": _b64(AESGCM(key).encrypt(nonce, _pack(value), aad))}, key


def open_request(private, envelope: dict, *, aad: bytes) -> tuple[dict, bytes]:
    if not isinstance(envelope, dict) or set(envelope) != {
            "v", "epk", "nonce", "ciphertext"} or envelope.get("v") != 1:
        raise ValueError("中继请求封套无效")
    key = _key(private, _unb64(envelope["epk"]))
    nonce = _unb64(envelope["nonce"])
    if len(nonce) != 12:
        raise ValueError("中继 nonce 无效")
    raw = AESGCM(key).decrypt(nonce, _unb64(
        envelope["ciphertext"], max_chars=1_400_000), aad)
    if len(raw) > MAX_PLAINTEXT:
        raise ValueError("中继请求过大")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("中继请求必须是对象")
    return value, key


def seal_response(key: bytes, value: dict, *, aad: bytes) -> dict:
    nonce = os.urandom(12)
    return {"v": 1, "nonce": _b64(nonce),
            "ciphertext": _b64(AESGCM(key).encrypt(nonce, _pack(value), aad))}


def open_response(key: bytes, envelope: dict, *, aad: bytes) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {
            "v", "nonce", "ciphertext"} or envelope.get("v") != 1:
        raise ValueError("中继应答封套无效")
    nonce = _unb64(envelope["nonce"])
    if len(nonce) != 12:
        raise ValueError("中继应答 nonce 无效")
    raw = AESGCM(key).decrypt(nonce, _unb64(
        envelope["ciphertext"], max_chars=1_400_000), aad)
    if len(raw) > MAX_PLAINTEXT:
        raise ValueError("中继应答过大")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("中继应答必须是对象")
    return value
