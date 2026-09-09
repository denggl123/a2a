"""资金锚：把"网内积分"钉回"托管里的真钱"。

这是整条链上唯一无法去中心化的一环 —— 因为"钱真的在托管账户里"是一个
物理世界的事实，网络投票投不出来。我们能做的不是去掉它，而是：

  1. **让它只签名，不裁决** —— 锚只回答"托管余额是多少"，
     不参与"这单该不该结算"。后者由服务层事实 + 管理层裁定决定。
  2. **让它成为三把钥匙之一** —— 只有锚、没有 ≥2/3 见证，根依然不成立。
     单方（哪怕是持牌方）凑不齐增发所需的全部钥匙。

生产落地：锚由持牌清算方的 HSM 签名，公链场景下则是链上稳定币合约的
储备证明。本包不关心是哪种，只关心"有一个可验证的余额声明"。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

from a2n_kernel.hashing import canonical_json, now_iso, sha256

Signer = Callable[[str], str]          # msg -> sig
Verifier = Callable[[str, str, str], bool]   # (pub, msg, sig) -> bool


@dataclass(frozen=True)
class EscrowAttestation:
    """托管余额声明。字段极少，是因为能少就少 —— 每多一个字段就多一处可争议。"""

    epoch_id: str
    balance_fen: int
    as_of: str
    custodian_ref: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def msg(self) -> str:
        """签名域。canonical JSON 保证不同实现算出同一条消息。"""
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return sha256(self.msg())


class Anchor:
    """资金锚签名器。signer 为 None 时表示本节点不持有锚钥匙（只读）。"""

    def __init__(self, did: str = "did:a2n:custodian", pub: str = "",
                 signer: Signer | None = None,
                 verifier: Verifier | None = None) -> None:
        self.did = did
        self.pub = pub
        self._signer = signer
        self._verifier = verifier

    @property
    def can_sign(self) -> bool:
        return self._signer is not None

    def attest(self, epoch_id: str, balance_fen: int, custodian_ref: str = "") -> EscrowAttestation:
        return EscrowAttestation(epoch_id=epoch_id, balance_fen=balance_fen,
                                 as_of=now_iso(), custodian_ref=custodian_ref)

    def sign(self, att: EscrowAttestation) -> dict:
        if not self._signer:
            raise PermissionError("本节点不持有资金锚钥匙：只有持牌方能签")
        return {"signer": self.did, "kind": "anchor", "sig": self._signer(att.msg()),
                "attestation": att.to_dict()}

    def verify(self, sig: dict, expect_epoch: str) -> tuple[bool, str]:
        if not self._verifier:
            return False, "未注入验签器，无法验证资金锚"
        att = sig.get("attestation") or {}
        if att.get("epoch_id") != expect_epoch:
            return False, f"锚的 epoch 不匹配：{att.get('epoch_id')} != {expect_epoch}"
        if not self._verifier(self.pub, EscrowAttestation(**att).msg(), sig.get("sig", "")):
            return False, "资金锚签名无效"
        return True, f"资金锚有效，托管余额 {att.get('balance_fen')} 分"


@dataclass(frozen=True)
class AnchorAttestation:
    """锚的最终形态：声明 + 签名，随 epoch 一起存档。"""

    epoch_id: str
    balance_fen: int
    signer: str
    sig: str
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


def three_keys_ok(entries_ok: bool, witness_ok: bool, anchor_ok: bool) -> bool:
    """三把钥匙齐全才算数。任何两把都不行 —— 这是对抗单方作恶的最小结构。"""
    return bool(entries_ok and witness_ok and anchor_ok)
