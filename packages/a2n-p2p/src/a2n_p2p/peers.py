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
    # 业务入口通告：gossip 只用来发现，真实调用（大负载）走直连 HTTP，
    # 所以"我在哪个地址上被调用"必须能被邻居知道。这是 L0 寻址的份内事，
    # 不是业务字段 —— 本包不认识 card/skill 之外的东西。
    advert: dict = field(default_factory=dict)

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
               via: str = "unknown", skills: list[str] | None = None,
               advert: dict | None = None) -> Peer:
        p = self.peers.get(did)
        if p is None:
            p = Peer(did=did, host=host, port=port, pub_raw=pub_raw, via=via,
                     skills=skills or [], advert=dict(advert or {}))
            self.peers[did] = p
        else:
            # 已存在：只更新可达性与能力，不覆盖已学到的公钥
            # （公钥一旦学到就固定，防止被后来者冒名顶替）
            p.host, p.port, p.last_seen = host, port, time.time()
            if p.pub_raw is None and pub_raw:
                p.pub_raw = pub_raw
            if skills:
                p.skills = skills
            if advert:
                # 入口通告可以更新（节点换端口是正常运维），公钥不可以
                p.advert = dict(advert)
            if via != "unknown":
                # 首次建立联系的标签可被显式刷新（如 punch 打通），
                # 但匿名刷新（unknown）不得抹掉已有事实。
                p.via = via
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
