"""Bounded public coordination. No Agent execution or business ledger access."""
from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import threading
import time
import uuid

from a2n_sdk.coordination import CandidateKey, query_fingerprint
from a2n_sdk.projection import canonical_json

from .card import card_did, card_hash, card_skills, verify_card
from .coord_identity import (capability_declaration, coord_envelope, node_record,
                             referral, verify_coord_envelope, verify_node_record)


PATH_OPERATIONS = {"hello": "HELLO", "find": "FIND", "card": "GET_CARD",
                   "routes": "RESOLVE_ROUTES", "probe": "PROBE", "forward": "FORWARD_COORD"}
MAX_COORD_BYTES = 262144


class PublicCoordination:
    def __init__(self, identity, store, *, endpoint, cards, services):
        self.identity, self.store = identity, store
        self.endpoint, self.cards, self.services = endpoint, cards, services
        self.network = None
        self._lock = threading.RLock()
        self._recent = OrderedDict()
        self._rates = OrderedDict()
        self._snapshots = OrderedDict()
        self._mailboxes = {}
        self.mailbox_route = None

    def record(self, *, mailbox=None):
        mailbox = mailbox or self.mailbox_route
        base = self.endpoint().rstrip("/")
        if mailbox:
            routes = [{"channel_type": "coord_mailbox", "endpoint": mailbox["endpoint"],
                       "relay_did": mailbox["relay_did"]}]
        else:
            routes = [{"channel_type": "local_http" if base.startswith("http:") else "direct_https",
                       "endpoint": base + "/public/v1/coord"}] if base else []
        return node_record(self.identity, coord_routes=routes, ttl=120)

    def capabilities(self):
        record = self.record()
        return capability_declaration(self.identity, coord_routes=record["coord_routes"],
                                      limits={"max_request_bytes": MAX_COORD_BYTES, "max_response_bytes": MAX_COORD_BYTES,
                                              "rate_per_second": 2, "burst": 120,
                                              "local_rate_per_second": 2, "local_burst": 120,
                                              "requests_per_minute": 120},
                                      **{k: bool(self.services().get(k)) for k in ("witness", "task_relay", "blob_cache")})

    def remember(self, record):
        if not verify_node_record(record, now=time.time()):
            raise ValueError("节点记录签名无效或已过期")
        if record["node_did"] == self.identity.did:
            return
        with self._lock:
            rows = self.store.items("coord_nodes")
            if len(rows) >= 256 and record["node_did"] not in rows:
                oldest = min(rows, key=lambda k: rows[k].get("expires_at", 0))
                self.store.delete("coord_nodes", oldest)
            self.store.put("coord_nodes", record["node_did"], record)

    def known_records(self):
        return [r for r in self.store.items("coord_nodes").values()
                if verify_node_record(r, now=time.time())]

    def cache_card(self, card):
        ok, _ = verify_card(card, require_endpoint=True)
        if not ok:
            raise ValueError("商品卡签名无效")
        rows = self.store.items("coord_cards")
        digest = card_hash(card)
        if len(rows) >= 512 and digest not in rows:
            oldest = min(rows, key=lambda k: rows[k]["at"])
            self.store.delete("coord_cards", oldest)
        self.store.put("coord_cards", digest, {"card": card, "at": time.time()})

    def all_cards(self):
        own = self.cards()
        cached = [r["card"] for r in self.store.items("coord_cards").values()
                  if time.time() - r["at"] < 600]
        out = {}
        for card in own + cached:
            ok, _ = verify_card(card, require_endpoint=True)
            if ok and ((card.get("x-a2n") or {}).get("projection") or {}).get("role") != "consumer":
                out[card_hash(card)] = card
        return list(out.values())[:512]

    def route(self, card):
        key = CandidateKey.of_card(card_did(card), card)
        digest = card_hash(card)
        relay = (card.get("x-a2n") or {}).get("relay") or {}
        return {"route_id": digest, "key": key.to_dict(), "card": card,
                "route_card_hash": digest, "channel_type": "sealed_relay_a2a" if relay else "direct_a2a",
                "relay_did": str(relay.get("node_did") or ""), "expires_at": int(time.time()) + 120}

    def _limit(self, sender, size):
        if size > MAX_COORD_BYTES:
            raise ValueError("协调请求过大")
        now = time.monotonic()
        with self._lock:
            timestamps = self._rates.setdefault(sender, deque())
            while timestamps and now - timestamps[0] > 60:
                timestamps.popleft()
            if len(timestamps) >= 120:
                raise TimeoutError("RATE_LIMITED: 协调请求频率超限")
            timestamps.append(now)
            self._rates.move_to_end(sender)
            while len(self._rates) > 512:
                self._rates.popitem(last=False)

    def handle(self, operation, envelope):
        if operation not in PATH_OPERATIONS.values():
            raise ValueError("未知协调操作")
        recipient = None if operation == "HELLO" and not envelope.get("recipient_did") else self.identity.did
        if not verify_coord_envelope(envelope, now=time.time(), recipient_did=recipient):
            raise PermissionError("协调请求身份或签名无效")
        if envelope["type"] != operation:
            raise ValueError("协调路径与操作不一致")
        sender, request_id = envelope["sender_did"], str(envelope.get("request_id") or "")
        if not request_id or len(request_id) > 200:
            raise ValueError("request_id 无效")
        digest = hashlib.sha256(canonical_json(envelope).encode()).hexdigest()
        key = (sender, request_id)
        with self._lock:
            previous = self._recent.get(key)
            if previous and previous["expires"] > time.time():
                if previous["digest"] != digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return previous["response"]
        self._limit(sender, len(canonical_json(envelope).encode()))
        try:
            body = self._dispatch(operation, envelope["body"], sender)
            result_type = operation + "_RESULT"
        except (ValueError, KeyError, TimeoutError) as exc:
            from a2n_sdk.coordination import ERROR_HTTP
            code = str(exc).split(":", 1)[0]
            body = {"code": "DEADLINE_EXCEEDED" if isinstance(exc, TimeoutError) else
                           (code if code in ERROR_HTTP else "INVALID_REQUEST"),
                    "error": str(exc)}
            result_type = "ERROR"
        response = coord_envelope(self.identity, result_type, body, recipient_did=sender,
                                  request_id="rsp_" + uuid.uuid4().hex, in_reply_to=request_id, ttl=10)
        if len(canonical_json(response).encode()) > MAX_COORD_BYTES:
            response = coord_envelope(self.identity, "ERROR", {"code": "RESPONSE_TOO_LARGE", "error": "响应超过节点上限"},
                                      recipient_did=sender, request_id="rsp_" + uuid.uuid4().hex,
                                      in_reply_to=request_id, ttl=10)
        with self._lock:
            self._recent[key] = {"digest": digest, "response": response,
                                 "expires": min(envelope["expires_at"], time.time() + 10)}
            while len(self._recent) > 512:
                self._recent.popitem(last=False)
        return response

    def _dispatch(self, operation, body, sender):
        if operation == "HELLO":
            record = body.get("node_record") or {}
            if record.get("node_did") != sender or "a2n-coord/1" not in body.get("versions", []):
                raise ValueError("节点身份或协议版本不匹配")
            self.remember(record)
            return {"selected_version": "a2n-coord/1", "node_record": self.record(),
                    "nonce": body.get("nonce"), "limits": {"request_bytes": MAX_COORD_BYTES,
                    "response_bytes": MAX_COORD_BYTES, "requests_per_minute": 120},
                    "services": self.services()}
        if operation == "PROBE":
            return {"nonce": body.get("nonce"), "node_did": self.identity.did, "served_at": int(time.time())}
        if operation == "FIND":
            skill = str(body.get("skill") or "").strip()
            if not skill or len(skill) > 96:
                raise ValueError("能力标识无效")
            size = body.get("page_size", 10)
            if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 10:
                raise ValueError("page_size 必须是 1–10 的整数")
            fingerprint = query_fingerprint(skill, body.get("coarse_requirements") or {}, size)
            if body.get("query_fingerprint") != fingerprint:
                raise ValueError("query_fingerprint 不一致")
            cap = min(MAX_COORD_BYTES, int(body.get("max_response_bytes") or 65536))
            with self._lock:
                cursor = body.get("cursor")
                if cursor:
                    snapshot_id, offset = cursor.rsplit(":", 1)
                    snapshot = self._snapshots.get(snapshot_id)
                    if not snapshot or snapshot["expires"] <= time.time():
                        raise ValueError("CURSOR_EXPIRED")
                    if (snapshot["sender"], snapshot["fingerprint"]) != (sender, fingerprint):
                        raise ValueError("游标不属于本次查询")
                    offset = int(offset)
                    if not 0 <= offset <= len(snapshot["objects"]):
                        raise ValueError("游标偏移无效")
                else:
                    snapshot_id, offset = uuid.uuid4().hex, 0
                    offers, refs = [], []
                    for card in self.all_cards():
                        if skill not in card_skills(card):
                            continue
                        key = CandidateKey.of_card(card_did(card), card)
                        offers.append(("offers", {**key.to_dict(), "skills": card_skills(card),
                                        "card_ref": {"source_did": self.identity.did}, "card_hash": card_hash(card),
                                        "observed_at": int(time.time())}))
                    for record in self.known_records()[:64]:
                        if record["node_did"] != sender:
                            refs.append(("referrals", referral(self.identity, record)))
                    # Every page can expose another source; a large catalogue cannot hide referrals.
                    objects = []
                    for index in range(max(len(offers), len(refs))):
                        if index < len(offers):
                            objects.append(offers[index])
                        if index < len(refs):
                            objects.append(refs[index])
                    snapshot = {"sender": sender, "fingerprint": fingerprint,
                                "objects": objects, "expires": time.time() + 60}
                    self._snapshots[snapshot_id] = snapshot
                    while len(self._snapshots) > 64:
                        self._snapshots.popitem(last=False)
                result = {"query_fingerprint": fingerprint, "snapshot_id": snapshot_id,
                          "snapshot_revision": 1, "offers": [], "referrals": [],
                          "next_cursor": None, "truncated": False, "limit_reason": None}
                sent = 0
                while offset < len(snapshot["objects"]) and sent < size:
                    kind, obj = snapshot["objects"][offset]
                    if kind == "referrals" and len(result[kind]) >= 8:
                        break
                    result[kind].append(obj)
                    if len(canonical_json(result).encode()) + 1024 > cap:
                        result[kind].pop()
                        break
                    offset += 1
                    sent += 1
                if offset < len(snapshot["objects"]):
                    result.update(next_cursor=f"{snapshot_id}:{offset}", truncated=True,
                                  limit_reason="PAGE_OR_BYTE_LIMIT")
                if not sent and result["next_cursor"]:
                    raise ValueError("RESPONSE_TOO_LARGE: 单条线索超过响应额度")
                return result
        if operation in {"GET_CARD", "RESOLVE_ROUTES"}:
            key = CandidateKey.from_dict(body.get("key") or {})
            cards = [c for c in self.all_cards() if CandidateKey.of_card(card_did(c), c) == key]
            if operation == "GET_CARD":
                wanted = str(body.get("card_hash") or "")
                card = next((c for c in cards if card_hash(c) == wanted), None)
                if card is None:
                    raise ValueError("NOT_FOUND: 没有这个版本的商品卡")
                return {"key": key.to_dict(), "card": card, "card_hash": wanted, "served_at": int(time.time())}
            limit = max(1, min(int(body.get("limit") or 10), 20))
            return {"key": key.to_dict(), "routes": [self.route(c) for c in cards[:limit]],
                    "observed_at": int(time.time()), "truncated": len(cards) > limit}
        if operation == "FORWARD_COORD":
            inner = body.get("inner_request") or {}
            target = str(body.get("target_did") or "")
            path = body.get("path") or []
            if (path != [self.identity.did, target] or target == self.identity.did
                    or body.get("origin_did") != sender
                    or body.get("parent_request_id") != inner.get("request_id")
                    or body.get("origin_did") != inner.get("sender_did")
                    or body.get("inner_request_hash") != hashlib.sha256(canonical_json(inner).encode()).hexdigest()
                    or int(body.get("deadline") or 0) <= time.time()
                    or int(body.get("max_operations") or 0) < 1
                    or not verify_coord_envelope(inner, now=time.time(), recipient_did=target)
                    or inner.get("type") not in set(PATH_OPERATIONS.values()) - {"FORWARD_COORD"}):
                raise ValueError("转送约定无效；只支持明确指定的一个邻居")
            response = self.mailbox_submit(target, inner, min(3, body["deadline"] - time.time()))
            if response is None:
                record = self.store.get("coord_nodes", target)
                if not record or not verify_node_record(record, now=time.time()) or not self.network:
                    raise ValueError("UNREACHABLE: 没有已认证的目标邻居")
                session = self.network.connect(record, time.monotonic() + min(3, body["deadline"] - time.time()))
                response = self.network.exchange(session, inner, min(int(body.get("max_response_bytes", 65536)), MAX_COORD_BYTES))["envelope"]
            return {"target_response": response, "forward_status": "DELIVERED", "operations_used": 1}
        raise ValueError("不支持的协调操作")

    def mailbox(self, action, envelope):
        if not verify_coord_envelope(envelope, now=time.time(), recipient_did=self.identity.did):
            raise PermissionError("协调邮箱身份无效")
        if envelope.get("type") != ("HELLO" if action == "register" else "PROBE"):
            raise ValueError("协调邮箱消息类型与操作不一致")
        self._limit(envelope["sender_did"], len(canonical_json(envelope).encode()))
        sender, body = envelope["sender_did"], envelope["body"]
        with self._lock:
            now = time.time()
            for did in list(self._mailboxes):
                if self._mailboxes[did]["expires"] <= now:
                    self._mailboxes.pop(did)
            if action == "register":
                record = body.get("node_record") or {}
                if record.get("node_did") != sender or not verify_node_record(record, now=now):
                    raise ValueError("协调邮箱节点记录无效")
                expected = self.endpoint().rstrip("/") + "/public/v1/coord/mailbox"
                if not any(r.get("channel_type") == "coord_mailbox" and r.get("endpoint") == expected
                           and r.get("relay_did") == self.identity.did for r in record.get("coord_routes", [])):
                    raise ValueError("邮箱通道没有绑定本节点")
                if sender not in self._mailboxes and len(self._mailboxes) >= 128:
                    raise TimeoutError("协调邮箱名额已满")
                box = self._mailboxes.setdefault(sender, {"queue": deque(), "pending": {}, "expires": now + 90})
                box["expires"] = now + 90
                self.remember(record)
                result = {"lease_expires_at": int(box["expires"])}
            elif sender not in self._mailboxes:
                raise ValueError("协调邮箱租约已过期")
            elif action == "poll":
                box = self._mailboxes[sender]
                result = {"requests": [box["queue"].popleft()] if box["queue"] else []}
            elif action == "reply":
                response = body.get("response") or {}
                pending = self._mailboxes[sender]["pending"].get(response.get("in_reply_to"))
                if (not pending or not verify_coord_envelope(response, now=now, recipient_did=pending["request"]["sender_did"])
                        or response.get("sender_did") != sender):
                    raise ValueError("邮箱应答与原请求不一致")
                pending["response"] = response
                pending["event"].set()
                result = {"ok": True}
            else:
                raise ValueError("未知协调邮箱操作")
        return coord_envelope(self.identity, "HELLO_RESULT" if action == "register" else "PROBE_RESULT",
                              result, recipient_did=sender, request_id="rsp_" + uuid.uuid4().hex,
                              in_reply_to=envelope["request_id"], ttl=10)

    def mailbox_submit(self, target, request, timeout):
        with self._lock:
            box = self._mailboxes.get(target)
            if not box or box["expires"] <= time.time():
                return None
            if len(box["pending"]) >= 8:
                raise TimeoutError("目标协调邮箱繁忙")
            pending = {"request": request, "event": threading.Event(), "response": None}
            rid = request["request_id"]
            box["pending"][rid] = pending
            box["queue"].append(request)
        try:
            if not pending["event"].wait(max(.001, timeout)):
                raise TimeoutError("协调邮箱目标未及时应答")
            return pending["response"]
        finally:
            with self._lock:
                box["pending"].pop(rid, None)
                box["queue"] = deque(r for r in box["queue"] if r["request_id"] != rid)
