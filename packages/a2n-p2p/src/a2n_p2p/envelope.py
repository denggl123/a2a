"""gossip 信封：去重 + TTL + 签名，三件套缺一不可。

  没去重 → 广播风暴      没 TTL → 无限扩散      没签名 → 女巫污染

八类业务消息里，只有一部分进账本；HELLO 是网络层的内部发现机制，不进账本。
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Callable

from a2n_kernel.hashing import new_id

# 业务消息：进账本的标注在下表
CARD = "CARD"        # 广播/更新 Agent Card          → 进（card_hash）
QUERY = "QUERY"      # 按需发现：谁会这个 skill      → 不进
OFFER = "OFFER"      # 对 QUERY 的应答（附背书签名）  → 不进
TASK = "TASK"        # 派单（锁定合约价 + card_hash） → 进
RESULT = "RESULT"    # 结果 + 计量报告（签名）        → 进（hash）
RECEIPT = "RECEIPT"  # 凭证                          → 进
EPOCH = "EPOCH"      # 批次 Merkle root 提议          → 进
ANCHOR = "ANCHOR"    # 资金锚签名 (epoch, bal, root)  → 进

# 网络层内部：邻居发现，不进账本
HELLO = "HELLO"

MSG_TYPES = {CARD, QUERY, OFFER, TASK, RESULT, RECEIPT, EPOCH, ANCHOR}
# 发现类消息：不进账本，且允许携带自报公钥（TOFU），因为握手/提问/应答
# 发生在互不相识的节点之间。HELLO 也必须验签；否则攻击者能抢先污染 DID。
DISCOVERY_TYPES = {HELLO, QUERY, OFFER}
LEDGER_TYPES = {CARD, TASK, RESULT, RECEIPT, EPOCH, ANCHOR}

DEFAULT_TTL = 5


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


class Envelope:
    """一条可传播的消息。签名覆盖除 sig 外的所有字段。"""

    __slots__ = ("msg_id", "frm", "ttl", "type", "ts", "payload", "pub", "sig")

    def __init__(self, frm: str, type: str, payload: dict,
                 ttl: int = DEFAULT_TTL, msg_id: str | None = None,
                 ts: float | None = None, pub: str | None = None,
                 sig: str | None = None) -> None:
        self.msg_id = msg_id or new_id("msg")
        self.frm = frm
        self.ttl = ttl
        self.type = type
        self.ts = ts if ts is not None else time.time()
        self.payload = payload
        self.pub = pub   # 仅在 HELLO 中携带，用于公钥交换
        self.sig = sig

    # ---- 签名 / 验签 ----
    def signing_payload(self) -> dict:
        """签名域。**TTL 不参与签名** —— 它是传输属性，每跳都会变，
        而中间节点无权代表发起者重新签名。若把 TTL 放进签名域，
        任何一次转发都会让签名失效，多跳网络直接瘫痪。

        TTL 的防篡改由"只减不增"+"接收方 clamp 上限"共同保证，不需要签名。
        """
        return {
            "msg_id": self.msg_id, "frm": self.frm,
            "type": self.type, "ts": round(self.ts, 6), "payload": self.payload,
        }

    def sign(self, identity) -> "Envelope":
        self.sig = identity.sign(self.signing_payload())
        return self

    def verify(self, pub_lookup: Callable[[str], bytes | None]) -> bool:
        """验签：需要调用方提供 did → 公钥 的查询。查不到公钥 = 无法验证 = 不可信。"""
        if not self.sig:
            return False
        raw = pub_lookup(self.frm)
        if raw is None:
            return False
        payload = self.signing_payload()
        return _verify_raw(raw, payload, self.sig)

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id, "frm": self.frm, "ttl": self.ttl,
            "type": self.type, "ts": self.ts, "payload": self.payload,
            "pub": self.pub, "sig": self.sig,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Envelope | None":
        try:
            d = json.loads(data.decode())
            if not isinstance(d, dict):
                return None
            frm, kind = d.get("frm"), d.get("type")
            payload, msg_id = d.get("payload") or {}, d.get("msg_id")
            ttl, ts = d.get("ttl", DEFAULT_TTL), d.get("ts")
            pub, sig = d.get("pub"), d.get("sig")
            if (not isinstance(frm, str) or not frm or len(frm) > 200
                    or kind not in (MSG_TYPES | {HELLO})
                    or not isinstance(payload, dict)
                    or not isinstance(msg_id, str) or not msg_id or len(msg_id) > 200
                    or isinstance(ttl, bool) or not isinstance(ttl, int)
                    or isinstance(ts, bool) or not isinstance(ts, (int, float))
                    or not math.isfinite(float(ts))
                    or (pub is not None and (not isinstance(pub, str) or len(pub) > 200))
                    or (sig is not None and (not isinstance(sig, str) or len(sig) > 200))):
                return None
            return cls(
                frm=frm, type=kind, payload=payload,
                ttl=ttl, msg_id=msg_id, ts=ts, pub=pub, sig=sig,
            )
        except (TypeError, ValueError, KeyError, UnicodeDecodeError):
            return None   # 畸形报文直接丢弃，不抛异常——网络层必须能扛脏输入

    def decay(self) -> "Envelope":
        """转发一跳：TTL -1。归零的消息不再被转发。"""
        self.ttl -= 1
        return self

    def clone(self) -> "Envelope":
        """独立副本。

        **转发必须克隆**：TTL 参与签名，若就地 decay 会污染已经分发给订阅者的
        那个对象——订阅者随后验签会失败，且看到的是被改过的 TTL。
        """
        return Envelope(frm=self.frm, type=self.type, payload=self.payload,
                        ttl=self.ttl, msg_id=self.msg_id, ts=self.ts,
                        pub=self.pub, sig=self.sig)

    def forwardable(self) -> bool:
        return self.ttl > 0


def verify_pub(pub_raw: bytes, payload: dict, sig: str) -> bool:
    """用**裸公钥**验一段 dict 的签名（不需要有 Identity 实例）。

    公开出来是因为校验方常常"只有公钥"：验一张自证的 Agent Card、
    验对方还回来的收据，都是这种场景。算法只有这一份，
    验签口径必须与 Envelope.sign / Identity.sign 严格一致（同 canonical）。
    """
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    msg = _canonical(payload)
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), msg)
        return True
    except (InvalidSignature, ValueError):
        return False


# 兼容旧调用名（本包内部与测试历史上用的是下划线版）
_verify_raw = verify_pub


def hello_payload(identity, port: int, skills: list[str] | None = None,
                  advert: dict | None = None) -> dict:
    """邻居发现报文：携带公钥，让对方无需中心目录即可验证我后续的所有消息。

    advert 是我的**业务入口通告**（如 {"http": "http://ip:port", "card_hash": "…"}）：
    gossip 只负责发现，真实调用（大负载）要走直连 HTTP，所以邻居必须知道
    "该往哪打"。发现与调用因此可以彻底分开——前者多跳、尽力而为，后者单跳、可靠。
    """
    import base64

    return {
        "did": identity.did,
        "port": port,
        "skills": skills or [],
        "advert": advert or {},
        "pub": base64.urlsafe_b64encode(identity.pub_raw).decode().rstrip("="),
    }


def parse_pub(b64: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
