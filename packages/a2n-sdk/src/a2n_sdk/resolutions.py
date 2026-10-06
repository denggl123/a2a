"""Bilateral signed negotiation, using the same local disputes and trade facts."""
from __future__ import annotations

import secrets
import time

from .experience import signed, unsigned
from .trade_facts import digest, FACT_NS

VERSION = "a2n-resolution-message/1"
FIELDS = {"v", "message_id", "dispute_id", "trade_uid", "author_did", "target_did", "kind", "parent_id", "body", "created_at", "proof"}


class ResolutionBook:
    def __init__(self, store, disputes, facts, *, node_did, signer, verifier, now=None):
        self.store, self.disputes, self.facts = store, disputes, facts
        self.node_did, self.signer, self.verifier = node_did, signer, verifier
        self.now = now or time.time

    def open(self, scope, task_id, side, reason, details=None):
        with self.store.tx():
            dispute = self.disputes.open(scope, task_id, side, reason, details)
            facts = self.facts.for_call(scope, task_id)
            if facts:
                dispute = {**dispute, "trade_uid": facts["trade_uid"]}
                self.store.put("disputes", dispute["id"], dispute)
                self.store.put(FACT_NS, facts["trade_uid"], {**facts, "dispute": "OPEN"})
            return dispute

    def _parties(self, uid):
        facts = self.facts.get(uid)
        if not facts or not facts.get("relation_verified"):
            raise ValueError("UNVERIFIED_LOCAL_TRADE")
        parties = {facts["buyer_did"], facts["provider_did"]}
        if len(parties) != 2 or self.node_did not in parties or "" in parties:
            raise ValueError("UNVERIFIED_TRADE_PARTIES")
        return facts, next(p for p in parties if p != self.node_did)

    def _validate_body(self, kind, body):
        if not isinstance(body, dict):
            raise ValueError("INVALID_RESOLUTION_BODY")
        if kind == "REPLY":
            if set(body) != {"text"} or not isinstance(body["text"], str) or not 1 <= len(body["text"]) <= 500:
                raise ValueError("INVALID_REPLY")
        elif kind == "PROPOSAL":
            if set(body) != {"action", "text", "amount_minor", "currency"} or body["action"] not in {"REFUND", "REWORK", "CLOSE"}:
                raise ValueError("INVALID_PROPOSAL")
            from .payments import minor
            minor(body["amount_minor"])
            if not isinstance(body["text"], str) or len(body["text"]) > 500 or not isinstance(body["currency"], str) or len(body["currency"]) > 12:
                raise ValueError("INVALID_PROPOSAL")
            if body["action"] != "REFUND" and (body["amount_minor"] or body["currency"]):
                raise ValueError("INVALID_NONMONETARY_PROPOSAL")
            if body["action"] == "REFUND" and (not body["amount_minor"] or not body["currency"]):
                raise ValueError("INVALID_REFUND_PROPOSAL")
        elif kind in {"ACCEPT", "REJECT"}:
            if set(body) != {"proposal_hash"} or not isinstance(body["proposal_hash"], str) or len(body["proposal_hash"]) != 64:
                raise ValueError("INVALID_ACCEPTANCE")
        else:
            raise ValueError("UNSUPPORTED_RESOLUTION_KIND")

    def post(self, dispute_id, *, kind="REPLY", body, parent_id="", command_id):
        dispute = self.disputes.get(dispute_id)
        if not dispute or not dispute.get("trade_uid"):
            raise ValueError("DISPUTE_TRADE_NOT_FOUND")
        facts, other = self._parties(dispute["trade_uid"])
        self._validate_body(kind, body)
        fingerprint = digest([dispute_id, kind, body, parent_id])
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("必须提供 Idempotency-Key")
        with self.store.tx():
            old = self.store.get("resolution_commands", command_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.store.get("resolution_messages", old["message_id"])
            message_id = "rm_" + secrets.token_hex(16)
            core = {"v": VERSION, "message_id": message_id, "dispute_id": dispute_id,
                "trade_uid": facts["trade_uid"], "author_did": self.node_did, "target_did": other,
                "kind": kind, "parent_id": parent_id, "body": body, "created_at": self.now()}
            record = signed(core, self.signer)
            self._save(record, incoming=False)
            self.store.put("resolution_commands", command_id, {"fingerprint": fingerprint, "message_id": message_id})
            self.store.put("resolution_outbox", message_id, {"message_id": message_id, "trade_uid": facts["trade_uid"],
                "target_did": other, "endpoint": facts.get("counterparty_endpoint", ""),
                "state": "READY", "attempts": 0, "next_attempt": self.now()})
            return record

    def receive(self, record):
        if not isinstance(record, dict) or set(record) != FIELDS or record["v"] != VERSION or not self.verifier(record.get("proof"), unsigned(record)):
            raise ValueError("UNVERIFIED_RESOLUTION_MESSAGE")
        if any(not isinstance(record[k], str) or not 1 <= len(record[k]) <= 256 for k in
               ("message_id", "dispute_id", "trade_uid", "author_did", "target_did")):
            raise ValueError("INVALID_RESOLUTION_IDENTITY")
        _, other = self._parties(record["trade_uid"])
        if record["author_did"] != other or record["target_did"] != self.node_did:
            raise ValueError("RESOLUTION_PARTY_MISMATCH")
        self._validate_body(record["kind"], record["body"])
        if (not isinstance(record["message_id"], str) or not 1 <= len(record["message_id"]) <= 128
                or not isinstance(record["dispute_id"], str) or not 1 <= len(record["dispute_id"]) <= 128
                or not isinstance(record["parent_id"], str) or len(record["parent_id"]) > 128
                or isinstance(record["created_at"], bool) or not isinstance(record["created_at"], (float, int))
                or not 0 <= record["created_at"] <= self.now() + 30):
            raise ValueError("INVALID_RESOLUTION_FIELDS")
        with self.store.tx():
            return self._save(record, incoming=True)

    def _save(self, record, *, incoming):
        dispute = self.disputes.get(record["dispute_id"])
        if dispute and dispute.get("trade_uid") != record["trade_uid"]:
            raise ValueError("DISPUTE_ID_CONFLICT")
        existing = self.store.get("resolution_messages", record["message_id"])
        if existing:
            if existing != record:
                raise ValueError("RESOLUTION_MESSAGE_CONFLICT")
            return existing
        messages = self.messages(record["trade_uid"])
        if len(messages) >= 128:
            raise ValueError("RESOLUTION_CAPACITY_LIMIT")
        parent = self.store.get("resolution_messages", record["parent_id"]) if record["parent_id"] else None
        if record["parent_id"] and (not parent or parent["trade_uid"] != record["trade_uid"]):
            raise ValueError("RESOLUTION_PARENT_MISSING")
        if record["kind"] in {"ACCEPT", "REJECT"} and (not parent or parent["kind"] != "PROPOSAL" or record["body"]["proposal_hash"] != digest(parent)):
            raise ValueError("ACCEPTANCE_PROPOSAL_MISMATCH")
        if record["kind"] in {"ACCEPT", "REJECT"}:
            decision = next((m for m in messages if m["kind"] in {"ACCEPT", "REJECT"}
                and m["parent_id"] == record["parent_id"] and m["author_did"] == record["author_did"]), None)
            if decision and decision["kind"] != record["kind"]:
                raise ValueError("DECISION_ALREADY_FIXED: 修改方案须提出新方案")
        self.store.put("resolution_messages", record["message_id"], record)
        if incoming and not self.disputes.get(record["dispute_id"]):
            facts, _ = self._parties(record["trade_uid"])
            rec = {"id": record["dispute_id"], "trade_uid": record["trade_uid"], "scope": facts["scope"],
                "task_id": facts["task_id"], "side": "counterparty", "reason": "对方发来协商消息",
                "state": "OPEN", "created_at": str(record["created_at"]), "kind": "bilateral_negotiation"}
            self.store.put("disputes", rec["id"], rec)
        facts = self.facts.get(record["trade_uid"])
        if facts.get("dispute") != "CLOSED_BILATERAL" or record["kind"] == "PROPOSAL":
            self.store.put(FACT_NS, record["trade_uid"], {**facts, "dispute": "NEGOTIATING"})
        if record["kind"] in {"ACCEPT", "REJECT"}:
            acceptances = [r for r in [*messages, record] if r["kind"] == "ACCEPT" and r["parent_id"] == record["parent_id"]]
            authors = {r["author_did"] for r in acceptances}
            rejections = [r for r in [*messages, record] if r["kind"] == "REJECT" and r["parent_id"] == record["parent_id"]]
            old_agreement = self.store.get("resolution_agreements", record["parent_id"]) or {}
            state = "DECLINED" if rejections else "BOTH_ACCEPTED" if len(authors) == 2 else "ONE_ACCEPTED"
            execution = old_agreement.get("execution", "NOT_EXECUTED")
            if state == "BOTH_ACCEPTED" and parent["body"]["action"] == "CLOSE":
                execution = "CLOSED_BILATERAL"
                for did, dispute in self.store.items("disputes").items():
                    if dispute.get("trade_uid") == record["trade_uid"] and dispute.get("state") == "OPEN":
                        self.store.put("disputes", did, {**dispute, "state": "CLOSED_BILATERAL",
                            "closed_at": self.now(), "agreement_id": parent["message_id"]})
                self.store.put(FACT_NS, record["trade_uid"], {**facts, "dispute": "CLOSED_BILATERAL"})
            self.store.put("resolution_agreements", record["parent_id"], {**old_agreement, "proposal": parent,
                "acceptances": acceptances, "rejections": rejections, "state": state,
                "execution": execution, "notice": "关闭按双方签名执行；补做需买方明确启动新任务，退款需真实通道。"})
        return record

    def accept(self, dispute_id, proposal_id, *, command_id):
        return self.decide(dispute_id, proposal_id, accepted=True, command_id=command_id)

    def decide(self, dispute_id, proposal_id, *, accepted, command_id):
        proposal = self.store.get("resolution_messages", proposal_id)
        if not proposal or proposal["kind"] != "PROPOSAL":
            raise ValueError("PROPOSAL_NOT_FOUND")
        dispute = self.disputes.get(dispute_id)
        if not dispute or dispute.get("trade_uid") != proposal["trade_uid"]:
            raise ValueError("ACCEPTANCE_PROPOSAL_MISMATCH")
        kind = "ACCEPT" if accepted is True else "REJECT" if accepted is False else None
        if not kind:
            raise ValueError("INVALID_DECISION")
        previous = next((r for r in self.messages(proposal["trade_uid"]) if r["kind"] in {"ACCEPT", "REJECT"}
            and r["parent_id"] == proposal_id and r["author_did"] == self.node_did), None)
        if previous:
            if previous["kind"] != kind:
                raise ValueError("DECISION_ALREADY_FIXED: 修改方案须提出新方案")
            return previous
        return self.post(dispute_id, kind=kind, parent_id=proposal_id,
                         body={"proposal_hash": digest(proposal)}, command_id=command_id)

    def retry(self, message_id):
        with self.store.tx():
            row = self.store.get("resolution_outbox", message_id)
            if not row:
                raise ValueError("OUTBOX_MESSAGE_NOT_FOUND")
            if row["state"] != "DELIVERED":
                row = {**row, "state": "READY", "attempts": 0, "next_attempt": self.now()}
                self.store.put("resolution_outbox", message_id, row)
            return row

    def rework_agreement(self, proposal_id):
        agreement = self.store.get("resolution_agreements", proposal_id)
        if not agreement or agreement["state"] != "BOTH_ACCEPTED" or agreement["proposal"]["body"]["action"] != "REWORK":
            raise ValueError("BILATERAL_REWORK_AGREEMENT_REQUIRED")
        facts, _ = self._parties(agreement["proposal"]["trade_uid"])
        return agreement, facts

    def note_rework(self, proposal_id, facts):
        agreement, _ = self.rework_agreement(proposal_id)
        self.store.put("resolution_agreements", proposal_id, {**agreement,
            "execution": "REWORK_" + facts["execution"], "rework_trade_uid": facts["trade_uid"]})

    def messages(self, trade_uid):
        return sorted((r for r in self.store.items("resolution_messages").values() if r["trade_uid"] == trade_uid),
                      key=lambda r: (r["created_at"], r["message_id"]))
