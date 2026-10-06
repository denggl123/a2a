"""Signed package catalog and explicit owner grants; execution is an injected port."""
from __future__ import annotations

import base64
import hashlib
import re

from .experience import unsigned
from .trade_facts import digest

VERSION = "a2n-agent-package/1"
ABI = "a2n-wasm-json/1"
PERMISSIONS = {"network": "NONE", "filesystem": "NONE"}


class PackageBook:
    def __init__(self, store, verifier):
        self.store, self.verifier = store, verifier

    def preview(self, manifest, module_base64):
        fields = {"v", "author_did", "package_id", "version", "abi", "module_sha256", "card", "permissions", "limits", "royalty", "proof"}
        if (not isinstance(manifest, dict) or set(manifest) != fields or manifest["v"] != VERSION
                or not self.verifier(manifest["proof"], unsigned(manifest))):
            raise ValueError("UNVERIFIED_AGENT_PACKAGE")
        if not isinstance(manifest["package_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", manifest["package_id"]):
            raise ValueError("INVALID_PACKAGE_ID")
        if not isinstance(manifest["version"], str) or not 1 <= len(manifest["version"]) <= 64 or manifest["abi"] != ABI:
            raise ValueError("UNSUPPORTED_PACKAGE_ABI_OR_VERSION")
        if manifest["permissions"] != PERMISSIONS:
            raise ValueError("UNSUPPORTED_PACKAGE_PERMISSION")
        limits = manifest["limits"]
        ranges = {"memory_bytes": (65536, 32 * 1024**2), "fuel": (100, 10_000_000),
                  "output_bytes": (1, 262144), "timeout_ms": (100, 10000)}
        if not isinstance(limits, dict) or set(limits) != set(ranges):
            raise ValueError("INVALID_PACKAGE_LIMITS")
        for name, (low, high) in ranges.items():
            value = limits[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError("INVALID_PACKAGE_LIMITS")
        card = manifest["card"]
        if (not isinstance(card, dict) or card.get("version") != manifest["version"] or not card.get("name")
                or not isinstance(card.get("skills"), list) or not 1 <= len(card["skills"]) <= 16
                or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) or not 1 <= len(s["id"]) <= 80 for s in card["skills"])
                or len(str(card)) > 16000):
            raise ValueError("INVALID_PACKAGE_CARD")
        royalty = manifest["royalty"]
        if (not isinstance(royalty, dict) or set(royalty) != {"creator_did", "share_bps"}
                or royalty["creator_did"] != manifest["author_did"] or isinstance(royalty["share_bps"], bool)
                or not isinstance(royalty["share_bps"], int) or not 0 <= royalty["share_bps"] <= 10000):
            raise ValueError("INVALID_CREATOR_AGREEMENT")
        if not isinstance(module_base64, str) or len(module_base64) > 350000:
            raise ValueError("PACKAGE_MODULE_TOO_LARGE")
        try:
            module = base64.b64decode(module_base64, validate=True)
        except Exception as exc:
            raise ValueError("INVALID_PACKAGE_MODULE_ENCODING") from exc
        if len(module) > 256 * 1024 or not module.startswith(b"\x00asm") or hashlib.sha256(module).hexdigest() != manifest["module_sha256"]:
            raise ValueError("PACKAGE_MODULE_DIGEST_MISMATCH")
        sid = "pkg_" + digest([manifest["author_did"], manifest["package_id"]])[:24]
        return {"service_id": sid, "manifest": manifest, "module_base64": module_base64,
                "preview_digest": digest(manifest), "publisher_verified": True, "execution_granted": False,
                "creator_settlement": "UNAVAILABLE_UNTIL_REAL_PAYMENT", "platform_commission_minor": 0}

    def install(self, preview, *, granted_permissions, accepted_digest, command_id):
        if granted_permissions != PERMISSIONS or accepted_digest != preview["preview_digest"]:
            raise ValueError("EXPLICIT_PACKAGE_GRANT_REQUIRED")
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("必须提供 Idempotency-Key")
        with self.store.tx():
            previous = self.store.get("package_commands", command_id)
            if previous and previous != preview["preview_digest"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            sid = preview["service_id"]
            row = {**preview, "execution_granted": True, "active": True}
            self.store.put("agent_package_versions", preview["preview_digest"], row)
            self.store.put("agent_packages", sid, row)
            self.store.put("package_commands", command_id, preview["preview_digest"])
            return row

    def rollback(self, service_id, version_digest):
        with self.store.tx():
            version = self.store.get("agent_package_versions", version_digest)
            if not version or version["service_id"] != service_id or not version["execution_granted"]:
                raise ValueError("PACKAGE_VERSION_NOT_GRANTED")
            self.store.put("agent_packages", service_id, version)
            return version

    def list(self):
        return [{k: v for k, v in r.items() if k != "module_base64"} for r in self.store.items("agent_packages").values()]
