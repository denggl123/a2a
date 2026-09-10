"""P2P 节点：网络层（L0）的可运行形态。

它回答四个问题中的三个：我是谁（身份）、我怎么找到别人（发现）、消息怎么到（传播）。
第四个问题（共识）不在本包，由 a2n-consensus 负责。

设计取舍：
  - 传输用 UDP —— gossip 天然容忍丢包，且不需要连接状态。
  - 大负载不走 gossip。超过 60KB 直接拒绝：那是传输层隧道（tunnel/relay）的活，
    让 gossip 扛大包是典型的层次错位。
  - 本包只做"网络"，不知道什么是 agent、任务、积分。
  - 当前定位：**发现基板**。真实的调用/结算执行链走 a2n-transport（隧道/中继），
    gossip 只负责"把网络里的节点互相找到"；别误以为本包已接入执行链。
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from typing import Any, Callable

from a2n_kernel.hashing import new_id

from .envelope import (CARD, DEFAULT_TTL, DISCOVERY_TYPES, HELLO, MSG_TYPES,
                       OFFER, QUERY, Envelope, parse_pub)
from .identity import Identity
from .peers import PeerTable

# UDP 单包上限（留安全余量）
MAX_GOSSIP_BYTES = 60_000

BEACON_BASE = 9701
BEACON_FANOUT = 8        # 向 base..base+fanout-1 发，使同机多节点也能互相发现
BROADCAST_ADDR = "255.255.255.255"


class P2PNode:
    def __init__(self, identity: Identity | None = None, port: int = BEACON_BASE,
                 bootstrap: list[tuple[str, int]] | None = None,
                 beacon: bool = True, beacon_interval: float = 2.0,
                 host: str = "0.0.0.0", advertise_host: str = "127.0.0.1") -> None:
        self.identity = identity or Identity.generate()
        self.port = port
        self.host = host
        # 对外通告的地址。绑定 0.0.0.0 与"别人该怎么找到我"是两回事，
        # 生产环境由部署者显式指定（内网穿透/公网 IP 都填在这里）。
        self.advertise_host = advertise_host
        self.bootstrap = bootstrap or []
        self.beacon = beacon
        self.beacon_interval = beacon_interval
        self.skills: list[str] = []

        self.table = PeerTable()
        self._handlers: dict[str, list[Callable[[Envelope, tuple[str, int]], None]]] = {}
        self._offers: dict[str, list[dict]] = {}
        self._running = False
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self.stats = {"sent": 0, "recv": 0, "dropped_dup": 0, "dropped_badsig": 0,
                      "dropped_unknown_pub": 0}

        self.on(HELLO, self._on_hello)
        self.on(QUERY, self._on_query)
        self.on(OFFER, self._on_offer)

    # ---------- 生命周期 ----------
    def start(self) -> "P2PNode":
        if self._running:
            return self
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind((self.host, self.port))
        s.settimeout(0.5)
        self._sock = s
        self._running = True

        self._spawn(self._listen_loop, "p2p-listen")
        if self.beacon:
            self._spawn(self._beacon_loop, "p2p-beacon")
        # 冷启动：先连种子
        for addr in self.bootstrap:
            self._send_hello(addr)
        return self

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=1.5)

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    # ---------- 订阅 ----------
    def on(self, msg_type: str,
           handler: Callable[[Envelope, tuple[str, int]], None]) -> None:
        self._handlers.setdefault(msg_type, []).append(handler)

    def _dispatch(self, env: Envelope, addr: tuple[str, int]) -> None:
        for h in self._handlers.get(env.type, []):
            try:
                h(env, addr)
            except Exception:  # noqa: BLE001 - 单个订阅者出错不得拖垮网络线程
                pass

    # ---------- 发现 ----------
    def _beacon_targets(self) -> list[tuple[str, int]]:
        """广播 + 端口范围，覆盖同机多节点与真实局域网两种情形。

        生产环境应换成真正的 mDNS（224.0.0.251:5353）或 DHT；这里保持零配置可跑。
        """
        targets = [(BROADCAST_ADDR, p) for p in range(BEACON_BASE, BEACON_BASE + BEACON_FANOUT)]
        targets += list(self.bootstrap)
        return targets

    def _send_hello(self, addr: tuple[str, int]) -> None:
        from .envelope import hello_payload

        env = Envelope(frm=self.identity.did, type=HELLO,
                       payload=hello_payload(self.identity, self.port, self.skills), ttl=1)
        self.send(addr, env)

    def _beacon_loop(self) -> None:
        self._send_hello((BROADCAST_ADDR, BEACON_BASE))
        while self._running:
            time.sleep(self.beacon_interval)
            for addr in self._beacon_targets():
                self._send_hello(addr)
            self.table.prune()

    def _on_hello(self, env: Envelope, addr: tuple[str, int]) -> None:
        if env.frm == self.identity.did:
            return                                   # 不认识自己
        host = addr[0]
        port = int(env.payload.get("port") or addr[1])
        is_new = env.frm not in self.table.peers
        pub = env.payload.get("pub")
        self.table.upsert(env.frm, host, port,
                          parse_pub(pub) if pub else None,
                          via="mdns", skills=env.payload.get("skills") or [])
        if is_new:
            self._send_hello((host, port))           # 首次见到才回，避免互喊风暴

    # ---------- 按需发现 ----------
    def _on_query(self, env: Envelope, addr: tuple[str, int]) -> None:
        skill = (env.payload or {}).get("skill")
        if not skill or skill not in self.skills:
            return
        reply_to = env.payload.get("reply_to")
        if not reply_to:
            return
        env2 = Envelope(frm=self.identity.did, type=OFFER, ttl=1, payload={
            "query_id": env.msg_id, "skill": skill,
            "did": self.identity.did, "skills": self.skills,
            # 带上自报公钥：多跳场景下发起者并不认识我，否则它的验签必然失败。
            "pub": self.pub_b64(),
        })
        # **回信地址优先于 DID**：多跳查询时响应者未必认识发起者，
        # 靠 DID 查地址会失败。QUEST 自带地址，应答才能跨越不认识的中间节点回来。
        ra = env.payload.get("reply_addr")
        if ra:
            self.send((ra[0], int(ra[1])), env2)
        else:
            self.send_to_peer(reply_to, env2)

    def _on_offer(self, env: Envelope, addr: tuple[str, int]) -> None:
        qid = (env.payload or {}).get("query_id")
        if qid:
            self._offers.setdefault(qid, []).append(env.payload)

    def query(self, skill: str, timeout: float = 2.0) -> list[dict]:
        """按需发现：向邻居问"谁会这个"，收集应答。

        注意：**响应者只来自你的邻居**。这是刻意的——没有全局目录，
        你能发现的网络大小取决于你连了多少人，而不是平台愿意给你看多少。
        """
        # 用**报文自己的 msg_id** 作为会话号：应答方回填的就是它，二者必须对上。
        env = Envelope(frm=self.identity.did, type=QUERY, ttl=2, payload={
            "skill": skill,
            "reply_to": self.identity.did,
            "reply_addr": [self.advertise_host, self.port],
            # 自报公钥：多跳后收到查询的节点未必认识我，不带就等于让它无法验签。
            "pub": self.pub_b64(),
        })
        qid = env.msg_id
        self._offers[qid] = []
        self._broadcast(env)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.05)
        return self._offers.pop(qid, [])

    def announce(self, skills: list[str]) -> None:
        """广播我的能力（A2N 里携带 card_hash，由上层塞进 payload）。"""
        self.skills = list(skills)
        self.gossip(CARD, {"did": self.identity.did, "skills": skills,
                           "port": self.port})

    # ---------- 发送 ----------
    def send(self, addr: tuple[str, int], env: Envelope) -> bool:
        if self._sock is None:
            return False
        if env.sig is None:
            env.sign(self.identity)
        data = env.to_bytes()
        if len(data) > MAX_GOSSIP_BYTES:
            return False                             # 大包拒绝：层次边界
        try:
            self._sock.sendto(data, addr)
            self.stats["sent"] += 1
            return True
        except OSError:
            return False

    def send_to_peer(self, did: str, env: Envelope) -> bool:
        p = self.table.get(did)
        return self.send(p.addr, env) if p else False

    def gossip(self, msg_type: str, payload: dict, ttl: int = DEFAULT_TTL) -> int:
        """向所有活着的对等发送，并标记自己已见（防止被回传时重复处理）。"""
        return self._broadcast(Envelope(frm=self.identity.did, type=msg_type,
                                        payload=payload, ttl=ttl))

    def _broadcast(self, env: Envelope) -> int:
        if env.sig is None:
            env.sign(self.identity)
        self.table.mark_seen(env.msg_id)
        n = 0
        for p in self.table.alive():
            if self.send(p.addr, env):
                n += 1
        return n

    # ---------- 接收 ----------
    def _listen_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                data, addr = self._sock.recvfrom(65535)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                if not self._running:
                    break
                continue
            self.stats["recv"] += 1
            self._handle(data, addr)

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        env = Envelope.from_bytes(data)
        if env is None:
            return
        if env.frm == self.identity.did:
            return
        if self.table.already_seen(env.msg_id):
            self.stats["dropped_dup"] += 1
            return
        self.table.mark_seen(env.msg_id)

        # 防 TTL 放大：签名不覆盖 TTL，所以上限必须在接收侧强制。
        if env.ttl > DEFAULT_TTL:
            env.ttl = DEFAULT_TTL

        # 发现类消息允许 TOFU：先尝试从报文里学自报公钥，再验签。
        # 学到公钥 ≠ 信任它 —— 信任由上层信誉（D5）裁定，网络层只保证"消息确实出自持私钥者"。
        if env.type in DISCOVERY_TYPES and self.table.pub_lookup(env.frm) is None:
            p = env.payload.get("pub")
            if p:
                try:
                    self.table.learn_pub(env.frm, parse_pub(p))
                except (ValueError, TypeError):
                    pass

        # 验签：查不到公钥 = 无法验证 = 不采信。网络层不传递无法验证的东西。
        if env.type in MSG_TYPES and not env.verify(self.table.pub_lookup):
            if self.table.pub_lookup(env.frm) is None:
                self.stats["dropped_unknown_pub"] += 1
            else:
                self.stats["dropped_badsig"] += 1
            return

        self._dispatch(env, addr)

        # 转发一跳（HELLO 是链路本地，不转发）
        # ttl > 1 才转发：ttl=1 表示"只给直接接收者"，点对点应答不该多跳一次。
        # 必须克隆后 decay —— 就地改 TTL 会污染已经交给订阅者的对象。
        if env.type != HELLO and env.ttl > 1:
            fwd = env.clone().decay()
            for p in self.table.alive():
                if p.did != env.frm:
                    self.send(p.addr, fwd)

    # ---------- 视图 ----------
    def pub_b64(self) -> str:
        return base64.urlsafe_b64encode(self.identity.pub_raw).decode().rstrip("=")
