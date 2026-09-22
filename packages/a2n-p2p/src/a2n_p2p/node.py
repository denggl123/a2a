"""P2P 节点：网络层（L0）的可运行形态。

它回答四个问题中的三个：我是谁（身份）、我怎么找到别人（发现）、消息怎么到（传播）。
第四个问题（共识）不在本包，由 a2n-consensus 负责。

设计取舍：
  - 传输用 UDP —— gossip 天然容忍丢包，且不需要连接状态。
  - 大负载不走 gossip。签名信封限制在 MTU 安全范围：完整卡、任务与结果属于
    HTTP/QUIC/隧道层，让 gossip 扛大包是典型的层次错位。
  - 本包只做"网络"，不知道什么是 agent、任务、积分。
  - 当前定位：**发现基板**。真实的调用/结算执行链走 a2n-transport（隧道/中继），
    gossip 只负责"把网络里的节点互相找到"；别误以为本包已接入执行链。
"""
from __future__ import annotations

import base64
from collections import OrderedDict, deque
import json
import socket
import threading
import time
from typing import Any, Callable

from a2n_kernel.hashing import new_id

from .envelope import (CARD, DEFAULT_TTL, DISCOVERY_TYPES, HELLO, MSG_TYPES,
                       OFFER, QUERY, Envelope, parse_pub)
from .identity import DID_PREFIX, Identity, fingerprint_of
from .peers import PeerTable

# UDP 不提供可靠分片；控制面报文必须留在常见互联网 MTU 内。完整卡、任务和
# 结果均应走可靠传输层，不可塞进 gossip。
MAX_GOSSIP_BYTES = 1_400
MAX_WIRE_SKILLS = 32
QUERY_RATE_PER_SECOND = 20
QUERY_ROUTE_CACHE = 4096
DISCOVERY_MAX_AGE = 180.0
DISCOVERY_MAX_FUTURE_SKEW = 30.0

BEACON_BASE = 9701
BEACON_FANOUT = 8        # 向 base..base+fanout-1 发，使同机多节点也能互相发现
BROADCAST_ADDR = "255.255.255.255"


