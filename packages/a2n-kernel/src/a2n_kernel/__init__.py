"""a2n-kernel（L0 内核）：所有包都能依赖它，它不依赖任何包。

    hashing  hash 链、canonical JSON、ID 与时钟
    merkle   Merkle 根 / SPV 证明（批次共识的横向完整性）
    events   进程内领域事件总线（跨模块通信默认走这里）
    policy   策略版本化 PolicyRef + 默认分账规则

这里不允许出现任何业务概念：不认识"任务"，也不认识"钱"。
"""
from .events import publish, subscribe
from .hashing import GENESIS, canonical_json, chain_hash, new_id, now_iso, sha256
from .merkle import EMPTY_ROOT, merkle_proof, merkle_root, proof_root, verify_proof
from .policy import PolicyRef, split_amount, split_rule

__all__ = ["publish", "subscribe", "GENESIS", "canonical_json", "chain_hash",
           "new_id", "now_iso", "sha256", "PolicyRef", "split_amount", "split_rule",
           "EMPTY_ROOT", "merkle_root", "merkle_proof", "verify_proof", "proof_root"]
