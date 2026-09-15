"""a2n-registry（L2 网络）：节点自证的能力，由本包索引并背书。

定位：节点才是权威，注册表只证明"这张卡确实是他发的、当时就是这么写的"。
所以存的是 card_hash、签名校验结果、索引与背书，不是能力本身。

reachability 回答"这一单能不能送进去"——家宽 PC 也能接单的关键。
"""
from . import rosters, trial
from .reachability import (ALL_MODES, describe, is_public_url, nat_verdict,
                           normalize_connection, probe_inbound, reachable)
from .service import (CARD_INVALID, CARD_REJECTED, CARD_TAMPERED, SELF_SIGNED,
                      UNATTESTED, Registry, card_hash, card_verdict,
                      registry, validate_card, verify_card, verify_selfproof)

__all__ = ["Registry", "registry", "card_hash", "card_verdict", "verify_card",
           "verify_selfproof", "validate_card",
           "SELF_SIGNED", "UNATTESTED", "CARD_INVALID", "CARD_TAMPERED", "CARD_REJECTED",
           "reachable", "describe",
           "nat_verdict", "normalize_connection", "probe_inbound", "is_public_url", "ALL_MODES",
           "rosters", "trial"]