class P2PNode:
    def __init__(self, identity: Identity | None = None, port: int = BEACON_BASE,
                 bootstrap: list[tuple[str, int]] | None = None,
                 beacon: bool = True, beacon_interval: float = 2.0,
                 host: str = "0.0.0.0", advertise_host: str = "127.0.0.1",
                 advert: dict | None = None,
                 offer_provider: Callable[[str], dict] | None = None) -> None:
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
        # 业务入口通告（{"http": ..., "card_hash": ...}）：让邻居知道该往哪直连。
        # 本包不理解这些字段的含义，只负责把它们传出去——L0 只管寻址。
        self.advert: dict = dict(advert or {})
        self.offer_provider = offer_provider

        self.table = PeerTable()
        self._handlers: dict[str, list[Callable[[Envelope, tuple[str, int]], None]]] = {}
        self._offers: dict[str, list[dict]] = {}
        self._query_routes: OrderedDict[str, tuple[tuple[str, int], float]] = OrderedDict()
        self._query_rate: dict[str, deque[float]] = {}
        self._running = False
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self.stats = {"sent": 0, "recv": 0, "dropped_dup": 0, "dropped_badsig": 0,
                      "dropped_unknown_pub": 0, "dropped_bad_identity": 0,
                      "dropped_rate": 0, "dropped_oversize": 0,
                      "dropped_stale": 0}

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
                       payload=hello_payload(self.identity, self.port, self._wire_skills(),
                                             self.advert), ttl=1)
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
                          via="mdns", skills=env.payload.get("skills") or [],
                          advert=env.payload.get("advert") or None)
        if is_new:
            self._send_hello((host, port))           # 首次见到才回，避免互喊风暴

    # ---------- 按需发现 ----------
    def _on_query(self, env: Envelope, addr: tuple[str, int]) -> None:
        skill = (env.payload or {}).get("skill")
        if not skill:
            return
        # Remember the observed previous hop. Never reflect an OFFER to an
        # address supplied inside an untrusted QUERY payload.
        if env.msg_id not in self._offers:
            self._query_routes.setdefault(env.msg_id, (addr, time.time()))
            self._query_routes.move_to_end(env.msg_id)
            while len(self._query_routes) > QUERY_ROUTE_CACHE:
                self._query_routes.popitem(last=False)
        if skill not in self.skills:
            return
        advert = self.offer_provider(skill) if self.offer_provider else self.advert
        env2 = Envelope(frm=self.identity.did, type=OFFER, ttl=1, payload={
            "query_id": env.msg_id, "skill": skill,
            "did": self.identity.did, "skills": [str(skill)[:96]],
            # 带上我的业务入口：多跳场景下提问者不认识我，
            # 光有 DID 它没法调用我（DID 是身份，不是地址）。
            "advert": advert,
            # 带上自报公钥：多跳场景下发起者并不认识我，否则它的验签必然失败。
            "pub": self.pub_b64(),
        })
        self.send(addr, env2)

    def _on_offer(self, env: Envelope, addr: tuple[str, int]) -> None:
        qid = (env.payload or {}).get("query_id")
        if qid in self._offers:
            offer = dict(env.payload)
            # Transport observation is not a claim by the peer and therefore is
            # deliberately kept outside the signed payload.
            offer["_source_host"] = addr[0]
            offer["_source_port"] = addr[1]
            self._offers[qid].append(offer)
        elif qid:
            route = self._query_routes.get(qid)
            if route and route[0] != addr:
                self.send(route[0], env.clone())

    def query(self, skill: str, timeout: float = 2.0) -> list[dict]:
        """按需发现：向邻居问"谁会这个"，收集应答。

        注意：**响应者只来自你的邻居**。这是刻意的——没有全局目录，
        你能发现的网络大小取决于你连了多少人，而不是平台愿意给你看多少。
        """
        # 用**报文自己的 msg_id** 作为会话号：应答方回填的就是它，二者必须对上。
        env = Envelope(frm=self.identity.did, type=QUERY, ttl=2, payload={
            "skill": skill,
            "reply_to": self.identity.did,
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

    def announce(self, skills: list[str], advert: dict | None = None) -> None:
        """广播我的能力（A2N 里携带 card_hash，由上层塞进 advert）。"""
        self.skills = list(skills)
        if advert:
            self.advert = dict(advert)
        self.gossip(CARD, {"did": self.identity.did, "skills": self._wire_skills(),
                           "port": self.port, "advert": self.advert})

    def _wire_skills(self) -> list[str]:
        """A bounded hint; on-demand QUERY remains authoritative."""
        values: list[str] = []
        used = 0
        for raw in self.skills[:MAX_WIRE_SKILLS]:
            value = str(raw)[:96]
            cost = len(value.encode("utf-8")) + 4
            if used + cost > 400:
                break
            values.append(value)
            used += cost
        return values

    # ---------- 发送 ----------
    def send(self, addr: tuple[str, int], env: Envelope) -> bool:
        if self._sock is None:
            return False
        if env.sig is None:
            env.sign(self.identity)
        data = env.to_bytes()
        if len(data) > MAX_GOSSIP_BYTES:
            self.stats["dropped_oversize"] += 1
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
        if len(data) > MAX_GOSSIP_BYTES:
            self.stats["dropped_oversize"] += 1
            return
        env = Envelope.from_bytes(data)
        if env is None:
            return
        if env.frm == self.identity.did:
            return
        age = time.time() - env.ts
        if (env.type in DISCOVERY_TYPES
                and (age > DISCOVERY_MAX_AGE or age < -DISCOVERY_MAX_FUTURE_SKEW)):
            self.stats["dropped_stale"] += 1
            return
        if self.table.already_seen(env.msg_id):
            self.stats["dropped_dup"] += 1
            return
        self.table.mark_seen(env.msg_id)

        # 防 TTL 放大：签名不覆盖 TTL，所以上限必须在接收侧强制。
        if env.ttl > DEFAULT_TTL:
            env.ttl = DEFAULT_TTL

        if env.type == QUERY and not self._allow_query(addr[0]):
            self.stats["dropped_rate"] += 1
            return

        # 发现类消息允许 TOFU，但 DID 必须等于公钥指纹。否则攻击者可以
        # 抢在本人之前把任意 DID 永久绑定到攻击者公钥。
        if env.type in DISCOVERY_TYPES and self.table.pub_lookup(env.frm) is None:
            p = env.payload.get("pub")
            if p:
                try:
                    raw = parse_pub(p)
                    expected = DID_PREFIX + "ag_" + fingerprint_of(raw)
                    if env.frm != expected:
                        self.stats["dropped_bad_identity"] += 1
                        return
                    self.table.learn_pub(env.frm, raw)
                except (ValueError, TypeError):
                    pass

        # 验签：查不到公钥 = 无法验证 = 不采信。网络层不传递无法验证的东西。
        if env.type in (MSG_TYPES | {HELLO}) and not env.verify(self.table.pub_lookup):
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

    def _allow_query(self, host: str) -> bool:
        now = time.monotonic()
        window = self._query_rate.setdefault(host, deque())
        while window and now - window[0] >= 1.0:
            window.popleft()
        if len(window) >= QUERY_RATE_PER_SECOND:
            return False
        window.append(now)
        if len(self._query_rate) > 1024:
            stale = [key for key, values in self._query_rate.items()
                     if not values or now - values[-1] >= 2.0]
            for key in stale[:256]:
                self._query_rate.pop(key, None)
        return True

    # ---------- 视图 ----------
    def pub_b64(self) -> str:
        return base64.urlsafe_b64encode(self.identity.pub_raw).decode().rstrip("=")
