"""P2P discovery adapter for the persistent local-node runtime.

The SDK runtime owns Agent bindings and projections.  ``a2n-p2p`` owns UDP
identity/discovery/gossip.  This module is the deliberately small seam between
them:

* it advertises only a compact index of this node's signed supply projections;
* a consumer fetches the actual Agent Card over HTTP and verifies its signature,
  owner DID and advertised hash before returning it;
* task payloads never travel through gossip.

This is LAN/bootstrap discovery, not NAT traversal.  A discovered card is useful
only when its advertised HTTP endpoint is reachable by the caller.  Hole
punching, relay selection and public address negotiation belong to the network
path layer and are intentionally not claimed here.
"""
from __future__ import annotations

from collections import OrderedDict
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

from a2n_p2p import Identity, P2PNode

from .card import card_did, card_hash, card_skills, verify_card


ADVERT_PROTOCOL = "a2n-projection-discovery/1"
# Keep the application payload small enough that the complete signed UDP
# envelope remains below the transport's MTU-safe ceiling.
MAX_ADVERT_BYTES = 600
MAX_CARD_BYTES = 1_000_000
MAX_LOCAL_CARDS = 256
MAX_FETCH_PER_QUERY = 64
MAX_DISCOVERED_CACHE = 512


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
                 fetcher: CardFetcher | None = None) -> None:
        if not isinstance(identity, Identity):
            raise TypeError("identity 必须是 a2n_p2p.Identity")
        if not 0 < int(port) <= 65_535:
            raise ValueError("P2P UDP 端口必须在 1–65535 之间")
        if fetch_timeout <= 0:
            raise ValueError("卡片拉取超时必须大于 0")
        peers: list[tuple[str, int]] = []
        for addr in bootstrap or ():
            if (not isinstance(addr, (tuple, list)) or len(addr) != 2
                    or not str(addr[0]).strip()
                    or not 0 < int(addr[1]) <= 65_535):
                raise ValueError(f"bootstrap 地址不合法：{addr!r}")
            peers.append((str(addr[0]).strip(), int(addr[1])))

        self.identity = identity
        self.fetch_timeout = float(fetch_timeout)
        self._fetcher = fetcher
        self._lock = threading.RLock()
        self._discover_lock = threading.Lock()
        self._cards: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._descriptors: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._discovered: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._running = False
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
        # Announce after the socket exists.  Bootstrap HELLO already carries the
        # same manifest; CARD improves propagation to established neighbours.
        self.p2p.announce(skills, advert)
        return self

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self.p2p.stop()

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
        skills = sorted({s for d in descriptors.values() for s in d["skills"]})
        with self._lock:
            self._cards = checked
            self._descriptors = descriptors
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
        selected: list[dict[str, Any]] = []
        for descriptor in matches:
            candidate = self._advert_payload(selected + [descriptor])
            try:
                self._check_advert_size(candidate)
            except ValueError:
                break
            selected.append(descriptor)
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
            offers = self.p2p.query(wanted, timeout=float(timeout))
            candidates = self._candidates(offers, wanted)
            found: list[dict[str, Any]] = []
            seen: set[str] = set()
            for did, desc in candidates[:MAX_FETCH_PER_QUERY]:
                endpoint = desc["endpoint"]
                try:
                    got = (self._fetcher(endpoint, self.fetch_timeout)
                           if self._fetcher else
                           self._fetch_card(endpoint, self.fetch_timeout,
                                            desc["source_host"]))
                except Exception:  # remote input/fetch adapters must not kill discovery
                    continue
                card = self._verified_remote(got, did=did, descriptor=desc,
                                             skill=wanted)
                if card is None:
                    continue
                digest = card_hash(card)
                if digest in seen:
                    continue
                seen.add(digest)
                found.append(card)
                self._remember(did, endpoint, digest, card)
            return [copy.deepcopy(card) for card in found]

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

    # ---------------- observability ----------------

    def snapshot(self) -> dict[str, Any]:
        """Serializable network view for the management/UI layer."""
        with self._lock:
            running = self._running
            local = copy.deepcopy(list(self._descriptors.values()))
            discovered = copy.deepcopy(list(self._discovered.values()))
        peers = [{
            "did": p.did,
            "address": [p.host, p.port],
            "skills": list(p.skills),
            "via": p.via,
            "last_seen": p.last_seen,
            "verified_envelope_key": bool(p.pub_raw),
        } for p in self.p2p.table.alive()]
        return {
            "running": running,
            "did": self.identity.did,
            "udp": {"listen": [self.p2p.host, self.p2p.port],
                    "advertise_host": self.p2p.advertise_host},
            "network": {
                "mode": "lan-bootstrap-gossip",
                "lan_beacon": bool(self.p2p.beacon),
                "bootstrap": [list(x) for x in self.p2p.bootstrap],
                "nat_traversal": False,
                "relay": False,
            },
            "local_cards": local,
            "discovered": discovered,
            "peers": peers,
            "stats": dict(self.p2p.stats),
        }


__all__ = ["P2PDiscoveryService", "ADVERT_PROTOCOL"]
