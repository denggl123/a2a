"""Optional opaque hash witness, not a ledger or custody service.

The volunteer sees two DID signatures on one receipt hash, never the receipt,
skill, price, task id, input or output. The parties keep the underlying facts.
"""
from __future__ import annotations

from collections import defaultdict, deque
import threading
import time

from a2n_kernel.hashing import now_iso
from a2n_p2p import verify_pub

from . import receipt as proof
from .card import did_from_pub, pub_b64, pub_unb64

MAX_WITNESSES = 100_000
MAX_PER_CALLER_PER_MINUTE = 30


def _digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value)


def _statement(receipt_hash: str) -> dict:
    return {"v": 1, "purpose": "a2n-public-witness", "receipt_hash": receipt_hash}


def attest(identity, receipt_hash: str) -> dict:
    """Each party signs only the opaque digest, never a transaction summary."""
    if not _digest(receipt_hash):
        raise ValueError("收据哈希不合法")
    return {"by": identity.did, "pub": pub_b64(identity.pub_raw),
            "sig": identity.sign(_statement(receipt_hash))}


def _verify_attestation(item: dict, receipt_hash: str) -> bool:
    if not isinstance(item, dict) or set(item) != {"by", "pub", "sig"}:
        return False
    try:
        public_key = pub_unb64(item["pub"])
        return (did_from_pub(public_key) == item.get("by")
                and verify_pub(public_key, _statement(receipt_hash),
                               item.get("sig") or ""))
    except (KeyError, TypeError, ValueError):
        return False


def claim(receipt_hash: str, provider: dict, caller: dict) -> dict:
    candidate = {"v": 1, "receipt_hash": receipt_hash,
                 "provider": provider, "caller": caller}
    if not verify_claim(candidate):
        raise ValueError("双方哈希见证签名无效")
    return candidate


def verify_claim(value: dict, *, receipt: dict | None = None) -> bool:
    if (not isinstance(value, dict) or value.get("v") != 1
            or set(value) != {"v", "receipt_hash", "provider", "caller"}):
        return False
    digest = value.get("receipt_hash")
    provider, caller = value.get("provider"), value.get("caller")
    if (not _digest(digest) or not _verify_attestation(provider, digest)
            or not _verify_attestation(caller, digest)
            or provider["by"] == caller["by"]):
        return False
    if receipt is not None:
        return (proof.verify(receipt)[0] and proof.fingerprint(receipt) == digest
                and receipt.get("provider_did") == provider["by"]
                and receipt.get("caller_did") == caller["by"])
    return True


def verify_witness(record: dict, *, receipt_hash: str | None = None,
                   signed_claim: dict | None = None) -> bool:
    """Verify the witness stamp and, if supplied, the bilateral hash claim."""
    if not isinstance(record, dict) or not isinstance(record.get("body"), dict):
        return False
    body = record["body"]
    try:
        public_key = pub_unb64(record["pub"])
    except (KeyError, TypeError, ValueError):
        return False
    if (did_from_pub(public_key) != record.get("by")
            or body.get("witness_did") != record.get("by")
            or not _digest(body.get("receipt_hash"))
            or not _digest(body.get("claim_hash"))
            or (receipt_hash is not None and body["receipt_hash"] != receipt_hash)):
        return False
    if signed_claim is not None and (not verify_claim(signed_claim)
            or signed_claim["receipt_hash"] != body["receipt_hash"]
            or proof.fingerprint(signed_claim) != body["claim_hash"]):
        return False
    try:
        return verify_pub(public_key, body, record.get("sig") or "")
    except (TypeError, ValueError):
        return False


class PublicWitness:
    def __init__(self, identity, store):
        self.identity, self.store = identity, store
        self._lock = threading.RLock()
        self._recent = defaultdict(deque)

    def witness(self, signed_claim: dict) -> dict:
        if not verify_claim(signed_claim):
            raise ValueError("公共见证需要双方对同一收据哈希的有效签名")
        digest = signed_claim["receipt_hash"]
        claim_hash = proof.fingerprint(signed_claim)
        with self._lock:
            existing = self.store.get("public_witnesses", digest)
            if existing:
                if (existing.get("body") or {}).get("claim_hash") != claim_hash:
                    raise ValueError("这份收据已有不同双方签名的见证")
                return existing
            if self.store.count("public_witnesses") >= MAX_WITNESSES:
                raise ValueError("本节点公共见证容量已满")
            caller = signed_claim["caller"]["by"]
            now = time.monotonic()
            if caller not in self._recent and len(self._recent) >= 4096:
                stale = [did for did, times in self._recent.items()
                         if not times or now - times[-1] > 60]
                for did in stale:
                    del self._recent[did]
                if len(self._recent) >= 4096:
                    raise ValueError("本节点公共见证当前请求过多")
            recent = self._recent[caller]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= MAX_PER_CALLER_PER_MINUTE:
                raise ValueError("见证请求过于频繁，请稍后再试")
            recent.append(now)
            body = {"v": 1, "receipt_hash": digest,
                    "claim_hash": claim_hash,
                    "witnessed_at": now_iso(), "witness_did": self.identity.did}
            record = {"body": body, "by": self.identity.did,
                      "pub": pub_b64(self.identity.pub_raw),
                      "sig": self.identity.sign(body)}
            self.store.put("public_witnesses", digest, record)
            return record

    def get(self, receipt_hash: str) -> dict | None:
        digest = str(receipt_hash or "")
        if not _digest(digest):
            raise ValueError("收据哈希不合法")
        return self.store.get("public_witnesses", digest)
