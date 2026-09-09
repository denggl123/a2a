"""见证人集合：谁有权为一个 epoch 背书，以及多少权重才算数。

为什么是 2/3 而不是过半：
  过半在"网络分裂成两半"时会出现两个都合法的根（双花）。
  2/3 保证任意两个被通过的根，其见证集合必有交集，交集里的见证人
  不可能对两个不同的根都签过名（除非它主动作恶并留下签名证据可追责）。

签名算法不在这里实现 —— 由装配层注入 `verifier(pub, msg, sig) -> bool`。
理由：ed25519 / 国密 / HSM 都只是实现细节，共识只关心"能不能验证"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

Verifier = Callable[[str, str, str], bool]   # (pub, msg, sig) -> bool


@dataclass(frozen=True)
class Witness:
    did: str
    pub: str
    weight: int = 1

    def to_dict(self) -> dict:
        return {"did": self.did, "pub": self.pub, "weight": self.weight}


@dataclass
class WitnessSet:
    witnesses: dict[str, Witness] = field(default_factory=dict)

    def add(self, w: Witness) -> None:
        """TOFU 只增不改：已存在的见证人不可被悄悄替换公钥。

        这与 p2p 层的 `learn_pub` 是同一条纪律 —— 覆盖公钥等于允许
        "先混进见证名单，再换钥匙"，那样门槛签名就失去意义。
        """
        if w.did in self.witnesses:
            return
        self.witnesses[w.did] = w

    def remove(self, did: str) -> None:
        self.witnesses.pop(did, None)

    def total_weight(self) -> int:
        return sum(w.weight for w in self.witnesses.values())

    def threshold(self) -> int:
        """达到即可通过的最小权重：严格超过 2/3。"""
        total = self.total_weight()
        if total == 0:
            return 1                       # 无人见证时不放行，见下方 signed_weight 判断
        return total * 2 // 3 + 1

    def signed_weight(self, sigs: Iterable[dict]) -> int:
        """按**当前**见证名单统计有效签名权重。

        不在名单里的人签名不算 —— 否则谁都能自封见证人把根签过去。
        """
        out = 0
        for s in sigs:
            w = self.witnesses.get(s.get("signer"))
            if w:
                out += w.weight
        return out

    def reached(self, sigs: Iterable[dict]) -> bool:
        if not self.witnesses:
            return False                   # 没有见证人 = 没有共识，宁可停摆
        return self.signed_weight(sigs) >= self.threshold()

    def verify(self, sigs: Iterable[dict], msg: str, verifier: Verifier) -> tuple[bool, str]:
        """逐个验签并统计权重。任一签名无效即整体不通过（不做"部分采信"）。"""
        if not self.witnesses:
            return False, "见证名单为空：没有共识"
        ok_weight = 0
        for s in sigs:
            w = self.witnesses.get(s.get("signer"))
            if not w:
                continue                   # 非见证人的签名忽略不计
            if not verifier(w.pub, msg, s.get("sig", "")):
                return False, f"见证人 {w.did} 的签名无效"
            ok_weight += w.weight
        if ok_weight < self.threshold():
            return False, f"见证权重不足：{ok_weight}/{self.total_weight()}，需 {self.threshold()}"
        return True, f"见证通过 {ok_weight}/{self.total_weight()}"
