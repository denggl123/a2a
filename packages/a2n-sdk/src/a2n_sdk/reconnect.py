"""Optional bounded sampling grants after a locally observed public network gap."""
from __future__ import annotations

import secrets
import time

from .trade_facts import axes


class ReconnectBook:
    def __init__(self, store, trials, *, node_did, now=None):
        self.store, self.trials, self.node_did = store, trials, node_did
        self.now = now or time.time
        trials.reconnect = self

    def policy(self):
        return self.store.get("reconnect_policy", "owner") or {"revision": 0, "enabled": False,
            "grant_size": 5, "interval_seconds": 86400, "lifetime_cap": 30}

    def configure(self, *, enabled, expected_revision):
        if not isinstance(enabled, bool) or isinstance(expected_revision, bool):
            raise ValueError("INVALID_RECONNECT_POLICY")
        with self.store.tx():
            old = self.policy()
            if old["revision"] != expected_revision:
                raise ValueError("REV_CONFLICT")
            row = {**old, "enabled": enabled, "revision": expected_revision + 1}
            self.store.put("reconnect_policy", "owner", row)
            self.store.put("reconnect_policy_versions", str(row["revision"]), row)
            return row

    def note_verified_connection(self, peer_did, service_ids):
        if not peer_did or peer_did == self.node_did:
            return []
        now = self.now()
        with self.store.tx():
            old = self.store.get("reconnect_observations", "public") or {}
            last = old.get("last_verified_at")
            self.store.put("reconnect_observations", "public", {"last_verified_at": max(now, last or 0),
                "peer_did": peer_did, "basis": "LOCALLY_OBSERVED_VERIFIED_PROTOCOL_CONTACT"})
            policy = self.policy()
            if not policy["enabled"] or last is None or now - last < policy["interval_seconds"]:
                return []
            granted = []
            for sid in dict.fromkeys(service_ids):
                if self.trials.trial(sid)["completed"] < self.trials.cap:
                    continue
                rows = [g for g in self.store.items("reconnect_grants").values() if g["service_id"] == sid]
                # Disabling/re-enabling, version changes and restarts never stack grants.
                if any(g["completed"] < g["size"] for g in rows):
                    continue
                if rows and now - max(g["granted_at"] for g in rows) < policy["interval_seconds"]:
                    continue
                initial = sum(1 for r in self.store.items("trial_calls").values()
                    if r.get("service_id") == sid and r.get("free_reason", "FREE_INITIAL" if r.get("free") else "") == "FREE_INITIAL")
                used = max(min(self.trials.trial(sid)["completed"], self.trials.cap), initial) + sum(g["completed"] for g in rows)
                size = min(policy["grant_size"], max(0, policy["lifetime_cap"] - used))
                if not size:
                    continue
                row = {"grant_id": "rg_" + secrets.token_hex(16), "service_id": sid, "size": size,
                    "completed": 0, "reserved": 0, "granted_at": now, "gap_started_at": last,
                    "policy_revision": policy["revision"], "reason": "VERIFIED_CONTACT_AFTER_OBSERVATION_GAP",
                    "notice": "本机观察间隔，不证明互联网物理断线或对方质量。"}
                self.store.put("reconnect_grants", row["grant_id"], row)
                granted.append(row)
            return granted

    def available(self, service_id):
        if not self.policy()["enabled"]:
            return None
        return next((g for g in self.store.items("reconnect_grants").values() if g["service_id"] == service_id
                     and g["completed"] + g["reserved"] < g["size"]), None)

    def reserve(self, service_id):
        grant = self.available(service_id)
        if not grant:
            raise ValueError("RECONNECT_QUOTA_UNAVAILABLE")
        self.store.put("reconnect_grants", grant["grant_id"], {**grant, "reserved": grant["reserved"] + 1})
        return grant["grant_id"]

    def finish(self, admission, outcome):
        gid = admission.get("reconnect_grant_id")
        execution = axes(outcome)["execution"]
        if not gid or not admission.get("active") or execution not in {"DELIVERED", "FAILED", "CANCELED"}:
            return
        grant = self.store.get("reconnect_grants", gid)
        if grant:
            self.store.put("reconnect_grants", gid, {**grant, "reserved": max(0, grant["reserved"] - 1),
                "completed": grant["completed"] + (execution == "DELIVERED")})

    def summary(self, service_id):
        rows = [g for g in self.store.items("reconnect_grants").values() if g["service_id"] == service_id]
        return {"enabled": self.policy()["enabled"], "grants": rows,
            "remaining": sum(g["size"] - g["completed"] - g["reserved"] for g in rows) if self.policy()["enabled"] else 0,
            "completed": sum(g["completed"] for g in rows), "reserved": sum(g["reserved"] for g in rows)}
