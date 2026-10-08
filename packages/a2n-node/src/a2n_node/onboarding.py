"""Owner-authorized first connection and observable product readiness."""
from __future__ import annotations

import base64

from .relay_crypto import private_from_seed
from .relay_provider import RelayProvider


class OnboardingService:
    def __init__(self, daemon):
        self.daemon = daemon

    def status(self):
        d = self.daemon
        return {"node_did": d.identity.did, "neighbors": len(d.public_coordination.known_records()),
            "public_nodes": list(d.public_directories.bases),
            "public_discovery": True, "public_samples": True,
            "outbound_discovery": bool(d.coord_mailbox.registered),
            "outbound_settlement": bool(d.settlement_mailbox.lease),
            "outbound_agent": bool(d.relay_provider and d.relay_provider.registered),
            "relay_configured": bool(d.relay_provider),
            "supplies": len(d.runtime.bindings.list()), "imported": len(d.runtime.imported),
            "reputation_mode": d.policies.get()["values"]["mode"]}

    def connect(self, body):
        d = self.daemon
        origin, added = d.public_directories.add(body.get("address"))
        try:
            session, _ = d.coord_network.handshake({"endpoint": origin + "/public/v1/coord"}, 16384, 3)
            # Optional service claims come from the verified HELLO envelope.
            relay_supported = session.get("services", {}).get("task_relay") is True
        except Exception:
            if added:
                d.public_directories.remove(origin)
            raise
        saved = d.management._saved_public_nodes()
        if origin not in saved:
            saved.append(origin)
        with d.store.tx():
            d.store.put("node_settings", "public_nodes", saved)
            d.store.put("node_settings", "outbound_coordination_node", origin)
            if relay_supported:
                d.store.put("node_settings", "relay_node", origin)
        if relay_supported and (not d.relay_provider or d.relay_provider.relay_node != origin):
            if d.relay_provider:
                d.relay_provider.stop()
            seed = base64.b64decode(d.store.get("identity", "relay_seed"))
            d.relay_provider = RelayProvider(d.identity, d.runtime, private_from_seed(seed), origin)
            d.management.relay_provider = d.relay_provider
            d.relay_provider.start()
        return {"connected": True, "node_did": session["node_did"], "address": origin,
            "outbound_configured": True, "agent_relay_configured": bool(relay_supported),
            "notice": "节点身份已核验，发现和结算出站通道正在建立。" +
                ("对方提供加密 Agent 中继。" if relay_supported else "对方未提供任务中继；对外供给仍需可达入口或另选中继节点。")}
