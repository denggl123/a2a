"""Opt-in, bounded encrypted file ranges; independent of mandatory discovery."""
from __future__ import annotations

from collections import deque
import math
import time

from a2n_sdk.assets import CHUNK
from a2n_sdk.experience import signed, unsigned

from .asset_service import VERSION as ASSET_VERSION
from .feedback_identity import signer_for, verifier_for
from .metadata_mailbox import PublicMetadataMailbox, MetadataMailboxClient
from .secure_metadata import decode

VERSION = "a2n-asset-mailbox/1"
LEASE_VERSION = "a2n-asset-lease/1"
PREFIX = "/public/v1/asset-mailbox/"
GLOBAL_BYTES = 32 * 1024 * 1024
AUTHOR_BYTES = 16 * 1024 * 1024


def verify_lease(lease):
    try:
        issue, expiry = lease["issued_at"], lease["expires_at"]
        return (set(lease) == {"v", "author_did", "relay_did", "endpoint", "domains", "issued_at", "expires_at", "proof"}
            and lease["v"] == LEASE_VERSION and lease["domains"] == [ASSET_VERSION]
            and isinstance(lease["endpoint"], str) and len(lease["endpoint"]) <= 2048
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (issue, expiry))
            and 0 < expiry - issue <= 90 and issue - 30 <= time.time() < expiry
            and verifier_for()(lease["proof"], unsigned(lease)))
    except (ValueError, KeyError, TypeError):
        return False


class PublicAssetMailbox(PublicMetadataMailbox):
    version, prefix, domains = VERSION, PREFIX, [ASSET_VERSION]
    lease_validator = staticmethod(verify_lease)
    global_rate, author_rate, request_cap = 512, 256, 8192

    def __init__(self, identity, *, endpoint, enabled):
        super().__init__(identity, endpoint=endpoint)
        self._enabled = enabled
        self._bytes = deque()

    def enabled(self):
        return bool(self._enabled())

    def operations(self, domain):
        return {"READ_RANGE"} if domain == ASSET_VERSION else ()

    def reserve(self, inner):
        body = inner["body"]
        if not isinstance(body, dict) or set(body) != {"asset_id", "trade_uid", "start", "end", "recipient_key"}:
            raise ValueError("INVALID_ASSET_REQUEST")
        start, end = body["start"], body["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start <= end < 16 * 1024 * 1024 or end - start >= CHUNK:
            raise ValueError("ASSET_RANGE_LIMIT")
        if not isinstance(body["asset_id"], str) or len(body["asset_id"]) > 64 or not isinstance(body["trade_uid"], str) or len(body["trade_uid"]) > 80:
            raise ValueError("INVALID_ASSET_IDENTITY")
        decode(body["recipient_key"], 32)
        now, size, author = time.monotonic(), end - start + 1, inner["author_did"]
        while self._bytes and self._bytes[0][0] < now - 60:
            self._bytes.popleft()
        if sum(n for _, _, n in self._bytes) + size > GLOBAL_BYTES or sum(n for _, a, n in self._bytes if a == author) + size > AUTHOR_BYTES:
            raise ValueError("ASSET_BYTE_BUDGET_EXCEEDED")
        # Failed forwards consume reservation too, preventing retry amplification.
        self._bytes.append((now, author, size))

    def valid_response(self, response):
        body = response.get("body")
        if response.get("op") == "ERROR":
            return isinstance(body, dict) and set(body) == {"code"} and isinstance(body["code"], str) and len(body["code"]) <= 128
        if not isinstance(body, dict) or set(body) != {"sealed_message"} or not isinstance(body["sealed_message"], dict):
            return False
        sealed = body["sealed_message"]
        try:
            if set(sealed) != {"v", "ephemeral_key", "nonce", "ciphertext"} or sealed["v"] != "a2n-private-asset/1":
                return False
            decode(sealed["ephemeral_key"], 32)
            decode(sealed["nonce"], 12)
            return 16 <= len(decode(sealed["ciphertext"], maximum=125000)) <= 93750
        except (ValueError, TypeError, KeyError):
            return False

    def handle(self, action, request):
        status, response = super().handle(action, request)
        if status == 200 and action == "capabilities":
            body = {**response["body"], "global_bytes_per_minute": GLOBAL_BYTES,
                "author_bytes_per_minute": AUTHOR_BYTES, "range_cap": CHUNK,
                "retention": "EPHEMERAL_ENCRYPTED", "opt_in_service": "blob_cache"}
            response = self._response(request, body)
        return status, response


class AssetMailboxClient(MetadataMailboxClient):
    version, prefix, name, poll_interval = VERSION, PREFIX, "a2n-asset-mailbox", .1

    def make_lease(self, relay, capability):
        if capability.get("protocol") != VERSION or capability.get("domains") != [ASSET_VERSION]:
            raise ValueError("ASSET_MAILBOX_UNSUPPORTED")
        now = time.time()
        return signed({"v": LEASE_VERSION, "author_did": self.identity.did, **relay,
            "domains": [ASSET_VERSION], "issued_at": now, "expires_at": now + 90}, signer_for(self.identity))

    def dispatch(self, request):
        return self.experience.handle("read", request)
