"""a2n-dispatch（L3 业务）：发现自由，派单受控。

发现是信息（谁都能搜），派单是钱（必须校验状态、在线、黑名单、
card_hash 一致性、KYA 等级、并发上限）。
"""
from .service import ASSIGNABLE_STATUS, Discovery, discovery

__all__ = ["Discovery", "discovery", "ASSIGNABLE_STATUS"]
