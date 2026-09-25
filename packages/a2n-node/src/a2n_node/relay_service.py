"""Voluntary bounded mailbox: moves sealed bytes, never Agent payloads."""
from __future__ import annotations

from collections import deque
import copy
import threading
import time
import uuid

from a2n_kernel.hashing import new_id
from a2n_p2p import verify_pub

from .card import card_did, card_skills, did_from_pub, pub_b64, pub_unb64, verify_card
from .peer import ReplayGuard
from .receipt import hash_payload
from .relay_crypto import PROTOCOL, valid_public

MAX_CARDS = 256
MAX_JOBS = 64
LEASE_SECONDS = 45
JOB_SECONDS = 25


def _domain(proof: dict) -> dict:
    return {key: proof.get(key) for key in (
        "v", "purpose", "provider_did", "body_hash", "nonce", "ts")}


def sign_auth(identity, purpose: str, body: dict) -> dict:
    if purpose not in {"register", "poll", "complete"}:
        raise ValueError("中继认证用途无效")
    proof = {"v": 1, "purpose": "a2n-relay-" + purpose,
             "provider_did": identity.did, "body_hash": hash_payload(body),
             "nonce": new_id("relay"), "ts": time.time(),
             "pub": pub_b64(identity.pub_raw)}
    proof["sig"] = identity.sign(_domain(proof))
    return proof


