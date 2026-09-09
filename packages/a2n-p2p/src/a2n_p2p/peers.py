"""对等表：我知道谁、他们的公钥是什么、最近见过哪些消息。

三件事合一：地址簿（发给谁）、公钥簿（验谁的签）、去重表（别重复传播）。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable

# 每个节点缓存最近这么多条 msg_id 用于去重。
# 数量 ∝ 单节点内存，与全网规模无关 —— 这是 gossip 能扩展的前提之一。
SEEN_CACHE = 4096

# 超过这么久没心跳/没消息的对等，视为离线（不再向它发送）
PEER_TTL = 120.0


@dataclass
class Peer:
    did: str
    host: str
    port: int
    pub_raw: bytes | None = None
    last_seen: float = field(default_factory=time.time)
    via: str = "unknown"        # mdns | bootstrap | gossip
    skills: list[str] = field(default_factory=list)

    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    def alive(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_seen < PEER_TTL


class PeerTable:
    def __init__(self) -> None:
        self.peers: dict[str, Peer] = {}
        self._known: dict[str, bytes] = {}   # TOFU 学到的公钥（未必是邻居）
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ---- 对等 ----
    def upsert(self, did: str, host: str, port: int, pub_raw: bytes | None = None,
               via: str = "unknown", skills: list[str] | None = None) -> Peer:
        p = self.peers.get(did)
        if p is None:
            p = Peer(did=did, host=host, port=port, pub_raw=pub_raw, via=via,
                     skills=skills or [])
            self.peers[did] = p
        else:
            # 已存在：只更新可达性与能力，不覆盖已学到的公钥
            # （公钥一旦学到就固定，防止被后来者冒名顶替）
            p.host, p.port, p.last_seen = host, port, time.time()
            if p.pub_raw is None and pub_raw:
                p.pub_raw = pub_raw
            if skills:
                p.skills = skills
        return p

    def get(self, did: str) -> Peer | None:
        return self.peers.get(did)

    def remove(self, did: str) -> None:
        self.peers.pop(did, None)

    def alive(self) -> list[Peer]:
        now = time.time()
        return [p for p in self.peers.values() if p.alive(now)]

    def prune(self, ttl: float = PEER_TTL) -> int:
        now = time.time()
        dead = [d for d, p in self.peers.items() if not p.alive(now)]
        for d in dead:
            del self.peers[d]
        return len(dead)

    def by_skill(self, skill: str) -> list[Peer]:
        return [p for p in self.alive() if skill in p.skills]

    # ---- 公钥查询（给 envelope 验签用）----
    def pub_lookup(self, did: str) -> bytes | None:
        p = self.peers.get(did)
        if p is not None and p.pub_raw:
            return p.pub_raw
        return self._known.get(did)

    def learn_pub(self, did: str, pub_raw: bytes) -> None:
        """记录一个"自称"的公钥（TOFU）。

        分层边界：网络层只能保证"这条消息确实出自持有该私钥的人"，
        **不能保证这个人可信**——那是管理层信誉（D5）的职责。
        所以自学报的公钥单独存放，且永不覆盖已有记录（防冒名顶替）。
        """
        if did in self._known:
            return
        self._known[did] = pub_raw
        p = self.peers.get(did)
        if p is not None and p.pub_raw is None:
            p.pub_raw = pub_raw

    def is_endorsed(self, did: str) -> bool:
        """是否由我**直接认识**的节点担保 —— 用于上层判断候选可信度。"""
        p = self.peers.get(did)
        return bool(p is not None and p.pub_raw)

    # ---- 去重 ----
    def already_seen(self, msg_id: str) -> bool:
        return msg_id in self._seen

    def mark_seen(self, msg_id: str) -> None:
        self._seen[msg_id] = None
        self._seen.move_to_end(msg_id)
        while len(self._seen) > SEEN_CACHE:
            self._seen.popitem(last=False)

    def __len__(self) -> int:
        return len(self.peers)

    def __iter__(self) -> Iterable[Peer]:
        return iter(self.peers.values())
