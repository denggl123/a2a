"""P2P discovery adapter for the persistent local-node runtime.

The SDK runtime owns Agent bindings and projections.  ``a2n-p2p`` owns UDP
identity/discovery/gossip.  This module is the deliberately small seam between
them:

* it advertises only a compact index of this node's signed supply projections;
* a consumer fetches the actual Agent Card over HTTP and verifies its signature,
  owner DID and advertised hash before returning it;
* task payloads never travel through gossip.

This is LAN/bootstrap discovery plus an optional UDP hole-punch helper.  A
discovered card is useful only when its advertised HTTP endpoint is reachable
by the caller.  Hole punching coordinates with an alive shared neighbour and
works for cone NATs only; symmetric NATs must fall back to the sealed relay.
No task payload ever travels through this layer.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
import copy
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from a2n_p2p import Envelope, Identity, MAX_GOSSIP_BYTES, OFFER, P2PNode

from .card import card_did, card_hash, card_skills, verify_card


ADVERT_PROTOCOL = "a2n-projection-discovery/1"
# Keep the application payload small enough that the complete signed UDP
# envelope remains below the transport's MTU-safe ceiling.
MAX_ADVERT_BYTES = 600
MAX_CARD_BYTES = 1_000_000
MAX_DIRECTORY_BYTES = 2_000_000
MAX_LOCAL_CARDS = 256
MAX_FETCH_PER_QUERY = 64
MAX_DISCOVERED_CACHE = 512
MAX_PEER_METRICS = 512
MAX_PEER_PROBES = 32
PEER_HISTORY = 30


CardFetcher = Callable[[str, float], dict[str, Any] | None]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Keep TLS identity on the advertised host while connecting to the UDP peer."""

    def __init__(self, host: str, connect_host: str, *, port: int, timeout: float):
        self._connect_host = connect_host
        super().__init__(host, port=port, timeout=timeout,
                         context=ssl.create_default_context())

    def connect(self) -> None:
        sock = socket.create_connection((self._connect_host, self.port),
                                        self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class P2PDiscoveryService:
    """Expose signed runtime projections over the existing P2P discovery plane.

    ``identity`` must be the same ``Identity`` used by the daemon's projection
    signer.  There is no second key store and no second card-verification
    implementation in this service.
    """

    def __init__(self, identity: Identity, *, port: int = 9701,
                 bootstrap: Iterable[tuple[str, int]] | None = None,
                 beacon: bool = True, beacon_interval: float = 2.0,
                 host: str = "0.0.0.0", advertise_host: str = "127.0.0.1",
                 fetch_timeout: float = 3.0,
                 fetcher: CardFetcher | None = None,
                 probe_interval: float = 5.0,
                 probe_timeout: float = 0.8) -> None:
        if not isinstance(identity, Identity):
            raise TypeError("identity 必须是 a2n_p2p.Identity")
        if not 0 < int(port) <= 65_535:
            raise ValueError("P2P UDP 端口必须在 1–65535 之间")
        if fetch_timeout <= 0:
            raise ValueError("卡片拉取超时必须大于 0")
        if probe_interval <= 0 or probe_timeout <= 0:
            raise ValueError("邻居探测间隔和超时必须大于 0")
        peers: list[tuple[str, int]] = []
        for addr in bootstrap or ():
            if (not isinstance(addr, (tuple, list)) or len(addr) != 2
                    or not str(addr[0]).strip()
                    or not 0 < int(addr[1]) <= 65_535):
                raise ValueError(f"bootstrap 地址不合法：{addr!r}")
            peers.append((str(addr[0]).strip(), int(addr[1])))

        self.identity = identity
        self.fetch_timeout = float(fetch_timeout)
        self.probe_interval = float(probe_interval)
        self.probe_timeout = float(probe_timeout)
        self._fetcher = fetcher
        self._lock = threading.RLock()
        self._discover_lock = threading.Lock()
        self._cards: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._descriptors: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._offer_offsets: dict[str, int] = {}
        self._discovered: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._peer_metrics: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._running = False
        self._probe_stop = threading.Event()
        self._probe_thread: threading.Thread | None = None
        self._probe_offset = 0
        self._fetch_pool: ThreadPoolExecutor | None = None
        self._fetch_inflight: set[Any] = set()
        self.p2p = P2PNode(
            identity, port=int(port), bootstrap=peers, beacon=beacon,
            beacon_interval=beacon_interval, host=host,
            advertise_host=advertise_host, advert=self._summary_payload(),
            offer_provider=self._offer_for_skill,
        )

    # ---------------- lifecycle ----------------

    def start(self) -> "P2PDiscoveryService":
        with self._lock:
            if self._running:
                return self
            skills = self._skills_locked()
            advert = self._summary_payload()
            self.p2p.skills = skills
            self.p2p.advert = advert
            self.p2p.start()
            self._running = True
            self._probe_stop.clear()
            self._probe_thread = threading.Thread(
                target=self._probe_loop, daemon=True, name="a2n-peer-probe")
            self._probe_thread.start()
        # Announce after the socket exists.  Bootstrap HELLO already carries the
        # same manifest; CARD improves propagation to established neighbours.
        self.p2p.announce(skills, advert)
        return self

    def stop(self) -> None:
        with self._lock:
            was_running = self._running
            self._running = False
            self._probe_stop.set()
        if was_running:
            self.p2p.stop()
        if self._probe_thread and self._probe_thread is not threading.current_thread():
            batches = (MAX_PEER_PROBES + 7) // 8
            self._probe_thread.join(timeout=max(2.0, self.probe_timeout * batches + 2))
        self._probe_thread = None
        with self._lock:
            pool = self._fetch_pool
            self._fetch_pool = None
            inflight = list(self._fetch_inflight)
            self._fetch_inflight.clear()
        for future in inflight:
            future.cancel()
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- local supply ----------------

    def advertise(self, cards: Iterable[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
        """Replace this node's advertised supply projections.

        Each card must be self-signed by ``identity`` and have an HTTP(S)
        endpoint.  Consumer/local-workbench projections are rejected: importing
        an Agent for personal use must not silently republish it as this person's
        supply.
        """
        incoming = [cards] if isinstance(cards, dict) else list(cards)
        if len(incoming) > MAX_LOCAL_CARDS:
            raise ValueError(f"单节点最多通告 {MAX_LOCAL_CARDS} 张 Agent Card")

        checked: OrderedDict[str, dict[str, Any]] = OrderedDict()
        descriptors: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for raw in incoming:
            card = copy.deepcopy(raw)
            desc = self._descriptor(card)
            sid = desc["service_id"]
            if sid in descriptors:
                raise ValueError(f"供给标识重复：{sid}")
            checked[sid] = card
            descriptors[sid] = desc

        for desc in descriptors.values():
            self._check_advert_size(self._advert_payload([desc]))
            for skill in desc["skills"]:
                self._check_offer_size(skill, self._advert_payload([desc]))
        skills = sorted({s for d in descriptors.values() for s in d["skills"]})
        with self._lock:
            self._cards = checked
            self._descriptors = descriptors
            self._offer_offsets = {skill: self._offer_offsets.get(skill, 0)
                                   for skill in skills}
            self.p2p.skills = skills
            self.p2p.advert = self._summary_payload(descriptors)
            running = self._running
        if running:
            self.p2p.announce(skills, self.p2p.advert)
        return self.cards()

    # Explicit name for composition roots; ``advertise`` remains the concise API.
    advertise_projected = advertise

    def cards(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(card) for card in self._cards.values()]

    def directory_cards(self, skill: str, *, limit: int = 30,
                        max_age: float = 300.0) -> list[dict[str, Any]]:
        """A bounded read-only view of verified public cards, never private imports.

        A public directory answers from facts this node already verified; an
        unauthenticated HTTP query must not trigger UDP fan-out or card fetches.
        Remote observations expire so an old sighting is not a permanent listing.
        """
        wanted = str(skill or "").strip()
        if not wanted or len(wanted) > 96:
            raise ValueError("能力标识必须为 1–96 个字符")
        count = max(1, min(int(limit), 50))
        now = time.time()
        with self._lock:
            candidates = [
                (copy.deepcopy(self._cards[sid]), desc["card_hash"])
                for sid, desc in self._descriptors.items()
                if wanted in desc["skills"]]
            candidates.extend(
                (copy.deepcopy(row["card"]), digest)
                for digest, row in self._discovered.items()
                if now - row["seen_at"] <= max_age
                and wanted in card_skills(row["card"]))
        result = []
        seen = set()
        used_bytes = 0
        for card, digest in candidates:
            if digest not in seen:
                size = len(json.dumps(card, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8"))
                if used_bytes + size > MAX_DIRECTORY_BYTES:
                    continue
                seen.add(digest)
                result.append(card)
                used_bytes += size
                if len(result) >= count:
                    break
        return result

    def route_hints(self, did: str, *, max_age: float = 300.0) -> list[dict[str, Any]]:
        """Resolve one DID from already verified cards; never dial on public GET."""
        wanted = str(did or "").strip()
        if not wanted or len(wanted) > 200:
            raise ValueError("节点 DID 不合法")
        now = time.time()
        with self._lock:
            local = [
                {"endpoint": desc["endpoint"], "card_hash": desc["card_hash"],
                 "skills": list(desc["skills"]), "seen_at": now}
                for desc in self._descriptors.values()]
            remote = [
                {"endpoint": row["endpoint"], "card_hash": digest,
                 "skills": card_skills(row["card"]), "seen_at": row["seen_at"]}
                for digest, row in self._discovered.items()
                if row["did"] == wanted and now - row["seen_at"] <= max_age]
        return (local if wanted == self.identity.did else remote)[:50]

    def _descriptor(self, card: dict[str, Any]) -> dict[str, Any]:
        ok, reason = verify_card(card, require_endpoint=True)
        if not ok:
            raise ValueError(f"不能通告未通过自证的 Agent Card：{reason}")
        owner = card_did(card)
        if owner != self.identity.did:
            raise ValueError(f"不能替其他节点通告卡片：{owner!r}")
        ext = card.get("x-a2n") or {}
        projection = ext.get("projection") or {}
        if projection.get("role") == "consumer":
            raise ValueError("工作台消费投影不能作为本节点供给重新通告")
        endpoint = self._endpoint(card.get("url"))
        skills = sorted(set(card_skills(card)))
        if not skills:
            raise ValueError("Agent Card 没有可通告的技能")
        digest = card_hash(card)
        service_id = str(projection.get("service_id") or ext.get("uid")
                         or ext.get("node_id") or digest[:24])
        if not service_id or len(service_id) > 200:
            raise ValueError("Agent Card 的供给标识为空或过长")
        return {
            "service_id": service_id,
            "endpoint": endpoint,
            "card_hash": digest,
            "skills": skills,
        }

    @staticmethod
    def _endpoint(value: Any) -> str:
        endpoint = str(value or "").rstrip("/")
        parsed = urlsplit(endpoint)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError(f"卡片地址端口不合法：{value!r}") from exc
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError(f"卡片地址不是可拉取的 HTTP(S) 入口：{value!r}")
        return endpoint

    @staticmethod
    def _advert_payload(descriptors: list[dict[str, Any]]) -> dict[str, Any]:
        return {"protocol": ADVERT_PROTOCOL, "cards": copy.deepcopy(descriptors)}

    def _summary_payload(self, descriptors=None) -> dict[str, Any]:
        values = list((descriptors if descriptors is not None else self._descriptors).values())
        return {"protocol": ADVERT_PROTOCOL, "card_count": len(values)}

    def _offer_for_skill(self, skill: str) -> dict[str, Any]:
        """Build a skill-specific, MTU-safe offer instead of gossiping a catalog."""
        with self._lock:
            matches = [copy.deepcopy(value) for value in self._descriptors.values()
                       if skill in value["skills"]]
            start = self._offer_offsets.get(skill, 0) % max(1, len(matches))
            ordered = matches[start:] + matches[:start]
            selected: list[dict[str, Any]] = []
            for descriptor in ordered:
                candidate = self._advert_payload(selected + [descriptor])
                try:
                    self._check_advert_size(candidate)
                    self._check_offer_size(skill, candidate)
                except ValueError:
                    break
                selected.append(descriptor)
            if matches:
                # Large same-skill catalogs rotate fairly across queries instead
                # of making insertion-order winners permanently discoverable.
                self._offer_offsets[skill] = (start + max(1, len(selected))) % len(matches)
        advert = self._advert_payload(selected)
        if len(selected) < len(matches):
            advert["truncated"] = True
            advert["total"] = len(matches)
        return advert

    @staticmethod
    def _check_advert_size(advert: dict[str, Any]) -> None:
        size = len(json.dumps(advert, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > MAX_ADVERT_BYTES:
            raise ValueError(
                f"P2P 卡片索引为 {size} 字节，超过安全上限 {MAX_ADVERT_BYTES}；"
                "请拆分到不同节点，完整卡片不会走 gossip"
            )

    def _check_offer_size(self, skill: str, advert: dict[str, Any]) -> None:
        sample = Envelope(self.identity.did, OFFER, {
            "query_id": "msg_" + "x" * 32,
            "skill": str(skill)[:96],
            "did": self.identity.did,
            "skills": [str(skill)[:96]],
            "advert": advert,
            "pub": self.p2p.pub_b64(),
        }, ttl=1).sign(self.identity)
        size = len(sample.to_bytes())
        if size > MAX_GOSSIP_BYTES:
            raise ValueError(
                f"P2P 签名发现包为 {size} 字节，超过安全上限 {MAX_GOSSIP_BYTES}；"
                "请缩短能力标识或入口地址"
            )

    def _skills_locked(self) -> list[str]:
        return sorted({s for d in self._descriptors.values() for s in d["skills"]})

    # ---------------- remote discovery ----------------

    def discover(self, skill: str, *, timeout: float = 2.0) -> list[dict[str, Any]]:
        """Return verified Agent Cards offering ``skill``.

        Discovery offers are only signed routing hints.  This method still
        fetches each card, verifies its self-proof, checks that its DID equals the
        offering node, and compares the full-card hash with the gossiped hash.
        """
        wanted = str(skill or "").strip()
        if not wanted:
            raise ValueError("发现技能不能为空")
        if timeout <= 0:
            raise ValueError("发现超时必须大于 0")
        # P2PNode supports distinct query IDs, but serialising here also keeps a
        # caller from multiplying remote HTTP fetches through concurrent UI taps.
        with self._discover_lock:
            deadline = time.monotonic() + float(timeout)
            query_budget = min(float(timeout), max(0.05, float(timeout) * 0.55))
            offers = self.p2p.query(wanted, timeout=query_budget)
            candidates = self._candidates(offers, wanted)
            found: list[dict[str, Any]] = []
            seen: set[str] = set()
            cursor = 0
            limited = candidates[:MAX_FETCH_PER_QUERY]
            while cursor < len(limited):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                batch = limited[cursor:cursor + 8]
                future_rows = {}
                per_fetch = min(self.fetch_timeout, remaining)
                for index, (did, desc) in enumerate(batch):
                    future = self._submit_fetch(desc, per_fetch)
                    if future is None:
                        break
                    future_rows[future] = (index, did, desc)
                if not future_rows:
                    break
                done, pending = wait(future_rows, timeout=remaining)
                by_index = {}
                for future in done:
                    index, did, desc = future_rows[future]
                    try:
                        raw = future.result()
                    except Exception:
                        continue
                    card = self._verified_remote(raw, did=did, descriptor=desc,
                                                 skill=wanted)
                    if card is not None:
                        by_index[index] = (did, desc, card)
                for index in sorted(by_index):
                    did, desc, card = by_index[index]
                    digest = card_hash(card)
                    if digest in seen:
                        continue
                    seen.add(digest)
                    found.append(card)
                    self._remember(did, desc["endpoint"], digest, card)
                if pending or len(future_rows) < len(batch):
                    break
                cursor += len(batch)
            return [copy.deepcopy(card) for card in found]

    def _submit_fetch(self, descriptor: dict[str, Any], timeout: float):
        """Use one shared bounded pool; timed-out searches cannot spawn forever."""
        with self._lock:
            self._fetch_inflight = {future for future in self._fetch_inflight
                                    if not future.done()}
            if len(self._fetch_inflight) >= 8:
                return None
            if self._fetch_pool is None:
                self._fetch_pool = ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="a2n-card-fetch")
            endpoint = descriptor["endpoint"]
            args = ((endpoint, timeout) if self._fetcher else
                    (endpoint, timeout, descriptor["source_host"]))
            future = self._fetch_pool.submit(
                self._fetcher if self._fetcher else self._fetch_card, *args)
            self._fetch_inflight.add(future)
            future.add_done_callback(self._fetch_finished)
            return future

    def _fetch_finished(self, future) -> None:
        with self._lock:
            self._fetch_inflight.discard(future)

    def _candidates(self, offers: Iterable[dict[str, Any]], skill: str) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[str, str, str]] = set()
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            did = str(offer.get("did") or "")
            source_host = str(offer.get("_source_host") or "")
            advert = offer.get("advert") or {}
            peer = self.p2p.table.get(did)
            # Full-card HTTP fetch is intentionally direct-neighbour-only.  A
            # relayed OFFER cannot prove that an arbitrary URL belongs to the
            # provider and must not turn this node into an SSRF client.
            if (not did or not source_host or not peer or peer.host != source_host
                    or not isinstance(advert, dict)):
                continue
            descriptors = advert.get("cards")
            if advert.get("protocol") != ADVERT_PROTOCOL or not isinstance(descriptors, list):
                # Compatibility with the existing one-card SovereignNode advert.
                endpoint = advert.get("http")
                descriptors = ([{"service_id": did, "endpoint": endpoint,
                                 "card_hash": advert.get("card_hash"),
                                 "skills": offer.get("skills") or []}]
                               if endpoint else [])
            for raw in descriptors:
                if not isinstance(raw, dict):
                    continue
                skills = raw.get("skills") or []
                if not isinstance(skills, list) or skill not in skills:
                    continue
                try:
                    endpoint = self._endpoint(raw.get("endpoint"))
                except ValueError:
                    continue
                digest = str(raw.get("card_hash") or "")
                if not digest:
                    continue
                key = (did, endpoint, digest)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((did, {
                    "service_id": str(raw.get("service_id") or ""),
                    "endpoint": endpoint, "card_hash": digest,
                    "skills": list(skills), "source_host": source_host,
                }))
        return candidates

    @staticmethod
    def _fetch_card(endpoint: str, timeout: float,
                    source_host: str) -> dict[str, Any] | None:
        """Fetch without redirects, with a hard body cap and a pinned peer IP."""
        parsed = urlsplit(endpoint)
        source_ip = ipaddress.ip_address(source_host)
        if source_ip.is_unspecified or source_ip.is_multicast:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        resolved = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        }
        if source_ip not in resolved:
            return None
        path = (parsed.path.rstrip("/") + "/.well-known/agent.json") or \
            "/.well-known/agent.json"
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _PinnedHTTPSConnection(
                parsed.hostname, source_host, port=port, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(source_host, port=port,
                                                    timeout=timeout)
        try:
            connection.request("GET", path, headers={"Accept": "application/json",
                                                      "Host": parsed.netloc})
            response = connection.getresponse()
            if response.status != 200:
                return None
            declared = response.getheader("Content-Length")
            if declared and int(declared) > MAX_CARD_BYTES:
                return None
            raw = response.read(MAX_CARD_BYTES + 1)
            if len(raw) > MAX_CARD_BYTES:
                return None
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
            return None
        finally:
            connection.close()

    @staticmethod
    def _verified_remote(raw: Any, *, did: str, descriptor: dict[str, Any],
                         skill: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        ok, _reason = verify_card(raw, require_endpoint=True)
        if not ok or card_did(raw) != did:
            return None
        if card_hash(raw) != descriptor["card_hash"]:
            return None
        if skill not in card_skills(raw):
            return None
        try:
            if P2PDiscoveryService._endpoint(raw.get("url")) != descriptor["endpoint"]:
                return None
        except ValueError:
            return None
        return copy.deepcopy(raw)

    def _remember(self, did: str, endpoint: str, digest: str,
                  card: dict[str, Any]) -> None:
        with self._lock:
            self._discovered[digest] = {
                "did": did, "endpoint": endpoint, "card_hash": digest,
                "seen_at": time.time(), "card": copy.deepcopy(card),
            }
            self._discovered.move_to_end(digest)
            while len(self._discovered) > MAX_DISCOVERED_CACHE:
                self._discovered.popitem(last=False)

    # ---------------- peer observability ----------------

    def probe(self, did: str) -> dict[str, Any]:
        """Run one signed control-plane RTT probe against a direct neighbour."""
        peer_id = str(did or "").strip()
        peer = self.p2p.table.get(peer_id)
        if not peer or not peer.alive():
            raise ValueError("邻居不存在或已经离线")
        rtt = self.p2p.ping(peer_id, timeout=self.probe_timeout)
        return self._record_probe(peer_id, rtt)

    def punch(self, did: str, *, helper_did: str | None = None,
              timeout: float = 4.0) -> dict[str, Any]:
        """UDP 打洞：经一位活跃邻居协调，与目标 DID 对射打通直连。

        只协调发现/连通，不携带也不代理任何任务载荷。锥形 NAT 通常可通；
        对称型 NAT 打不通，应改走密封中继 —— 返回值如实说明。
        成功与否由邻居表可见：打通后对方 via="punch"。
        """
        peer_id = str(did or "").strip()
        if not peer_id:
            raise ValueError("目标 DID 不能为空")
        return self.p2p.punch(peer_id, helper_did=helper_did, timeout=timeout)

    def _probe_loop(self) -> None:
        # Give the bootstrap/HELLO exchange a short head start before the first
        # sample; later cycles use the configured VPN-like heartbeat cadence.
        if self._probe_stop.wait(min(0.25, self.probe_interval)):
            return
        while not self._probe_stop.is_set():
            alive = self.p2p.table.alive()
            if alive:
                start = self._probe_offset % len(alive)
                ordered = alive[start:] + alive[:start]
                peers = ordered[:MAX_PEER_PROBES]
                self._probe_offset = (start + len(peers)) % len(alive)
            else:
                peers = []
            if peers:
                workers = min(8, len(peers))
                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="a2n-peer-rtt") as pool:
                    futures = [pool.submit(self.probe, peer.did) for peer in peers]
                    for future in futures:
                        try:
                            future.result()
                        except Exception:  # one dead neighbour must not stop monitoring
                            pass
            if self._probe_stop.wait(self.probe_interval):
                return

    def _record_probe(self, did: str, rtt_ms: float | None) -> dict[str, Any]:
        now = time.time()
        sample = {"ts": now, "reachable": rtt_ms is not None,
                  "rtt_ms": round(float(rtt_ms), 2) if rtt_ms is not None else None}
        with self._lock:
            previous = self._peer_metrics.get(did) or {"history": []}
            history = list(previous.get("history") or [])[-(PEER_HISTORY - 1):]
            history.append(sample)
            value = {"did": did, "reachable": sample["reachable"],
                     "rtt_ms": sample["rtt_ms"], "last_probe": now,
                     "history": history}
            self._peer_metrics[did] = value
            self._peer_metrics.move_to_end(did)
            while len(self._peer_metrics) > MAX_PEER_METRICS:
                self._peer_metrics.popitem(last=False)
            return copy.deepcopy(value)

    # ---------------- observability ----------------

    def snapshot(self) -> dict[str, Any]:
        """Serializable network view for the management/UI layer."""
        with self._lock:
            running = self._running
            local = copy.deepcopy(list(self._descriptors.values()))
            discovered = copy.deepcopy(list(self._discovered.values()))
            metrics = copy.deepcopy(self._peer_metrics)
        peers = [{
            "did": p.did,
            "address": [p.host, p.port],
            "skills": list(p.skills),
            "via": p.via,
            "last_seen": p.last_seen,
            "verified_envelope_key": bool(p.pub_raw),
            **metrics.get(p.did, {"reachable": None, "rtt_ms": None,
                                  "last_probe": None, "history": []}),
        } for p in self.p2p.table.alive()]
        return {
            "running": running,
            "did": self.identity.did,
            "udp": {"listen": [self.p2p.host, self.p2p.port],
                    "advertise_host": self.p2p.advertise_host},
            "network": {
                "mode": "lan-bootstrap-gossip+punch",
                "lan_beacon": bool(self.p2p.beacon),
                "bootstrap": [list(x) for x in self.p2p.bootstrap],
                "nat_traversal": True,
                "punched_peers": sum(
                    1 for p in self.p2p.table.alive() if p.via == "punch"),
                "observed_endpoint": (
                    list(self.p2p.self_endpoint)
                    if self.p2p.self_endpoint else None),
                "relay": False,
            },
            "local_cards": local,
            "discovered": discovered,
            "peers": peers,
            "stats": dict(self.p2p.stats),
        }


__all__ = ["P2PDiscoveryService", "ADVERT_PROTOCOL"]