class PublicRelay:
    def __init__(self, base_provider):
        self.base_provider = base_provider
        self.guard = ReplayGuard()
        self._lock = threading.RLock()
        self._available = threading.Condition(self._lock)
        self._cards: dict[tuple[str, str], tuple[dict, float]] = {}
        self._pending: deque[str] = deque()
        self._jobs: dict[str, dict] = {}

    def _authenticate(self, purpose: str, body: dict, proof: dict) -> str:
        if not isinstance(proof, dict) or set(proof) != {
                "v", "purpose", "provider_did", "body_hash", "nonce", "ts",
                "pub", "sig"}:
            raise PermissionError("中继认证结构无效")
        if (proof.get("v") != 1 or proof.get("purpose") != "a2n-relay-" + purpose
                or proof.get("body_hash") != hash_payload(body)
                or not all(isinstance(proof.get(key), str) and proof[key]
                           for key in ("provider_did", "nonce", "pub", "sig"))):
            raise PermissionError("中继认证与请求内容不匹配")
        try:
            pub = pub_unb64(proof["pub"])
            verified = (did_from_pub(pub) == proof["provider_did"]
                        and verify_pub(pub, _domain(proof), proof["sig"]))
        except (ValueError, TypeError):
            verified = False
        if not verified:
            raise PermissionError("中继节点签名无效")
        ok, why = self.guard.check(proof["nonce"], proof.get("ts"))
        if not ok:
            raise PermissionError(why)
        return proof["provider_did"]

    def register(self, body: dict, proof: dict) -> dict:
        did = self._authenticate("register", body, proof)
        cards = body.get("cards")
        if not isinstance(cards, list) or len(cards) > 64:
            raise ValueError("中继卡片列表无效")
        base = self.base_provider().rstrip("/")
        if not base:
            raise ValueError("公共节点未配置可回连入口")
        checked = {}
        for card in cards:
            if not isinstance(card, dict):
                raise ValueError("中继卡片无效")
            ok, why = verify_card(card, require_endpoint=True)
            ext = card.get("x-a2n") or {}
            projection = ext.get("projection") or {}
            relay = ext.get("relay") or {}
            sid = projection.get("service_id")
            expected = f"{base}/relay/v1/{did}/a2a/{sid}"
            if (not ok or card_did(card) != did or projection.get("node_did") != did
                    or projection.get("role") != "supply" or not isinstance(sid, str)
                    or not sid or ext.get("peer_protocol") != "a2n-bilateral-a2a/1"
                    or relay.get("protocol") != PROTOCOL
                    or not valid_public(relay.get("public_key"))
                    or card.get("url") != expected):
                raise ValueError(f"中继 Card 不是此节点对这条路径的有效供给：{why}")
            if (did, sid) in checked:
                raise ValueError("中继供给标识重复")
            checked[(did, sid)] = copy.deepcopy(card)
        with self._lock:
            self._prune_locked()
            others = sum(1 for key in self._cards if key[0] != did)
            if others + len(checked) > MAX_CARDS:
                raise ValueError("公共中继卡片容量已满")
            for key in list(self._cards):
                if key[0] == did:
                    del self._cards[key]
            expires = time.monotonic() + LEASE_SECONDS
            self._cards.update({key: (card, expires) for key, card in checked.items()})
            self._available.notify_all()
        return {"ok": True, "count": len(checked), "lease_seconds": LEASE_SECONDS}

    def directory_cards(self, skill: str, *, limit: int = 30) -> list[dict]:
        with self._lock:
            self._prune_locked()
            return [copy.deepcopy(card) for card, _ in self._cards.values()
                    if skill in card_skills(card)][:max(0, limit)]

    def card(self, did: str, service_id: str) -> dict | None:
        with self._lock:
            self._prune_locked()
            item = self._cards.get((did, service_id))
            return copy.deepcopy(item[0]) if item else None

    def submit(self, did: str, service_id: str, kind: str,
               envelope: dict, *, timeout: float = JOB_SECONDS) -> dict:
        if (kind not in {"a2a", "ack"} or not isinstance(envelope, dict)
                or set(envelope) != {"v", "epk", "nonce", "ciphertext"}
                or envelope.get("v") != 1
                or not valid_public(envelope.get("epk"))
                or not isinstance(envelope.get("nonce"), str)
                or not isinstance(envelope.get("ciphertext"), str)):
            raise ValueError("中继任务无效")
        with self._lock:
            self._prune_locked()
            if (did, service_id) not in self._cards:
                raise ValueError("供给方没有在线中继租约")
            if len(self._jobs) >= MAX_JOBS:
                raise ValueError("中继任务队列已满")
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "provider_did": did,
                   "service_id": service_id, "kind": kind,
                   "envelope": copy.deepcopy(envelope),
                   "created": time.monotonic(), "event": threading.Event(),
                   "result": None}
            self._jobs[job_id] = job
            self._pending.append(job_id)
            self._available.notify_all()
        if not job["event"].wait(min(max(timeout, 0.1), JOB_SECONDS)):
            with self._lock:
                self._jobs.pop(job_id, None)
                try:
                    self._pending.remove(job_id)
                except ValueError:
                    pass
            raise TimeoutError("供给节点未在中继期限内回复；交付状态未知")
        result = job["result"]
        with self._lock:
            self._jobs.pop(job_id, None)
        return result

    def poll(self, body: dict, proof: dict, *, timeout: float = 5.0) -> dict:
        did = self._authenticate("poll", body, proof)
        if body.get("provider_did") != did:
            raise PermissionError("不能代领别人的中继任务")
        deadline = time.monotonic() + min(max(timeout, 0), 8)
        with self._available:
            while True:
                for job_id in list(self._pending):
                    job = self._jobs.get(job_id)
                    if not job:
                        self._pending.remove(job_id)
                    elif job["provider_did"] == did:
                        self._pending.remove(job_id)
                        return {key: copy.deepcopy(job[key]) for key in (
                            "job_id", "service_id", "kind", "envelope")}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"job": None}
                self._available.wait(remaining)

    def complete(self, body: dict, proof: dict) -> dict:
        did = self._authenticate("complete", body, proof)
        with self._lock:
            job = self._jobs.get(str(body.get("job_id") or ""))
            if not job or job["provider_did"] != did:
                raise ValueError("中继任务不存在或不属于此节点")
            if not isinstance(body.get("result"), dict):
                raise ValueError("中继应答封套无效")
            if job["result"] is not None:
                raise ValueError("中继任务已经完成")
            job["result"] = copy.deepcopy(body["result"])
            job["event"].set()
        return {"ok": True}

    def _prune_locked(self):
        now = time.monotonic()
        for key, (_card, expiry) in list(self._cards.items()):
            if expiry < now:
                del self._cards[key]
        for job_id, job in list(self._jobs.items()):
            if now - job["created"] > JOB_SECONDS + 5:
                del self._jobs[job_id]
                try:
                    self._pending.remove(job_id)
                except ValueError:
                    pass
