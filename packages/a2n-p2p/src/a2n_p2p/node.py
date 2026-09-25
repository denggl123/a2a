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
import ipaddress
import json
import socket
import threading
import time
from typing import Any, Callable

from a2n_kernel.hashing import new_id

from .envelope import (CARD, DEFAULT_TTL, DISCOVERY_TYPES, HELLO, MSG_TYPES,
                       NETWORK_TYPES, OFFER, PING, PONG, PUNCH, PUNCH_HINT,
                       PUNCH_REQ, PUNCH_TYPES, QUERY, Envelope, parse_pub)
from .identity import DID_PREFIX, Identity, fingerprint_of
from .peers import PEER_TTL, PeerTable

# UDP 不提供可靠分片；控制面报文必须留在常见互联网 MTU 内。完整卡、任务和
# 结果均应走可靠传输层，不可塞进 gossip。
MAX_GOSSIP_BYTES = 1_400
MAX_WIRE_SKILLS = 32
QUERY_RATE_PER_SECOND = 20
QUERY_ROUTE_CACHE = 4096
DISCOVERY_MAX_AGE = 180.0
DISCOVERY_MAX_FUTURE_SKEW = 30.0

# ---- UDP 打洞的有界参数 ------------------------------------------------
# 协调打洞不是义务：任何一方都可以拒绝。所有计数都以"单机内存有限、
# 不给别人当放大器"为上界；对称型 NAT 打不通属于物理事实，不做伪装。
PUNCH_SESSION_TTL = 6.0        # 一个打洞会话的有效期（秒）
MAX_PUNCH_SESSIONS = 64        # 本机同时挂着的打洞会话上界
MAX_PUNCH_REQ_PER_MIN = 10     # 我愿意为同一个请求者协调的频次
MAX_PUNCH_HINT_PER_MIN = 30    # 我接受同一个协调者的提示频次
MAX_PUNCH_HELPERS = 3          # 一次打洞最多依次尝试几个协调者
PUNCH_ATTEMPTS = 4             # 收到提示后对射的包数（有小上界，不当放大器）
PUNCH_INTERVAL = 0.25          # 对射间隔（秒）
MAX_OBSERVED_ENDPOINTS = 512   # 实测端点表上界（DID → 最近一次 UDP 源地址）

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
        self._ping_lock = threading.Lock()
        self._pending_pings: dict[str, dict[str, Any]] = {}
        self._punch_lock = threading.Lock()
        # 种子表可被运行中的控制台增删（"连接节点"），而维护循环/收包线程同时在读
        # —— 锁保证"遍历时不会被 append 撕裂"。
        self._bootstrap_lock = threading.RLock()
        # 实测端点表：PeerTable 的端口来自 payload 自报，跨 NAT 打洞必须用
        # 对方 UDP 报文的实测源地址（NAT 映射后的 ip:port）。
        self._observed: "OrderedDict[str, tuple[tuple[str, int], float]]" = OrderedDict()
        self._punch_sessions: dict[str, dict[str, Any]] = {}
        self._punch_rate: dict[str, deque[float]] = {}
        # 最近一次被邻居观测到的本机公网映射端点（STUN 式学习，仅供展示与诊断）
        self.self_endpoint: tuple[str, int] | None = None
        self._running = False
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self.stats = {"sent": 0, "recv": 0, "dropped_dup": 0, "dropped_badsig": 0,
                      "dropped_unknown_pub": 0, "dropped_bad_identity": 0,
                      "dropped_rate": 0, "dropped_oversize": 0,
                       "dropped_stale": 0, "dropped_unpeered": 0,
                       "punch_ok": 0, "punch_dropped": 0}

        self.on(HELLO, self._on_hello)
        self.on(PING, self._on_ping)
        self.on(PONG, self._on_pong)
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
        # Bootstrap retry, direct-neighbour keepalive and pruning are network
        # maintenance, not LAN broadcast.  ``beacon=False`` must not make a
        # configured seed silently disappear after the peer TTL.
        self._spawn(self._maintenance_loop, "p2p-maintenance")
        if self.beacon:
            self._spawn(self._beacon_loop, "p2p-beacon")
        # 冷启动：先连种子
        for addr in self.bootstrap_targets():
            self._send_hello(addr)
        return self

    def stop(self) -> None:
        self._running = False
        with self._ping_lock:
            pending = list(self._pending_pings.values())
        for item in pending:
            item["event"].set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=1.5)

    # ---------- 冷启动种子（可热加/热删）----------

    def bootstrap_targets(self) -> list[tuple[str, int]]:
        """种子的线程安全快照。维护循环与收包线程都读它。"""
        with self._bootstrap_lock:
            return list(self.bootstrap)

    def add_bootstrap(self, addr: tuple[str, int]) -> bool:
        """把一个种子加进冷启动列表，并**立刻**握一次手。

        控制台的「连接节点」走这里：加进来就够，不需要重启节点 —— 维护循环
        之后会持续重试，所以对方那一刻不在线也不会永久错过。
        返回 True 表示这条种子是新加的（False = 本来就在）。
        """
        host = str(addr[0] or "").strip()
        port = int(addr[1])
        if not host or not 0 < port <= 65_535:
            raise ValueError(f"种子地址不合法：{addr!r}")
        target = (host, port)
        with self._bootstrap_lock:
            if target in self.bootstrap:
                added = False
            else:
                self.bootstrap.append(target)
                added = True
        if self._running:
            self._send_hello(target)
        return added

    def remove_bootstrap(self, addr: tuple[str, int]) -> bool:
        """删掉一条种子。只影响**冷启动重试**，不会把已建立的邻居踢下线 ——
        邻居关系是双向事实，单方面删本地记录不等于对方不认识我。"""
        host = str(addr[0] or "").strip()
        try:
            port = int(addr[1])
        except (TypeError, ValueError):
            return False
        target = (host, port)
        with self._bootstrap_lock:
            if target not in self.bootstrap:
                return False
            self.bootstrap.remove(target)
        return True

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

    def _maintenance_loop(self) -> None:
        while self._running:
            time.sleep(self.beacon_interval)
            targets = set(self.bootstrap_targets())
            targets.update(peer.addr for peer in self.table.alive())
            for addr in targets:
                self._send_hello(addr)
            self.table.prune()
            # 打洞会话按时间回收：不依赖"下一次打洞"来清理，长跑节点不会攒满。
            self._prune_punch_sessions()

    def _on_hello(self, env: Envelope, addr: tuple[str, int]) -> None:
        if env.frm == self.identity.did:
            return                                   # 不认识自己
        host = addr[0]
        raw_port = env.payload.get("port", addr[1])
        if isinstance(raw_port, bool) or not isinstance(raw_port, int):
            return
        port = raw_port
        if not 0 < port <= 65_535:
            return
        is_new = env.frm not in self.table.peers
        pub = env.payload.get("pub")
        # via 只记"第一次是怎么认识的"：punch 打通的邻居不该被后续 HELLO
        # 降级成 mdns，bootstrap 同理 —— 标签是事实，不是当前状态的刷新。
        existing = self.table.get(env.frm)
        via = existing.via if existing is not None else (
            "bootstrap" if (host, port) in set(self.bootstrap_targets()) else "mdns")
        self._observe(env.frm, addr)
        self.table.upsert(env.frm, host, port,
                          parse_pub(pub) if pub else None,
                          via=via, skills=env.payload.get("skills") or [],
                          advert=env.payload.get("advert") or None)
        if is_new:
            self._send_hello((host, port))           # 首次见到才回，避免互喊风暴

    # ---------- 邻居链路探测 ----------
    def ping(self, did: str, timeout: float = 1.0) -> float | None:
        """Measure a signed UDP round trip to one directly known peer.

        The probe carries no Agent payload and never enters task, acceptance or
        settlement code. ``None`` means no verified reply arrived in time.
        """
        if timeout <= 0:
            raise ValueError("探测超时必须大于 0")
        peer = self.table.get(str(did or ""))
        if peer is None or not peer.alive():
            return None
        env = Envelope(frm=self.identity.did, type=PING, payload={}, ttl=1)
        pending = {"did": peer.did, "started": time.monotonic(),
                   "event": threading.Event(), "rtt_ms": None}
        with self._ping_lock:
            self._pending_pings[env.msg_id] = pending
        if not self.send(peer.addr, env):
            with self._ping_lock:
                self._pending_pings.pop(env.msg_id, None)
            return None
        pending["event"].wait(float(timeout))
        with self._ping_lock:
            finished = self._pending_pings.pop(env.msg_id, pending)
        value = finished.get("rtt_ms")
        return round(float(value), 2) if value is not None else None

    def _on_ping(self, env: Envelope, addr: tuple[str, int]) -> None:
        self._touch_direct_peer(env.frm, addr)
        self._observe(env.frm, addr)
        self.send(addr, Envelope(frm=self.identity.did, type=PONG, ttl=1,
                                 payload={"ping_id": env.msg_id}))

    def _on_pong(self, env: Envelope, addr: tuple[str, int]) -> None:
        self._touch_direct_peer(env.frm, addr)
        self._observe(env.frm, addr)
        ping_id = str((env.payload or {}).get("ping_id") or "")
        with self._ping_lock:
            pending = self._pending_pings.get(ping_id)
            if pending is None or pending["did"] != env.frm:
                return
            pending["rtt_ms"] = (time.monotonic() - pending["started"]) * 1000
            pending["event"].set()

    def _touch_direct_peer(self, did: str, addr: tuple[str, int]) -> None:
        peer = self.table.get(did)
        if peer and peer.addr == addr:
            peer.last_seen = time.time()

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

    # ---------- UDP 打洞 ----------
    # 三方角色：发起方 A 想直连 target B，但只知道 B 的 DID；
    # 协调节点 C 是双方共同的活跃邻居，负责交换"我实测到的你们两个的源地址"。
    # A、B 拿到对方的实测端点后同时向对方发包（对射），先打通各自 NAT 的
    # 出站映射；首个通过验签的 PUNCH 到达即把对方写入邻居表（via="punch"）。
    # 锥形 NAT（full/restricted cone）通常可通；对称型 NAT 映射随目标变化，
    # 打不通 —— 那时应走密封中继，而不是假装成功。

    def _observe(self, did: str, addr: tuple[str, int]) -> None:
        """记录一个 DID 最近一次实测的 UDP 源地址。"""
        now = time.time()
        with self._punch_lock:
            self._observed[did] = (addr, now)
            self._observed.move_to_end(did)
            while len(self._observed) > MAX_OBSERVED_ENDPOINTS:
                self._observed.popitem(last=False)

    def observed_endpoint(self, did: str) -> tuple[str, int] | None:
        with self._punch_lock:
            row = self._observed.get(did)
        if row is None or time.time() - row[1] > PEER_TTL:
            return None
        return row[0]

    def _punch_allow(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        window = self._punch_rate.setdefault(key, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        if len(self._punch_rate) > 2048:
            stale = [k for k, v in self._punch_rate.items()
                     if not v or now - v[-1] >= 120.0]
            for k in stale[:512]:
                self._punch_rate.pop(k, None)
        return True

    def _prune_punch_sessions(self, now: float | None = None) -> None:
        """按时间淘汰会话 —— 完成与否都要淘汰，否则长跑节点会被自己的历史撑满。"""
        now = time.time() if now is None else now
        with self._punch_lock:
            expired = [sid for sid, s in self._punch_sessions.items()
                       if now > s["expires"]]
            for sid in expired:
                self._punch_sessions.pop(sid, None)

    def _new_punch_session(self, session: dict[str, Any]) -> bool:
        with self._punch_lock:
            now = time.time()
            # 只按时间淘汰：已完成的会话仍要留一小段过期窗口，让等待方
            # 还能读到结果；但绝不允许它们永久占位（否则 64 个之后打洞全废）。
            self._punch_sessions = {
                sid: s for sid, s in self._punch_sessions.items()
                if now <= s["expires"]}
            if len(self._punch_sessions) >= MAX_PUNCH_SESSIONS:
                return False
            self._punch_sessions[session["sid"]] = session
        return True

    @staticmethod
    def _valid_endpoint(value) -> tuple[str, int] | None:
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not isinstance(value[0], str) or isinstance(value[1], bool)
                or not isinstance(value[1], int)):
            return None
        try:
            ipaddress.ip_address(value[0])
        except ValueError:
            return None
        if not 0 < value[1] <= 65_535:
            return None
        return (value[0], value[1])

    def _handle_punch(self, env: Envelope, addr: tuple[str, int]) -> bool:
        if env.type == PUNCH_REQ:
            return self._on_punch_req(env, addr)
        if env.type == PUNCH_HINT:
            return self._on_punch_hint(env, addr)
        return self._on_punch(env, addr)

    def _on_punch_req(self, env: Envelope, addr: tuple[str, int]) -> bool:
        payload = env.payload or {}
        target = payload.get("target")
        request_id = payload.get("request")
        if (not isinstance(target, str) or not target or len(target) > 200
                or target == self.identity.did
                or not isinstance(request_id, str) or not request_id
                or len(request_id) > 200):
            return False
        # 陌生人也可以来约，但必须带公钥且 DID=公钥指纹（同 HELLO 的 TOFU 纪律）
        pub_b64 = payload.get("pub")
        if not isinstance(pub_b64, str) or not self._verify_with_pub_b64(env, pub_b64):
            return False
        if not self._punch_allow("req:" + env.frm, MAX_PUNCH_REQ_PER_MIN):
            return False
        session = new_id("punch")
        expires = time.time() + PUNCH_SESSION_TTL
        peer_b = self.table.get(target)
        endpoint_b = (self.observed_endpoint(target)
                      if peer_b is not None and peer_b.alive() and peer_b.pub_raw else None)
        pub_b = None
        if endpoint_b is not None and peer_b.pub_raw:
            pub_b = base64.urlsafe_b64encode(peer_b.pub_raw).decode().rstrip("=")
        # 给发起方的答复：成功带 B 的实测端点；失败显式 endpoint=None（不许猜）
        self.send(addr, Envelope(
            frm=self.identity.did, type=PUNCH_HINT, ttl=1, payload={
                "session": session, "request": request_id,
                "peer_did": target, "peer_pub": pub_b,
                "endpoint": list(endpoint_b) if endpoint_b else None,
                "your_endpoint": [addr[0], addr[1]],
                "expires": expires}))
        if endpoint_b and pub_b:
            # 给目标的提示：发起方的实测源地址 + 自报公钥（B 需自行核 DID=指纹）。
            # 发往 B 也优先用实测端点 —— 自报端口在 NAT 后面常常不是能到达的端口。
            target_addr = self.observed_endpoint(target) or peer_b.addr
            self.send(target_addr, Envelope(
                frm=self.identity.did, type=PUNCH_HINT, ttl=1, payload={
                    "session": session, "peer_did": env.frm, "peer_pub": pub_b64,
                    "endpoint": [addr[0], addr[1]], "expires": expires}))
        return True

    def _on_punch_hint(self, env: Envelope, addr: tuple[str, int]) -> bool:
        payload = env.payload or {}
        # 提示只认当前活跃且验过签的直连邻居 —— 不是谁都能让我向某个地址发包
        helper = self.table.get(env.frm)
        if helper is None or not helper.alive() or not helper.pub_raw:
            return False
        # 比对**实测源地址**而不是邻居表里的自报端口：跨 NAT 时对方的映射端口
        # 与自报端口不同，只认同一个会让协调者的提示被静默丢掉。
        expected_addr = self.observed_endpoint(env.frm) or helper.addr
        if addr != expected_addr:
            return False
        if not self._punch_allow("hint:" + env.frm, MAX_PUNCH_HINT_PER_MIN):
            return False
        if not env.verify(lambda _did: helper.pub_raw):
            return False
        sid = payload.get("session")
        peer_did = payload.get("peer_did")
        if (not isinstance(sid, str) or not sid or len(sid) > 200
                or not isinstance(peer_did, str) or not peer_did or len(peer_did) > 200
                or peer_did == self.identity.did):
            return False
        endpoint = self._valid_endpoint(payload.get("endpoint"))
        peer_pub = payload.get("peer_pub")
        if endpoint is not None:
            # 真要对射时必须带对方公钥，且 DID=公钥指纹（防冒名诱导我发包）
            if not isinstance(peer_pub, str) or not peer_pub:
                return False
            try:
                peer_pub_raw = parse_pub(peer_pub)
            except (ValueError, TypeError):
                return False
            if peer_did != DID_PREFIX + "ag_" + fingerprint_of(peer_pub_raw):
                return False
        else:
            peer_pub_raw = None
        if endpoint is None:
            # 显式失败（多半是协调者不认识目标）：只对发起方有意义
            if payload.get("request"):
                failed = {"sid": sid, "request": str(payload["request"])[:200],
                          "failed": True, "event": threading.Event(),
                          "expires": time.time() + PUNCH_SESSION_TTL}
                if self._new_punch_session(failed):
                    failed["event"].set()
            return True
        try:
            expires = float(payload.get("expires"))
        except (TypeError, ValueError, OverflowError):
            return False
        if not (time.time() - 30.0) < expires < (time.time() + PUNCH_SESSION_TTL + 30.0):
            return False
        session = {
            "sid": sid,
            "request": str(payload.get("request") or "")[:200],
            "peer_did": peer_did, "peer_pub_raw": peer_pub_raw,
            "endpoint": endpoint, "expires": min(expires, time.time() + PUNCH_SESSION_TTL),
            "failed": False, "ok": False, "observed": None,
            "event": threading.Event()}
        if payload.get("your_endpoint") is not None:
            own = self._valid_endpoint(payload.get("your_endpoint"))
            if own and not self.self_endpoint:
                self.self_endpoint = own
        if not self._new_punch_session(session):
            return True
        # 对射线程是毫秒级短命线程，不进常驻线程表（_threads 只收生命周期线程）
        threading.Thread(target=self._punch_pacer, args=(session,),
                         daemon=True, name="p2p-punch").start()
        return True

    def _punch_pacer(self, session: dict[str, Any]) -> None:
        env = Envelope(frm=self.identity.did, type=PUNCH, ttl=1,
                       payload={"session": session["sid"]})
        for _ in range(PUNCH_ATTEMPTS):
            if session["event"].is_set() or not self._running:
                return
            self.send(session["endpoint"], env)
            session["event"].wait(PUNCH_INTERVAL)

    def _on_punch(self, env: Envelope, addr: tuple[str, int]) -> bool:
        sid = (env.payload or {}).get("session")
        if not isinstance(sid, str) or not sid or len(sid) > 200:
            return False
        with self._punch_lock:
            session = self._punch_sessions.get(sid)
        if session is None or session.get("ok") or session.get("failed"):
            return False
        if time.time() > session["expires"]:
            return False
        if env.frm != session["peer_did"] or addr != session["endpoint"]:
            return False
        peer_pub_raw = session.get("peer_pub_raw")
        if peer_pub_raw is None or not env.verify(lambda _did: peer_pub_raw):
            return False
        # 打通了：对方进邻居表（实测地址），并立刻 HELLO 完成常规握手
        self.table.upsert(env.frm, addr[0], addr[1], peer_pub_raw, via="punch")
        session["ok"] = True
        session["observed"] = addr
        session["event"].set()
        self.stats["punch_ok"] += 1
        self._send_hello(addr)
        return True

    def punch(self, target_did: str, helper_did: str | None = None,
              timeout: float = 4.0) -> dict[str, Any]:
        """请求活跃邻居协调，与 target 做 UDP 对射打洞。

        返回 {"ok": bool, "endpoint": [ip, port] | None, "detail": str}。
        ok=True 表示收到了来自 target 实测端点的有效 PUNCH —— 双方此后
        可直发 UDP。ok=False 的常见原因：协调者不认识目标（无实测端点）、
        对称型 NAT 映射随目标变化。任务载荷从不走这条通道。

        多个邻居时按顺序最多试 3 个协调者：第一个不认识目标不代表全网都不认识。
        指定 helper_did 时只试它（调用方明确指定就该尊重，而不是偷偷换人）。
        """
        if timeout <= 0:
            raise ValueError("打洞超时必须大于 0")
        target_did = str(target_did or "")
        if not target_did or len(target_did) > 200:
            raise ValueError("目标 DID 不合法")
        if target_did == self.identity.did:
            return {"ok": False, "endpoint": None, "detail": "目标就是本节点"}
        if not self._running:
            return {"ok": False, "endpoint": None, "detail": "网络层未启动"}
        if helper_did:
            named = self.table.get(helper_did)
            helpers = [named] if named is not None and named.alive() else []
        else:
            helpers = [p for p in self.table.alive()
                       if p.pub_raw and p.did != target_did][:MAX_PUNCH_HELPERS]
        if not helpers:
            return {"ok": False, "endpoint": None, "detail": "没有可协调的活跃邻居"}
        budget = max(0.5, float(timeout) / len(helpers))
        deadline = time.monotonic() + float(timeout)
        last = {"ok": False, "endpoint": None, "detail": "协调节点没有回应"}
        for helper in helpers:
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                break
            last = self._punch_via(helper, target_did, min(budget, remaining))
            if last.get("ok"):
                return last
        return last

    def _punch_via(self, helper, target_did: str,
                   timeout: float) -> dict[str, Any]:
        """经一个协调者发起一次打洞；失败原因原样返回，不吞。"""
        request_id = new_id("preq")
        req = Envelope(frm=self.identity.did, type=PUNCH_REQ, ttl=1, payload={
            "target": target_did, "request": request_id, "pub": self.pub_b64()})
        if not self.send(helper.addr, req):
            return {"ok": False, "endpoint": None, "detail": "协调请求发送失败"}
        deadline = time.monotonic() + float(timeout)
        session: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            with self._punch_lock:
                session = next(
                    (s for s in self._punch_sessions.values()
                     if s.get("request") == request_id), None)
            if session is not None:
                break
            time.sleep(0.05)
        if session is None:
            return {"ok": False, "endpoint": None, "detail": "协调节点没有回应"}
        if session.get("failed"):
            return {"ok": False, "endpoint": None,
                    "detail": "协调节点不认识目标或暂无实测端点"}
        remaining = max(0.1, deadline - time.monotonic())
        session["event"].wait(remaining)
        if session.get("ok"):
            return {"ok": True, "endpoint": list(session["observed"] or ()),
                    "detail": "已打通"}
        return {"ok": False, "endpoint": None,
                "detail": "对射未达成（对方可能是对称型 NAT，应改走中继）"}

    def _verify_with_pub_b64(self, env: Envelope, pub_b64: str) -> bool:
        try:
            raw = parse_pub(pub_b64)
        except (ValueError, TypeError):
            return False
        if env.frm != DID_PREFIX + "ag_" + fingerprint_of(raw):
            return False
        return env.verify(lambda _did: raw)

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
        except (OSError, OverflowError, ValueError):
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
        if (env.type in (DISCOVERY_TYPES | {PING, PONG} | PUNCH_TYPES)
                and (age > DISCOVERY_MAX_AGE or age < -DISCOVERY_MAX_FUTURE_SKEW)):
            self.stats["dropped_stale"] += 1
            return
        if self.table.already_seen(env.msg_id):
            self.stats["dropped_dup"] += 1
            return

        # 防 TTL 放大：签名不覆盖 TTL，所以上限必须在接收侧强制。
        if env.ttl > DEFAULT_TTL:
            env.ttl = DEFAULT_TTL

        if env.type in PUNCH_TYPES:
            # 打洞三类报文有自己的验签与限频规则（见 _handle_punch）：
            # PUNCH_REQ 允许陌生人带公钥来约（DID=公钥指纹），
            # PUNCH_HINT 只认当前活跃直连邻居，PUNCH 只认本机挂着的会话。
            if not self._handle_punch(env, addr):
                self.stats["punch_dropped"] += 1
                return
            self.table.mark_seen(env.msg_id)
            self._dispatch(env, addr)
            return

        if env.type == QUERY:
            # Only a directly handshaken neighbour may inject or forward a
            # query.  Reverse-path routing alone cannot prevent UDP source
            # spoofing from turning OFFER replies into a reflection primitive.
            if not any(peer.addr == addr and peer.pub_raw
                       for peer in self.table.alive()):
                self.stats["dropped_unpeered"] += 1
                return
            if not self._allow_query(addr[0]):
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
        if env.type in (MSG_TYPES | NETWORK_TYPES) and not env.verify(self.table.pub_lookup):
            if self.table.pub_lookup(env.frm) is None:
                self.stats["dropped_unknown_pub"] += 1
            else:
                self.stats["dropped_badsig"] += 1
            return

        # Only an authenticated packet may occupy a deduplication slot.  Marking
        # before verification lets an attacker send a forged packet with a
        # victim's msg_id and suppress the later legitimate packet.
        self.table.mark_seen(env.msg_id)

        self._dispatch(env, addr)

        # 转发一跳（HELLO 是链路本地，不转发）
        # ttl > 1 才转发：ttl=1 表示"只给直接接收者"，点对点应答不该多跳一次。
        # 必须克隆后 decay —— 就地改 TTL 会污染已经交给订阅者的对象。
        if env.type not in NETWORK_TYPES and env.ttl > 1:
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
