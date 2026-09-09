"""身份：公钥即身份，私钥永不出本机。

**身份不需要注册机构分配。** 公钥指纹自己就是 ID，这让"注册"从"申请账号"
降级为"广播一张签名的卡片"——去掉了中心服务器在身份这一环的存在理由。

    did:a2n:ag_<公钥指纹前 12 字节 hex>

原型期用 ed25519（32 字节公钥 / 64 字节签名）。选它而非 RSA 的理由：
短、快、无随机数复用风险（签名是确定性的）。
"""
from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

DID_PREFIX = "did:a2n:"


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Identity:
    """一个节点的身份。私钥只在本进程内存与本地密钥库文件中存在。"""

    def __init__(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        self._sk = private_key
        self._pk = private_key.public_key()

    # ---- 构造 ----
    @classmethod
    def generate(cls) -> "Identity":
        return cls(ed25519.Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "Identity":
        return cls(ed25519.Ed25519PrivateKey.from_private_bytes(raw))

    # ---- 标识 ----
    @property
    def pub_raw(self) -> bytes:
        return self._pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def fingerprint(self) -> str:
        """公钥指纹，短且稳定。用 sha256 前 12 字节，碰撞概率可忽略。"""
        import hashlib

        return hashlib.sha256(self.pub_raw).hexdigest()[:24]

    @property
    def node_id(self) -> str:
        return "ag_" + self.fingerprint

    @property
    def did(self) -> str:
        return DID_PREFIX + self.node_id

    # ---- 签名 ----
    def sign(self, payload: dict) -> str:
        """签名覆盖 canonical_json，保证跨语言实现可复现。"""
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
        return _b64(self._sk.sign(msg))

    def verify(self, did: str, payload: dict, sig: str) -> bool:
        """验签需要知道对方公钥，所以 did 必须携带 node_id，这里只做能做的部分。

        真正的跨节点验签在 `envelope` 层完成（那里持有已交换过的公钥表）。
        """
        raise NotImplementedError("跨节点验签请用 envelope.verify_envelope")

    def verify_with_pub(self, pub_raw: bytes, payload: dict, sig: str) -> bool:
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(pub_raw).verify(_unb64(sig), msg)
            return True
        except (InvalidSignature, ValueError):
            return False

    # ---- 密钥库 ----
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = self._sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        p.write_text(json.dumps({"node_id": self.node_id, "did": self.did,
                                 "sk": _b64(raw)}), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Identity":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_private_bytes(_unb64(d["sk"]))

    def __repr__(self) -> str:  # 私钥绝不出现在 repr 里
        return f"<Identity {self.did}>"
