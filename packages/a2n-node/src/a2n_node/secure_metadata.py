"""Persistent inbox key and ephemeral authenticated encryption for private messages."""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DOMAIN = b"a2n-private-resolution/1"


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value, size=None, *, maximum=24000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("INVALID_PRIVATE_MESSAGE_ENCODING")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("INVALID_PRIVATE_MESSAGE_ENCODING") from exc
    if size is not None and len(raw) != size:
        raise ValueError("INVALID_PRIVATE_MESSAGE_KEY")
    return raw


class PrivateInbox:
    def __init__(self, store, node_did, *, domain=DOMAIN, name="resolution", max_size=8192):
        self.node_did = node_did
        self.domain, self.max_size = domain, max_size
        with store.tx():
            raw = store.get("private_inbox_keys", name)
            if raw is None:
                raw = X25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
                store.put("private_inbox_keys", name, raw)
            self.key = X25519PrivateKey.from_private_bytes(bytes.fromhex(raw))
        self.public_key = encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    @staticmethod
    def seal(record, recipient_key, *, domain=DOMAIN, max_size=8192):
        ephemeral = X25519PrivateKey.generate()
        public = decode(recipient_key, 32)
        aad = domain + b"\0" + record["author_did"].encode() + b"\0" + record["target_did"].encode()
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=aad).derive(
            ephemeral.exchange(X25519PublicKey.from_public_bytes(public)))
        nonce = os.urandom(12)
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > max_size:
            raise ValueError("PRIVATE_MESSAGE_TOO_LARGE")
        return {"v": domain.decode(), "ephemeral_key": encode(ephemeral.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)), "nonce": encode(nonce),
            "ciphertext": encode(AESGCM(key).encrypt(nonce, raw, aad))}

    def open(self, envelope):
        try:
            sealed = envelope["body"]["sealed_message"]
            if set(sealed) != {"v", "ephemeral_key", "nonce", "ciphertext"} or sealed["v"] != self.domain.decode():
                raise ValueError("INVALID_PRIVATE_MESSAGE")
            if envelope["target_did"] != self.node_did:
                raise ValueError("PRIVATE_RECIPIENT_MISMATCH")
            aad = self.domain + b"\0" + envelope["author_did"].encode() + b"\0" + self.node_did.encode()
            shared = self.key.exchange(X25519PublicKey.from_public_bytes(decode(sealed["ephemeral_key"], 32)))
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=aad).derive(shared)
            raw = AESGCM(key).decrypt(decode(sealed["nonce"], 12), decode(sealed["ciphertext"], maximum=self.max_size * 2), aad)
            if len(raw) > self.max_size:
                raise ValueError("PRIVATE_MESSAGE_TOO_LARGE")
            return json.loads(raw)
        except Exception as exc:
            raise ValueError("PRIVATE_MESSAGE_AUTHENTICATION_FAILED") from exc
