"""Authenticated coordination network adapter with pinned DNS and bounded I/O."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
import uuid
from urllib.parse import urlsplit

from a2n_sdk.coordination import CandidateKey, NodeRecord, RouteObservation, query_fingerprint
from a2n_sdk.projection import canonical_json
from a2n_sdk.executor import DaemonExecutor

from .card import card_did, card_hash, verify_card
from .coord_identity import (coord_envelope, loads_strict, verify_coord_envelope,
                             verify_node_record, verify_referral)
from .coord_service import MAX_COORD_BYTES, PATH_OPERATIONS


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, address, **kwargs):
        self.address = address
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, address, **kwargs):
        self.address = address
        super().__init__(host, context=ssl.create_default_context(), **kwargs)

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class CoordinationNetwork:
    def __init__(self, identity, public, *, roots, legacy=None, legacy_cost=2,
                 legacy_available=None, allowed_networks=(), on_connection=None):
        self.identity, self.public = identity, public
        self.root_provider, self.legacy = roots, legacy
        self.legacy_cost, self.legacy_available = legacy_cost, legacy_available
        self.allowed_networks = tuple(ipaddress.ip_network(n) for n in allowed_networks)
        self.on_connection = on_connection
        self._dns = DaemonExecutor(2, thread_name_prefix="a2n-dns")
        self._dns_slots = threading.BoundedSemaphore(4)

    def close(self):
        self._dns.shutdown(wait_timeout=.1)

    def _addresses(self, host, port, deadline):
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        if not self._dns_slots.acquire(blocking=False):
            raise TimeoutError("DNS_CAPACITY_LIMIT")
        try:
            future = self._dns.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except BaseException:
            self._dns_slots.release()
            raise
        future.add_done_callback(lambda _: self._dns_slots.release())
        try:
            values = future.result(timeout=max(.001, deadline - time.monotonic()))
            return list(dict.fromkeys(r[4][0] for r in values))
        except TimeoutError:
            future.cancel()
            raise TimeoutError("DNS_DEADLINE_REACHED") from None

    def roots(self, skill):
        roots = [{"kind": "coord", "endpoint": str(base).rstrip("/") + "/public/v1/coord"}
                 for base in self.root_provider()]
        endpoints = {r["endpoint"] for r in roots}
        for record in self.public.known_records():
            for route in record["coord_routes"][:1]:
                if route["endpoint"] not in endpoints:
                    roots.append({"kind": "coord", "endpoint": route["endpoint"], "record": record,
                                  "node_did": record["node_did"]})
        if self.legacy and (self.legacy_available is None or self.legacy_available()):
            roots.append({"kind": "compatibility", "endpoint": "legacy", "node_did": ""})
        return roots[:32]

    def _request(self, endpoint, value=None, *, cap=MAX_COORD_BYTES, timeout=3, timeout_limit=5):
        deadline = time.monotonic() + min(timeout, timeout_limit, 25)
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("协调地址无效")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        authorized = {(urlsplit(b).hostname, urlsplit(b).port or
                       (443 if urlsplit(b).scheme == "https" else 80)) for b in self.root_provider()}
        addresses = self._addresses(parsed.hostname, port, deadline)
        if not addresses:
            raise ValueError("无法解析协调地址")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            allowed = (parsed.hostname, port) in authorized or any(ip in n for n in self.allowed_networks)
            if (not ip.is_global and not allowed) or (parsed.scheme == "http" and not allowed):
                raise PermissionError("引荐不能授予访问回环或内网地址的权限；请明确配置可信网络")
            if ip.is_multicast or ip.is_unspecified or ip.is_link_local:
                raise PermissionError("禁止访问特殊网络地址")
        cls = _PinnedTLS if parsed.scheme == "https" else _PinnedConnection
        connection = cls(parsed.hostname, addresses[0], port=port, timeout=max(.001, min(timeout, 5)))
        cap = max(1, min(int(cap), MAX_COORD_BYTES))
        raw = canonical_json(value).encode() if value is not None else None
        timer = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DEADLINE_EXCEEDED")
            connection.timeout = remaining
            connection.connect()
            sock = connection.sock
            def expire():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DEADLINE_EXCEEDED")
            timer = threading.Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            connection.request("POST" if raw is not None else "GET", parsed.path or "/", body=raw,
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
            response = connection.getresponse()
            if int(response.getheader("Content-Length") or 0) > cap:
                raise ValueError("RESPONSE_TOO_LARGE")
            payload = response.read(cap + 1)
            if len(payload) > cap:
                raise ValueError("RESPONSE_TOO_LARGE")
            # 非 200 也要把 body 读出来：认证后的错误是**已签名 ERROR 封套**，
            # 真实原因（RATE_LIMITED / BUSY / DEADLINE_EXCEEDED）与 Retry-After
            # 只在封套里，光看 HTTP 码会把"被限流"误读成别的问题。
            try:
                data = loads_strict(payload)
            except ValueError:
                if response.status == 200:
                    raise
                data = None  # 非 200 且不是 JSON：交给 _unwrap 报成 "HTTP {status}"。
            return data, len(payload), int(response.status)
        finally:
            if timer:
                timer.cancel()
            connection.close()

    def _check_response(self, response, request, expected=""):
        if (not verify_coord_envelope(response, now=time.time(), recipient_did=self.identity.did)
                or response.get("in_reply_to") != request["request_id"]
                or (expected and response.get("sender_did") != expected)
                or response.get("type") not in {request["type"] + "_RESULT", "ERROR"}):
            raise PermissionError("协调响应身份、签名或请求关联无效")
        if response["type"] == "ERROR":
            body = response["body"]
            raise ValueError(body.get("message") or body.get("error") or body.get("code"))
        if self.on_connection:
            self.on_connection(response["sender_did"])
        return response

    def _unwrap(self, data, status, request, expected=""):
        """校验一次交换的响应；非 200 时优先还原**已签名**的错误原因。

        * 状态码 200：正常校验，签名不对就 ``PermissionError``。
        * 非 200 且是发给我们的合法 ERROR 封套：``_check_response`` 会抛
          ``ValueError(<真实原因>)``，原样上抛。
        * 非 200 且不是可验证的封套（含旧节点没有该接口）：退回
          ``ValueError("协调节点返回 HTTP {status}")``，保留旧的可识别口径
          （``perform`` 靠子串 ``HTTP 404`` 决定是否回退兼容目录）。
        """
        if status != 200:
            try:
                self._check_response(data, request, expected)
            except PermissionError as exc:
                raise ValueError(f"协调节点返回 HTTP {status}") from exc
            raise ValueError(f"协调节点返回 HTTP {status}")
        return self._check_response(data, request, expected)

    def _envelope(self, operation, body, recipient=""):
        return coord_envelope(self.identity, operation, body, recipient_did=recipient,
                              request_id="req_" + uuid.uuid4().hex, ttl=10)

    def connect(self, node_record, deadline):
        record = node_record.to_dict() if isinstance(node_record, NodeRecord) else node_record
        if not verify_node_record(record, now=time.time()) or not record.get("coord_routes"):
            raise PermissionError("目标节点声明无效或没有协调通道")
        route = record["coord_routes"][0]
        return {"node_did": record["node_did"], "record": record, "endpoint": route["endpoint"],
                "channel_type": route["channel_type"], "relay_did": route.get("relay_did", ""),
                "deadline": deadline}

    def exchange(self, session, signed_request, response_cap):
        endpoint = session["endpoint"].rstrip("/")
        timeout = max(.001, session.get("deadline", time.monotonic() + 3) - time.monotonic())
        request = signed_request
        if session.get("channel_type") == "coord_mailbox":
            target = session["node_did"]
            relay_did = session.get("relay_did")
            body = {"origin_did": self.identity.did, "target_did": target,
                    "parent_request_id": request["request_id"], "inner_request": request,
                    "inner_request_hash": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
                    "path": [relay_did, target], "deadline": int(time.time()) + max(1, int(timeout)),
                    "max_response_bytes": response_cap, "max_operations": 1}
            outer = self._envelope("FORWARD_COORD", body, relay_did)
            response, count, status = self._request(endpoint.removesuffix("/mailbox") + "/forward", outer,
                                                    cap=response_cap, timeout=timeout)
            self._unwrap(response, status, outer, relay_did)
            response = response["body"].get("target_response") or {}
        else:
            suffix = next(k for k, v in PATH_OPERATIONS.items() if v == request["type"])
            response, count, status = self._request(endpoint + "/" + suffix, request, cap=response_cap, timeout=timeout)
        self._unwrap(response, status, request, session["node_did"])
        return {"envelope": response, "bytes": count}

    def handshake(self, source, cap, timeout):
        deadline = time.monotonic() + timeout
        record = source.get("record")
        count = 0
        if record:
            session = self.connect(record, deadline)
        else:
            endpoint = source["endpoint"].rstrip("/")
            nonce = uuid.uuid4().hex
            request = self._envelope("HELLO", {"versions": ["a2n-coord/1"],
                "node_record": self.public.record(), "nonce": nonce})
            response, used, status = self._request(endpoint + "/hello", request, cap=min(8192, cap), timeout=timeout)
            count += used
            self._unwrap(response, status, request)
            record = response["body"].get("node_record")
            if (response["body"].get("nonce") != nonce or not verify_node_record(record, now=time.time())
                    or record["node_did"] != response["sender_did"]):
                raise PermissionError("首次协调握手不匹配")
            session = {"node_did": record["node_did"], "record": record, "endpoint": endpoint,
                       "channel_type": "direct", "deadline": deadline}
        nonce = uuid.uuid4().hex
        request = self._envelope("HELLO", {"versions": ["a2n-coord/1"],
            "node_record": self.public.record(), "nonce": nonce}, session["node_did"])
        answer = self.exchange(session, request, cap - count)
        count += answer["bytes"]
        response = answer["envelope"]["body"]
        if (response.get("nonce") != nonce or not verify_node_record(response.get("node_record"), now=time.time())
                or response["node_record"]["node_did"] != session["node_did"]):
            raise PermissionError("协调握手挑战无效")
        self.public.remember(response["node_record"])
        session["record"] = response["node_record"]
        session["services"] = response.get("services", {})
        # Use the path that answered, never replace it with a self-declared private URL.
        return session, count

    def cost(self, action):
        if action["source"].get("kind") == "compatibility":
            return self.legacy_cost - (4 if action.get("cursor") and self.legacy_cost > 2 else 0)
        return (2 if action["source"].get("record") else 3) if action["kind"] == "find" else 1

    def perform(self, action, skill, byte_cap, timeout):
        source = action["source"]
        if source.get("kind") == "compatibility":
            data = self.legacy(skill, timeout, byte_cap, action.get("cursor", ""))
            raw_size = len(canonical_json(data["entries"]).encode())
            if raw_size > byte_cap:
                raise ValueError("兼容发现响应超过本轮额度")
            # Old protocols cannot supply a complete wire receipt. Retain the
            # full bounded reservation rather than refunding just the card size.
            return {**data, "compatibility": True, "bytes": byte_cap}
        if action["kind"] == "find":
            try:
                session, count = self.handshake(source, byte_cap, timeout)
            except ValueError as exc:
                # Only explicitly configured old nodes can fall back to the old directory.
                if "HTTP 404" not in str(exc) or source.get("record"):
                    raise
                cards = self._legacy_directory(source["endpoint"], skill, byte_cap, timeout)
                return {"cards": cards[0], "bytes": cards[1], "compatibility": True}
            body = {"skill": skill, "coarse_requirements": {}, "page_size": 10,
                    "cursor": action.get("cursor") or None,
                    "query_fingerprint": query_fingerprint(skill, {}, 10),
                    "max_response_bytes": max(1024, byte_cap - count - 1024)}
            request = self._envelope("FIND", body, session["node_did"])
            result = self.exchange(session, request, byte_cap - count)
            data = result["envelope"]["body"]
            if data.get("query_fingerprint") != body["query_fingerprint"]:
                raise ValueError("发现应答与查询不一致")
            refs = []
            for item in data.get("referrals", [])[:8]:
                if verify_referral(item, now=time.time()) and item["introducer_did"] == session["node_did"]:
                    record = item["target_record"]
                    if record["node_did"] == self.identity.did:
                        continue
                    refs.append({"kind": "coord", "node_did": record["node_did"], "record": record,
                                 "endpoint": record["coord_routes"][0]["endpoint"] if record["coord_routes"] else ""})
            return {"offers": data.get("offers", [])[:20], "referrals": refs,
                    "next_cursor": data.get("next_cursor"), "bytes": count + result["bytes"],
                    "source": {**source, "record": session["record"], "node_did": session["node_did"],
                               "endpoint": session["endpoint"], "session": session}}
        offer = action["offer"]
        session = source.get("session") or self.connect(source["record"], time.monotonic() + timeout)
        session = {**session, "deadline": time.monotonic() + timeout}
        key = CandidateKey.from_dict(offer)
        request = self._envelope("GET_CARD", {"key": key.to_dict(), "card_hash": offer["card_hash"]}, session["node_did"])
        result = self.exchange(session, request, byte_cap)
        card = result["envelope"]["body"].get("card") or {}
        info = self.describe_card(card)
        if info["key"] != key.to_dict() or info["card_hash"] != offer["card_hash"]:
            raise ValueError("商品卡与发现线索不一致")
        self.public.cache_card(card)
        return {"cards": [card], "bytes": result["bytes"]}

    def _legacy_directory(self, endpoint, skill, cap, timeout):
        base = endpoint.removesuffix("/public/v1/coord")
        # _request rejects query strings; old directory requires a skill query.
        from .public_directory import PublicDirectoryClient
        client = PublicDirectoryClient([base], timeout=timeout)
        cards = client._fetch(base, skill, 20, max_response_bytes=cap)
        size = len(canonical_json(cards).encode())
        if size > cap:
            raise ValueError("兼容目录响应过大")
        return cards, cap

    def describe_card(self, card, *, compatibility=False):
        ok, reason = verify_card(card, require_endpoint=True)
        ext = card.get("x-a2n") or {}
        if not ok and (not compatibility or ext.get("sovereign")):
            raise ValueError("商品卡验签失败：" + str(reason))
        did = card_did(card) if ok else "unattested:" + hashlib.sha256(str(card.get("url")).encode()).hexdigest()[:24]
        key = CandidateKey.of_card(did, card)
        digest = card_hash(card)
        return {"key": key.to_dict(), "card_hash": digest,
                "verification": "CARD_VERIFIED" if ok else "HINT",
                "route": self.public.route(card) if ok else None}

    def probe_route(self, route, timeout):
        began = time.monotonic()
        result = {"route_id": route["route_id"], "measured_by": self.identity.did,
                  "at": int(time.time()), "probe_kind": "signed_card_metadata",
                  "control_reachable": None, "task_reachable": None, "rtt_ms": None}
        try:
            card, _, status = self._request(route["card"]["url"].rstrip("/") + "/.well-known/agent.json",
                                            cap=65536, timeout=timeout)
            if status != 200:
                raise ValueError(f"商品卡元数据返回 HTTP {status}")
            info = self.describe_card(card)
            result["control_reachable"] = info["key"] == route["key"] and info["card_hash"] == route["route_card_hash"]
            result["rtt_ms"] = round((time.monotonic() - began) * 1000, 2)
        except Exception as exc:
            result.update(control_reachable=False, error=str(exc))
        return result

    def safe_probe(self, coord_route, nonce, deadline):
        route = {"endpoint": coord_route.endpoint}
        began = time.monotonic()
        session, _ = self.handshake(route, 16384, max(.001, deadline - began))
        request = self._envelope("PROBE", {"nonce": nonce}, session["node_did"])
        response = self.exchange(session, request, 8192)
        return RouteObservation(hashlib.sha256(coord_route.endpoint.encode()).hexdigest(), self.identity.did,
                                int(time.time()), "coord_nonce", control_reachable=response["envelope"]["body"].get("nonce") == nonce,
                                rtt_ms=round((time.monotonic() - began) * 1000, 2))

    def probe_task_route(self, route_descriptor, nonce, deadline):
        result = self.probe_route(route_descriptor.to_dict(), max(.001, deadline - time.monotonic()))
        return RouteObservation(**{k: v for k, v in result.items() if k in RouteObservation.__dataclass_fields__})

    def forward(self, session, forwarding_envelope, response_cap):
        return self.exchange(session, forwarding_envelope, response_cap)
