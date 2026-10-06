"""Private chunked delivery assets, stored by the same encrypted local journal."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time

CHUNK = 65536
MAX_ASSET = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
REF_FIELDS = {"v", "asset_id", "owner_did", "sha256", "size", "mime_type"}


def valid_ref(ref):
    return (isinstance(ref, dict) and set(ref) == REF_FIELDS and ref.get("v") == "a2n-asset/1"
        and isinstance(ref["asset_id"], str) and re.fullmatch(r"as_[0-9a-f]{32}", ref["asset_id"])
        and isinstance(ref["owner_did"], str) and 1 <= len(ref["owner_did"]) <= 128
        and isinstance(ref["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
        and type(ref["size"]) is int and 1 <= ref["size"] <= MAX_ASSET
        and isinstance(ref["mime_type"], str) and re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", ref["mime_type"]))


class AssetBook:
    def __init__(self, store, *, node_did):
        self.store, self.node_did = store, node_did
        # A crashed upload is never a delivered file and does not retain capacity.
        for asset_id, row in store.items("assets").items():
            if row.get("state") == "UPLOADING":
                self._discard(asset_id, row)

    def _discard(self, asset_id, row):
        with self.store.tx():
            for i in range((row["size"] + CHUNK - 1) // CHUNK):
                self.store.delete("asset_chunks", f"{asset_id}:{i}")
            self.store.delete("assets", asset_id)

    def upload(self, size, mime_type, chunks, *, origin=None):
        if type(size) is not int or not 1 <= size <= MAX_ASSET:
            raise ValueError("ASSET_SIZE_LIMIT")
        if not isinstance(mime_type, str) or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime_type) or len(mime_type) > 80:
            raise ValueError("INVALID_ASSET_MIME")
        asset_id = "as_" + secrets.token_hex(16)
        row = {"asset_id": asset_id, "size": size, "mime_type": mime_type, "state": "UPLOADING", "created_at": time.time()}
        with self.store.tx():
            existing = list(self.store.items("assets").values())
            if len(existing) >= 128 or sum(r["size"] for r in existing) + size > MAX_TOTAL:
                raise ValueError("ASSET_CAPACITY_LIMIT")
            self.store.put("assets", asset_id, row)
        count, index, buffer, hasher = 0, 0, bytearray(), hashlib.sha256()
        try:
            for chunk in chunks:
                if not isinstance(chunk, bytes) or len(chunk) > CHUNK:
                    raise ValueError("INVALID_ASSET_CHUNK")
                count += len(chunk)
                if count > size:
                    raise ValueError("ASSET_LENGTH_MISMATCH")
                hasher.update(chunk)
                buffer.extend(chunk)
                while len(buffer) >= CHUNK:
                    self.store.put("asset_chunks", f"{asset_id}:{index}", base64.b64encode(buffer[:CHUNK]).decode())
                    del buffer[:CHUNK]
                    index += 1
            if count != size:
                raise ValueError("ASSET_LENGTH_MISMATCH")
            if buffer:
                self.store.put("asset_chunks", f"{asset_id}:{index}", base64.b64encode(buffer).decode())
            sha = hasher.hexdigest()
            if origin and (not valid_ref(origin) or sha != origin["sha256"] or size != origin["size"] or mime_type != origin["mime_type"]):
                raise ValueError("ASSET_DIGEST_MISMATCH")
            ref = {"v": "a2n-asset/1", "asset_id": asset_id, "owner_did": self.node_did,
                   "sha256": sha, "size": size, "mime_type": mime_type}
            self.store.put("assets", asset_id, {**row, "state": "READY", "ref": ref, "origin": origin})
            return ref
        except BaseException:
            self._discard(asset_id, row)
            raise

    def owned(self, ref):
        row = self.store.get("assets", ref.get("asset_id", "")) if valid_ref(ref) else None
        return bool(row and row.get("state") == "READY" and row.get("ref") == ref)

    def ref(self, asset_id):
        row = self.store.get("assets", asset_id)
        if not row or row.get("state") != "READY":
            raise ValueError("ASSET_NOT_FOUND")
        return row["ref"]

    def chunks(self, asset_id, start=0, end=None):
        ref = self.ref(asset_id)
        end = ref["size"] - 1 if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end < ref["size"]:
            raise ValueError("ASSET_RANGE_INVALID")
        for i in range(start // CHUNK, end // CHUNK + 1):
            chunk = base64.b64decode(self.store.get("asset_chunks", f"{asset_id}:{i}"), validate=True)
            lower, upper = max(start - i * CHUNK, 0), min(end - i * CHUNK + 1, len(chunk))
            yield chunk[lower:upper]

    def grant(self, ref, trade_uid, buyer_did):
        if not self.owned(ref):
            raise ValueError("ASSET_NOT_OWNED")
        self.store.put("asset_grants", ref["asset_id"] + ":" + trade_uid,
                       {"buyer_did": buyer_did, "trade_uid": trade_uid, "ref": ref})

    def authorized(self, asset_id, trade_uid, buyer_did):
        row = self.store.get("asset_grants", asset_id + ":" + trade_uid)
        return bool(row and row["buyer_did"] == buyer_did)

    def list(self):
        return [r["ref"] for r in self.store.items("assets").values() if r.get("state") == "READY"]
