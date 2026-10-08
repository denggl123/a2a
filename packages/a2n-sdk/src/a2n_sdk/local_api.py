"""唯一节点运行时的 HTTP 适配器：回环管理、公共协议与本地工作台 A2A。"""
from __future__ import annotations

import hmac
import ipaddress
import json
import sys
import time
import re
from http.cookies import SimpleCookie
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .calls import CallService
from .coordination import CoordFailure
from .pairing import PairingService, loopback_url
from .ports import CallRequest
from .storage import LocalStore
from .trade_facts import digest as digest_for_points

MAX_BODY = 16 * 1024 * 1024
LOCAL_COOKIE = "A2N_LOCAL_TOKEN"


def _task_view(outcome, context_id: str = "") -> dict:
    wire_task_id = outcome.metadata.get("wire_task_id") or outcome.task_id
    pending = {"WORKING": "working", "SUBMITTED": "submitted", "INPUT-REQUIRED": "input-required",
               "AUTH-REQUIRED": "auth-required", "UNKNOWN": "unknown",
               # 上游 408/5xx 说明"交付结果未知"，不是业务失败 —— 线上状态必须与
               # metadata.a2nState 同一口径，不能在这里偷偷落成 failed。
               "DELIVERY_UNKNOWN": "unknown",
               # A2A has no standard "cancel requested" state.  Keep the wire
               # state honest (still working) and expose the local request in metadata.
               "CANCEL_REQUESTED": "working"}
    terminal = {"CANCELED": "canceled", "CANCELLED": "canceled",
                "REJECTED": "rejected", "FAILED": "failed", "INTERRUPTED": "failed"}
    state = pending.get(outcome.state, terminal.get(
        outcome.state, "completed" if outcome.ok else "failed"))
    # A remote A2A server may allocate/replace the context.  Expose that value
    # to the workbench so a follow-up after input/auth-required continues the
    # same remote conversation while the local task id remains pollable here.
    context_id = str(outcome.metadata.get("a2a_context_id")
                     or outcome.metadata.get("a2aContextId")
                     or context_id or "")
    artifacts = []
    if outcome.result is not None:
        part = ({"kind": "text", "text": outcome.result} if isinstance(outcome.result, str)
                else {"kind": "data", "data": outcome.result})
        artifacts = [{"artifactId": f"art_{wire_task_id}", "parts": [part]}]
    metadata = {"a2nState": outcome.state, "targetRef": outcome.target_ref,
                "acceptance": outcome.verdict, "settlement": outcome.settlement,
                "network": outcome.metadata}
    if outcome.receipt:
        metadata["a2nReceipt"] = outcome.receipt
    if outcome.metadata.get("witness_offer"):
        metadata["a2nWitnessOffer"] = outcome.metadata["witness_offer"]
    if outcome.metadata.get("public_trade_anchor"):
        metadata["a2nPublicTradeAnchor"] = outcome.metadata["public_trade_anchor"]
    if outcome.metadata.get("admission_contract"):
        metadata["a2nAdmissionContract"] = outcome.metadata["admission_contract"]
    if outcome.metadata.get("points_delivery"):
        metadata["a2nPointsDelivery"] = outcome.metadata["points_delivery"]
    for key in ("cancel_requested", "cancel_acknowledged",
                "remote_effect_unknown", "remote_terminal", "cancel_note"):
        if key in outcome.metadata:
            metadata[key] = outcome.metadata[key]
    status = {"state": state}
    remote_status = ((outcome.metadata.get("a2a_task") or {}).get("status") or {})
    if isinstance(remote_status, dict) and remote_status.get("message") is not None:
        status["message"] = remote_status["message"]
    return {"kind": "task", "id": wire_task_id, "contextId": context_id,
            "status": status, "artifacts": artifacts,
            "error": None if outcome.ok else outcome.error,
            "metadata": metadata}


class _LocalServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 允许第二个 socket 静默绑定同一端口（行为未定义）——
    # 个人电脑上"启动成功其实是别人在听"比报错更糟。POSIX 保留 reuseaddr 以便快速重启。
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


class LocalA2AGateway:
    def __init__(self, runtime, *, host="127.0.0.1", port=0, allow_remote_calls=False,
                 management=None, pairing=None, calls=None, public_card_bases=None,
                 peer_exchange=None):
        self.runtime, self.management = runtime, management
        self.allow_remote_calls = allow_remote_calls
        self.pairing = pairing or PairingService()
        self.peer_exchange = peer_exchange
        self._owned_store = None if calls else LocalStore()
        self.calls = calls or CallService(
            runtime.invoke_local_id, self._owned_store,
            refresh_remote=runtime.refresh_remote_task,
            cancel_remote=runtime.cancel_remote_task)
        self.public_card_bases = tuple(str(value).rstrip("/")
                                       for value in (public_card_bases or ()) if value)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "a2n-local-gateway/0.2"

            def setup(self):
                super().setup()
                self.connection.settimeout(120)

            def log_message(self, *_args):
                pass

            def _local(self):
                return ipaddress.ip_address(self.client_address[0]).is_loopback

            def _origin(self):
                origin = self.headers.get("Origin")
                if origin:
                    return origin
                # Browsers omit Origin on same-origin GET; Referer still identifies
                # the paired console. Cross-origin calls continue to use Origin.
                referer = urlsplit(self.headers.get("Referer") or "")
                return f"{referer.scheme}://{referer.netloc}" if referer.netloc else ""

            def _host_ok(self):
                return loopback_url("http://" + (self.headers.get("Host") or ""))

            def _token(self):
                header = self.headers.get("X-A2N-Local-Token") or ""
                if header:
                    return header
                try:
                    cookie = SimpleCookie(self.headers.get("Cookie") or "")
                    return cookie[LOCAL_COOKIE].value if LOCAL_COOKIE in cookie else ""
                except (KeyError, TypeError):
                    return ""

            def _management_ok(self):
                if not self._local() or not self._host_ok():
                    return False
                if not outer.pairing.origin_allowed(self._origin()):
                    return False
                token = self._token()
                return bool(token and (secrets_compare(token, runtime.management_token)
                                       or outer.pairing.verify(token, self._origin())))

            def _calls_ok(self):
                if not outer.pairing.origin_allowed(self._origin()):
                    return False
                return (outer.allow_remote_calls
                        or (self._local() and self._host_ok())
                        # Explicit public_card_bases authorizes one exact
                        # loopback reverse proxy route for both Card and A2A.
                        # Management endpoints still require _management_ok.
                        or bool(self._public_card_base()))

            def _public_card_base(self):
                """Recognize an explicitly configured, loopback reverse proxy."""
                if not self._local():
                    return None
                forwarded_host = (self.headers.get("X-Forwarded-Host") or "").strip()
                forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "").strip()
                raw_host = (self.headers.get("Host") or "").strip()
                for base in outer.public_card_bases:
                    wanted = urlsplit(base)
                    if raw_host == wanted.netloc:
                        return base
                    if (forwarded_host == wanted.netloc
                            and forwarded_proto == wanted.scheme):
                        return base
                return None

            def _send(self, code, obj, *, html=False, headers=None):
                raw = obj if isinstance(obj, bytes) else obj.encode("utf-8") if html else json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html else
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if self._origin() and outer.pairing.origin_allowed(self._origin()):
                    self.send_header("Access-Control-Allow-Origin", self._origin())
                    self.send_header("Vary", "Origin")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Execution is journaled independently of browser lifetime.

            def _drain_small_rejected_body(self):
                """Avoid a Windows TCP reset when rejecting a tiny POST early.

                Never wait on an unbounded or attacker-sized body merely to
                make an error response pretty; those connections may close.
                """
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return
                if 0 < size <= 65_536:
                    previous = self.connection.gettimeout()
                    try:
                        self.connection.settimeout(0.5)
                        self.rfile.read(size)
                    except (OSError, ValueError):
                        pass
                    finally:
                        self.connection.settimeout(previous)

            def _read(self):
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("请使用 application/json")
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    raise ValueError("请求体为空或过大")
                from .serialization import strict_object
                body = strict_object(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求必须是 JSON 对象")
                return body

            def do_OPTIONS(self):
                if not self._calls_ok():
                    return self._send(403, {"error": "控制台来源不被允许"})
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self._origin() or "null")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-A2N-Local-Token, Idempotency-Key, If-Match")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self):
                parsed = urlsplit(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path.startswith("/v1/coord/"):
                    if not self._management_ok():
                        return self._send(401, {"error": "请先通过本机或已配对的管理接口访问"})
                    from .coordination_api import dispatch
                    from .coordination_service import SearchError
                    try:
                        status, result = dispatch(getattr(outer.management, "coordination", None),
                                                  "GET", path, parse_qs(parsed.query), {}, self.headers)
                        return self._send(status, result)
                    except SearchError as exc:
                        return self._send(exc.status, exc.body())
                    except (ValueError, TypeError, KeyError) as exc:
                        return self._send(400, {"error": str(exc)})
                parts = path.strip("/").split("/")
                if (len(parts) == 7 and parts[:2] == ["relay", "v1"]
                        and parts[3] == "a2a"
                        and parts[5:] == [".well-known", "agent.json"]):
                    try:
                        outer.management._require_public_service("task_relay")
                        card = outer.management.relay_service.card(parts[2], parts[4])
                        return self._send(200, card) if card else self._send(
                            404, {"error": "中继卡片不存在"})
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except (ValueError, AttributeError) as exc:
                        return self._send(400, {"error": str(exc)})
                if path == "/public/v1/agents":
                    if not outer.management:
                        return self._send(404, {"error": "没有公共目录"})
                    query = parse_qs(parsed.query)
                    try:
                        skill = (query.get("skill") or [""])[0]
                        limit = int((query.get("limit") or ["30"])[0])
                        return self._send(200, outer.management.public_directory(
                            skill, limit=limit))
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                if path == "/public/v1/samples":
                    # 买方入口：调用前只读查看某供给真实交付过的公开样品（分页/上限/摘要优先）。
                    if not outer.management:
                        return self._send(404, {"error": "没有公共服务"})
                    query = parse_qs(parsed.query)
                    try:
                        return self._send(200, outer.management.public_samples(
                            (query.get("service_id") or [""])[0],
                            limit=int((query.get("limit") or ["10"])[0]),
                            cursor=(query.get("cursor") or [""])[0],
                            brief=(query.get("brief") or ["0"])[0].lower()
                            in {"1", "true", "yes"}))
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                if path in {"/public/v1/routes", "/public/v1/witness"}:
                    if not outer.management:
                        return self._send(404, {"error": "没有公共服务"})
                    query = parse_qs(parsed.query)
                    try:
                        if path.endswith("routes"):
                            return self._send(200, outer.management.public_routes(
                                (query.get("did") or [""])[0]))
                        record = outer.management.public_witness_get(
                            (query.get("receipt_hash") or [""])[0])
                        return self._send(200, record) if record else self._send(
                            404, {"error": "没有这份见证"})
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                card_item = card_id_from_path(path)
                if path == "/.well-known/agent.json":
                    card_item = (parse_qs(parsed.query).get("id") or [""])[0]
                public_card_base = self._public_card_base() if card_item is not None else None
                if not self._calls_ok() and not public_card_base:
                    return self._send(403, {"error": "本机网关没有开放此访问"})
                if path in {"/", "/console"}:
                    if not self._local() or not self._host_ok():
                        return self._send(403, {"error": "管理页只在本机开放"})
                    return self._send(200, (Path(__file__).parent / "web" / "runtime.html").read_text(
                        encoding="utf-8"), html=True, headers={
                            "Set-Cookie": (
                                f"{LOCAL_COOKIE}={runtime.management_token}; "
                                "Path=/; HttpOnly; SameSite=Strict"
                            )
                        })
                if path in {"/console/discovery.js", "/console/coordination.js", "/console/experience.js", "/console/business.js", "/console/payment.js", "/console/points.js", "/console/forms.js", "/console/onboarding.js"}:
                    if not self._local() or not self._host_ok():
                        return self._send(403, {"error": "管理页只在本机开放"})
                    raw = (Path(__file__).parent / "web" / path.rsplit("/", 1)[1]).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if path == "/health":
                    return self._send(200, {"ok": True, "service": "a2n-runtime", "version": 2,
                                            "instance_key": getattr(runtime, "instance_key", None)})
                if path.startswith("/v1/assets/") and path.endswith("/content"):
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    try:
                        book = outer.management.assets.book
                        asset_id = path.removeprefix("/v1/assets/").removesuffix("/content")
                        ref = book.ref(asset_id)
                        start, end, status = 0, ref["size"] - 1, 200
                        if self.headers.get("Range"):
                            match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"])
                            if not match or not any(match.groups()):
                                raise ValueError("ASSET_RANGE_INVALID")
                            lower, upper = match.groups()
                            if not lower:
                                suffix = int(upper)
                                if suffix < 1:
                                    raise ValueError("ASSET_RANGE_INVALID")
                                start = max(0, ref["size"] - suffix)
                            else:
                                start, end = int(lower), min(int(upper), end) if upper else end
                            if not 0 <= start <= end < ref["size"]:
                                raise ValueError("ASSET_RANGE_INVALID")
                            status = 206
                        self.send_response(status)
                        self.send_header("Content-Type", ref["mime_type"])
                        self.send_header("Content-Length", str(end - start + 1))
                        self.send_header("Content-Disposition", f'attachment; filename="{asset_id}.bin"')
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Accept-Ranges", "bytes")
                        if status == 206:
                            self.send_header("Content-Range", f'bytes {start}-{end}/{ref["size"]}')
                        self.end_headers()
                        for chunk in book.chunks(asset_id, start, end):
                            self.wfile.write(chunk)
                        return
                    except ValueError as exc:
                        return self._send(416 if str(exc) == "ASSET_RANGE_INVALID" else 404, {"error": str(exc)})
                if path == "/v1/onboarding":
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开"})
                    service = getattr(outer.management, "onboarding", None)
                    return self._send(200, service.status()) if service else self._send(404, {"error": "完整节点未安装"})
                if path in {"/v1/runtime", "/v1/accounts"}:
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    return self._send(200, runtime.accounts.list() if path.endswith("accounts") else
                                      outer.management.snapshot() if outer.management else runtime.snapshot())
                if path == "/v1/disputes":
                    # 「这单我不认」的本机记录：只读列表。开单/撤回走 management.command。
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    if not outer.management:
                        return self._send(404, {"error": "非持久模式没有本地不认记录"})
                    query = parse_qs(parsed.query)
                    return self._send(200, {
                        "disputes": outer.management.disputes.list(
                            state=(query.get("state") or [None])[0],
                            scope=(query.get("scope") or [None])[0],
                            task_id=(query.get("task_id") or [None])[0],
                            limit=int((query.get("limit") or ["50"])[0])),
                        "counts": outer.management.disputes.counts(),
                        "kind": "local_rejection_not_arbitration"})
                if path == "/v1/quality":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    from .quality import quality_facts
                    return self._send(200, quality_facts(outer.management.store, outer.management.feedback,
                        scope=(parse_qs(parsed.query).get("scope") or [""])[0]))
                if path == "/v1/x402" or path.startswith("/v1/x402/intents/"):
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    service = getattr(outer.management, "x402", None)
                    if service is None:
                        return self._send(404, {"error": "X402_NOT_CONFIGURED"})
                    if path == "/v1/x402":
                        return self._send(200, service.status())
                    try:
                        if not service.buyer:
                            raise ValueError("X402_NOT_CONFIGURED")
                        return self._send(200, service.buyer.outcome(path.removeprefix("/v1/x402/intents/")))
                    except ValueError as exc:
                        return self._send(404, {"error": str(exc)})
                if path == "/v1/payment-coordination":
                    if not self._management_ok():
                        return self._send(403, {"error": "local management authentication required"})
                    service = getattr(outer.management, "payment_coordination", None)
                    return self._send(200, service.status()) if service else self._send(404, {"error": "not configured"})
                if path == "/v1/points":
                    if not self._management_ok():
                        return self._send(403, {"error": "需要本机管理凭据"})
                    service = getattr(outer.management, "points", None)
                    return self._send(200, service.summary()) if service else self._send(404, {"error": "not configured"})
                if path in {"/v1/payments", "/v1/risk"}:
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    if path == "/v1/risk":
                        book = getattr(outer.management, "risk", None)
                        return self._send(200, {"policy": book.policy() if book else None,
                            "reservations": list(outer.management.store.items("risk_reservations").values())})
                    book = getattr(outer.management, "payments", None)
                    return self._send(200, {"capabilities": book.capabilities() if book else {"available": False},
                        "intents": list(outer.management.store.items("payment_intents").values()), "platform_commission_minor": 0})
                if path.startswith("/v1/disputes/") and path.endswith("/messages"):
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    dispute_id = path.removeprefix("/v1/disputes/").removesuffix("/messages")
                    dispute = outer.management.disputes.get(dispute_id)
                    if not dispute:
                        return self._send(404, {"error": "异议不存在"})
                    messages=outer.management.resolutions.messages(dispute.get("trade_uid", ""))
                    return self._send(200, {"messages":messages,
                        "amount_minor_decimal":{m["message_id"]:str(m["body"]["amount_minor"]) for m in messages if m["kind"]=="PROPOSAL"}})
                if path == "/v1/policies":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    book = getattr(outer.management, "policies", None)
                    return self._send(200, {"policy": book.get() if book else None,
                        "opportunities": book.list() if book else []})
                if path == "/v1/reputation":
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    book = getattr(outer.management, "reputation", None)
                    return self._send(200, {"views": book.list() if book else [], "mode": "SHADOW"})
                if path == "/v1/trades" or path.startswith("/v1/trades/"):
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    book = getattr(outer.management, "trade_facts", None)
                    if book is None:
                        return self._send(404, {"error": "没有交易事实服务"})
                    if path == "/v1/trades":
                        return self._send(200, {"trades": book.list()})
                    record = book.get(path.removeprefix("/v1/trades/"))
                    return self._send(200, record) if record else self._send(404, {"error": "交易不存在"})
                if path.startswith("/v1/experience/queries/"):
                    if not self._management_ok():
                        return self._send(401, {"error": "需要本机管理凭据"})
                    service = getattr(outer.management, "experience_service", None)
                    record = service.get(path.removeprefix("/v1/experience/queries/")) if service else None
                    return self._send(200, record) if record else self._send(404, {"error": "体验查询不存在"})
                if path == "/v1/feedback" or path == "/v1/feedback/summary" \
                        or path.startswith("/v1/feedback/"):
                    # 双方反馈（R2）只读面：list / versions / summary（FEEDBACK-API §3）。
                    # 开单/改单/补交走 management.command；这里**只读，不动任何账本**。
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    if not outer.management:
                        return self._send(404, {"error": "非持久模式没有本地反馈记录"})
                    book = outer.management.feedback
                    q = parse_qs(parsed.query)
                    if path == "/v1/feedback/summary":
                        # 只给事实计数与各维平均（含样本数），不给信誉分、不给等级 —— 那是 R3。
                        who = (q.get("counterparty") or q.get("provider_did") or [""])[0]
                        return self._send(200, book.summary(counterparty=who))
                    if path == "/v1/feedback":
                        try:
                            limit = int((q.get("limit") or ["50"])[0])
                        except (ValueError, TypeError):
                            limit = 50
                        return self._send(200, book.page(
                            task_id=(q.get("task_id") or [None])[0],
                            direction=(q.get("direction") or [None])[0],
                            counterparty=(q.get("counterparty") or [None])[0],
                            limit=limit, cursor=(q.get("cursor") or [""])[0]))
                    rest = path[len("/v1/feedback/"):]
                    if rest.endswith("/versions"):
                        fid = rest[:-len("/versions")].strip("/")
                        chain = book.versions(fid)
                        if not chain and not book.get(fid):
                            return self._send(404, {"error": "没有这份反馈"})
                        # 版本链按 revision 升序（旧版只读，最新在最后）。
                        return self._send(200, {"feedback_id": fid,
                                                "versions": [book.view(r) for r in chain],
                                                "count": len(chain)})
                    return self._send(404, {"error": "not found"})
                if path == "/v1/calls/detail":
                    # 收据与凭证详情：管理面受保护；只带出凭证事实，不带请求载荷。
                    if not self._management_ok():
                        return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                    if not outer.management:
                        return self._send(404, {"error": "非持久模式没有本地调用记录"})
                    q = parse_qs(parsed.query)
                    scope = (q.get("scope") or [""])[0]
                    task_id = (q.get("task_id") or [""])[0]
                    row = outer.management.store.task(scope, task_id)
                    if not row:
                        return self._send(404, {"error": "本地没有这条调用记录"})
                    outcome = row.get("outcome") or {}
                    from .assets import valid_ref
                    result = outcome.get("result")
                    asset_refs = result.get("assets") or [] if isinstance(result, dict) else []
                    return self._send(200, {
                        "scope": scope, "task_id": task_id, "state": row["state"],
                        "assets": [ref for ref in asset_refs if valid_ref(ref)][:8],
                        "receipt": outcome.get("receipt"),
                        "settlement": outcome.get("settlement") or {},
                        "verdict": outcome.get("verdict") or {},
                        "disputes": outer.management.disputes.list(
                            scope=scope, task_id=task_id),
                        "metadata": outcome.get("metadata") or {}})
                item_id = card_item
                if item_id is not None:
                    card = runtime.card_for(item_id, public_base=public_card_base)
                    return self._send(200, card) if card else self._send(404, {"error": "Agent 不存在"})
                return self._send(404, {"error": "not found"})

            def do_PUT(self):
                path = urlsplit(self.path).path
                if not path.startswith("/v1/policies/") and path != "/v1/risk":
                    return self._send(404, {"error": "not found"})
                if not self._management_ok():
                    return self._send(401, {"error": "需要本机管理凭据"})
                expected = self.headers.get("If-Match")
                if expected is None:
                    return self._send(428, {"code": "REV_REQUIRED"})
                try:
                    if path == "/v1/risk":
                        body = self._read()
                        row = outer.management.risk.configure(body.get("limits"), max_pending=body.get("max_pending", 2),
                                                             expected_revision=int(expected.strip('"')))
                        return self._send(200, row)
                    book = getattr(outer.management, "policies", None)
                    if book is None:
                        return self._send(404, {"error": "没有本地策略"})
                    row = book.update(path.removeprefix("/v1/policies/"), self._read()["values"],
                        expected_revision=int(expected.strip('"')))
                    return self._send(200, row)
                except (ValueError, KeyError) as exc:
                    return self._send(409 if str(exc) == "REV_CONFLICT" else 400, {"error": str(exc)})

            def do_POST(self):
                path = urlsplit(self.path).path.rstrip("/")
                if path == "/v1/assets/upload":
                    if not self._management_ok():
                        self.close_connection = True
                        return self._send(401, {"error": "需要本机管理凭据"})
                    try:
                        size = int(self.headers.get("Content-Length") or 0)
                        mime_type = (self.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
                        self.connection.settimeout(10)
                        deadline = time.monotonic() + 60
                        def chunks():
                            remaining = size
                            while remaining:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("ASSET_UPLOAD_DEADLINE")
                                chunk = self.rfile.read(min(remaining, 65536))
                                if not chunk:
                                    raise ValueError("ASSET_LENGTH_MISMATCH")
                                remaining -= len(chunk)
                                yield chunk
                        result = outer.management.assets.upload(size, mime_type, chunks())
                        return self._send(201, result)
                    except (ValueError, TimeoutError) as exc:
                        self.close_connection = True
                        return self._send(400, {"error": str(exc)})
                if path == "/public/v1/trades/quote":
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 262144:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        return self._send(*outer.management.trade_service.public_quote(self._read()))
                    except (ValueError, KeyError, TypeError) as exc:
                        return self._send(400, {"code": "INVALID_BUSINESS_QUOTE", "error": str(exc)})
                if path.startswith(("/public/v1/settlement/", "/public/v1/settlement-mailbox/")):
                    mailbox = path.startswith("/public/v1/settlement-mailbox/")
                    prefix = "/public/v1/settlement-mailbox/" if mailbox else "/public/v1/settlement/"
                    service = getattr(outer.management, "public_settlement_mailbox" if mailbox else "settlement_gateway", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 196608:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        return self._send(*service.handle(path.removeprefix(prefix), self._read()))
                    except (ValueError, KeyError, TypeError):
                        return self._send(400, {"code": "INVALID_SETTLEMENT_ENVELOPE"})
                if path.startswith("/public/v1/points/") or path == "/public/v1/trades/points-recover":
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 262144:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        body = self._read()
                        if path.endswith("points-recover"):
                            return self._send(200, outer.management.trade_service.public_recover(body))
                        action = path.removeprefix("/public/v1/points/")
                        service = outer.management.points_node
                        return self._send(*service.public(action, body))
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        return self._send(400, {"code": "INVALID_POINTS_REQUEST", "error": str(exc)})
                    except Exception:
                        return self._send(503, {"code": "POINTS_SERVICE_UNAVAILABLE"})
                if path.startswith("/public/v1/payments/"):
                    service = getattr(outer.management, "payment_coordination", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 262144:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        result = service.public(path.removeprefix("/public/v1/payments/"), self._read(),
                            signature=self.headers.get("PAYMENT-SIGNATURE"), replay_token=self.headers.get("Idempotency-Key"))
                        if hasattr(result, "status"):
                            return self._send(result.status, result.body, headers=result.headers)
                        return self._send(*result)
                    except (ValueError, KeyError, TypeError, IndexError):
                        return self._send(400, {"code": "INVALID_PAYMENT_REQUEST"})
                    except Exception:
                        return self._send(503, {"code": "PAYMENT_CHANNEL_UNAVAILABLE"})
                if path.startswith("/public/v1/assets/"):
                    service = getattr(outer.management, "assets", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 8192:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        status, result = service.handle(path.removeprefix("/public/v1/assets/"), self._read())
                        return self._send(status, result)
                    except (ValueError, KeyError, TypeError):
                        return self._send(400, {"code": "INVALID_REQUEST"})
                if path.startswith("/public/v1/resolution/"):
                    service = getattr(outer.management, "public_resolution", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 16384:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        status, result = service.handle(path.removeprefix("/public/v1/resolution/"), self._read())
                        return self._send(status, result)
                    except (ValueError, TypeError, KeyError):
                        return self._send(400, {"code": "INVALID_REQUEST"})
                if path.startswith(("/public/v2/metadata-mailbox/", "/public/v1/asset-mailbox/")):
                    asset_route = path.startswith("/public/v1/asset-mailbox/")
                    prefix = "/public/v1/asset-mailbox/" if asset_route else "/public/v2/metadata-mailbox/"
                    service = getattr(outer.management, "public_asset_mailbox" if asset_route else "public_metadata_mailbox", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 196608:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        status, result = service.handle(path.removeprefix(prefix), self._read())
                        return self._send(status, result)
                    except (ValueError, TypeError, KeyError):
                        return self._send(400, {"code": "INVALID_REQUEST"})
                if path.startswith("/public/v1/experience/"):
                    service = getattr(outer.management, "public_experience", None)
                    if service is None:
                        return self._send(404, {"code": "UNSUPPORTED_PROTOCOL"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 16384:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE"})
                        status, result = service.handle(path.removeprefix("/public/v1/experience/"), self._read())
                        return self._send(status, result)
                    except (ValueError, TypeError, KeyError):
                        return self._send(400, {"code": "INVALID_REQUEST"})
                if path.startswith("/public/v1/coord/"):
                    service = getattr(outer.management, "public_coordination", None)
                    if service is None:
                        return self._send(404, {"error": "没有公共协调服务"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 262144:
                            return self._send(413, {"code": "REQUEST_TOO_LARGE", "error": "协调请求过大"})
                        body = self._read()
                        suffix = path.removeprefix("/public/v1/coord/")
                        operations = {"hello": "HELLO", "find": "FIND", "card": "GET_CARD",
                                      "routes": "RESOLVE_ROUTES", "probe": "PROBE", "forward": "FORWARD_COORD"}
                        if suffix.startswith("mailbox/"):
                            result = service.mailbox(suffix.split("/")[1], body)
                        elif suffix in operations:
                            result = service.handle(operations[suffix], body)
                        else:
                            return self._send(404, {"error": "协调接口不存在"})
                        return self._send(200, result)
                    except CoordFailure as exc:
                        # 身份已认证：带 HTTP 状态回**已签名** ERROR 封套（契约 §7），
                        # 让对端能分辨 RATE_LIMITED / BUSY / DEADLINE_EXCEEDED，而不是
                        # 只看到一个裸状态码、把它误当成身份问题。限流附 Retry-After。
                        headers = {}
                        retry = (exc.envelope.get("body") or {}).get("retry_after_seconds")
                        if retry:
                            headers["Retry-After"] = str(retry)
                        return self._send(exc.status, exc.envelope, headers=headers)
                    except PermissionError as exc:
                        return self._send(401, {"code": "UNVERIFIED_IDENTITY", "error": str(exc)})
                    except TimeoutError as exc:
                        return self._send(429, {"code": "RATE_LIMITED", "error": str(exc)})
                    except (ValueError, TypeError, KeyError) as exc:
                        return self._send(400, {"code": "INVALID_REQUEST", "error": str(exc)})
                if path.startswith("/v1/coord/"):
                    if not self._management_ok():
                        self._drain_small_rejected_body()
                        return self._send(401, {"error": "请先通过本机或已配对的管理接口访问"})
                    from .coordination_api import dispatch
                    from .coordination_service import SearchError
                    try:
                        status, result = dispatch(getattr(outer.management, "coordination", None),
                                                  "POST", path, {}, self._read(), self.headers)
                        return self._send(status, result)
                    except SearchError as exc:
                        return self._send(exc.status, exc.body())
                    except (ValueError, TypeError, KeyError) as exc:
                        return self._send(400, {"error": str(exc)})
                if path.startswith("/relay/v1/"):
                    try:
                        if not outer.management or not outer.management.relay_service:
                            return self._send(404, {"error": "没有公共中继"})
                        outer.management._require_public_service("task_relay")
                        if int(self.headers.get("Content-Length") or 0) > 1_400_000:
                            return self._send(413, {"error": "中继封套过大"})
                        body = self._read()
                        relay = outer.management.relay_service
                        action = path.removeprefix("/relay/v1/")
                        if action in {"register", "poll", "complete"}:
                            proof = body.pop("auth", None)
                            result = getattr(relay, action)(body, proof)
                            return self._send(200, result)
                        parts = action.split("/")
                        if len(parts) == 3 and parts[1] == "a2a":
                            did, _kind, sid = parts
                            kind = "a2a"
                        elif len(parts) == 4 and parts[1:3] == ["a2n", "ack"]:
                            did, _a2n, _ack, sid = parts
                            kind = "ack"
                        else:
                            return self._send(404, {"error": "中继路径不存在"})
                        return self._send(200, relay.submit(
                            did, sid, kind, body.get("envelope")))
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except TimeoutError as exc:
                        return self._send(504, {"error": str(exc)})
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        return self._send(400, {"error": str(exc)})
                if path == "/public/v1/witness":
                    if not outer.management:
                        return self._send(404, {"error": "没有公共见证"})
                    try:
                        if int(self.headers.get("Content-Length") or 0) > 65_536:
                            return self._send(413, {"error": "见证材料过大"})
                        body = self._read()
                        return self._send(201, outer.management.public_witness(
                            body.get("claim")))
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                    except (ValueError, TypeError) as exc:
                        return self._send(400, {"error": str(exc)})
                if not self._calls_ok():
                    self._drain_small_rejected_body()
                    return self._send(403, {"error": "本机网关没有开放此访问"})
                try:
                    body = self._read()
                    if path == "/v1/pairing":
                        if not self._local() or not self._host_ok():
                            raise PermissionError("配对只允许本机访问")
                        return self._send(200, outer.pairing.exchange(body.get("code") or "", self._origin()))
                    if path == "/a2n/ack":
                        if not outer.peer_exchange:
                            return self._send(404, {"error": "节点未启用双边收据"})
                        if not isinstance(body.get("ack"), dict):
                            raise ValueError("ack 必须是签名回执")
                        status, result = outer.peer_exchange.acknowledge(
                            str(body.get("service_id") or ""), body["ack"],
                            body.get("witness_claim"), body.get("feedback"))
                        return self._send(status, result)
                    if path.startswith("/v1/"):
                        if not self._management_ok():
                            return self._send(401, {"error": "请从本机控制台打开，或先完成远程管理配对"})
                        if path == "/v1/disconnect":
                            outer.pairing.revoke(self._token())
                            return self._send(200, {"ok": True})
                        if path == "/v1/pairing/new":
                            return self._send(200, {"code": outer.pairing.new_code(), "expires_in": 300})
                        if outer.management:
                            if path in {"/v1/experience/queries", "/v1/reputation/rebuild"}:
                                body["command_id"] = self.headers.get("Idempotency-Key") or body.get("command_id", "")
                            if path.startswith("/v1/disputes/") and path.endswith(("/messages", "/agreements")):
                                body["command_id"] = self.headers.get("Idempotency-Key") or body.get("command_id", "")
                            status, result = outer.management.command(path, body)
                            return self._send(status, result)
                        # Lightweight in-process runtime retains its existing management API.
                        if path == "/v1/accounts":
                            return self._send(201, runtime.add_account(**body))
                        if path == "/v1/projections":
                            item = runtime.import_agent(body.get("card") or {},
                                target_ref=body.get("target_ref"), projection_id=body.get("projection_id"),
                                headers=body.get("headers"), account_ref=body.get("account_ref"))
                            return self._send(201, {"projection_id": item.projection_id, "card": item.local_card})
                        if path == "/v1/bindings/http":
                            config = dict(body)
                            config["source_card"] = config.pop("card", {})
                            item = runtime.mount_http(**config)
                            return self._send(201, {"service_id": item.service_id,
                                                    "card": runtime.project_binding(item.service_id)})
                    upstream_id = upstream_id_from_path(path)
                    if upstream_id is not None:
                        result = outer.calls.invoke(upstream_id, request_from_plain(body))
                        return self._send(200, result.result) if result.ok else self._send(
                            502, {"error": result.error, "state": result.state})
                    item_id = invoke_id_from_path(path)
                    if item_id is not None:
                        return self._a2a(item_id, body)
                    return self._send(404, {"error": "not found"})
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    self._send(400, {"error": str(exc)})

            def _a2a(self, item_id, rpc):
                rid = rpc.get("id")
                if rpc.get("jsonrpc") != "2.0":
                    return self._send(200, rpc_error(rid, -32600, "只支持 JSON-RPC 2.0"))
                params = rpc.get("params") or {}
                if not isinstance(params, dict):
                    return self._send(200, rpc_error(rid, -32602, "params 必须是对象"))
                if rpc.get("method") == "tasks/get":
                    task_id = str(params.get("id") or params.get("taskId") or "")
                    if outer.peer_exchange and not self._management_ok():
                        task_id = outer.peer_exchange.authorize_control(
                            item_id, task_id, "tasks/get", params.get("a2nPeerControl"))
                    result = outer.calls.get(
                        item_id, task_id,
                        refresh_remote=True)
                    return self._send(200, rpc_ok(rid, _task_view(result)) if result else
                                      rpc_error(rid, -32602, "任务不存在"))
                if rpc.get("method") == "tasks/cancel":
                    task_id = str(params.get("id") or params.get("taskId") or "")
                    if not task_id:
                        return self._send(200, rpc_error(rid, -32602, "任务标识不能为空"))
                    if outer.peer_exchange and not self._management_ok():
                        task_id = outer.peer_exchange.authorize_control(
                            item_id, task_id, "tasks/cancel", params.get("a2nPeerControl"))
                    result = outer.calls.cancel(item_id, task_id)
                    return self._send(200, rpc_ok(rid, _task_view(result)) if result else
                                      rpc_error(rid, -32602, "任务不存在"))
                if rpc.get("method") != "message/send":
                    return self._send(200, rpc_error(rid, -32601, "不支持的方法"))
                binding = runtime.bindings.get(item_id)
                if binding and not self._management_ok() and (
                        not binding.enabled or not binding.metadata.get("listed", True)):
                    raise PermissionError("供给已暂停或下架，不接受新的公共调用")
                try:
                    message = params.get("message") or {}
                    parts = message.get("parts") or []
                    payload = message
                    for part in parts:
                        if "data" in part or "text" in part:
                            payload = part.get("data") if "data" in part else part.get("text")
                            break
                    meta = dict(params.get("metadata") or {})
                    peer_proof = meta.pop("a2nPeerRequest", None)
                    meta.pop("_a2n_verified_peer", None)
                    meta.pop("_a2n_wire_task_id", None)
                    meta.pop("_a2n_owner_invocation", None)
                    meta.pop("_a2n_anonymous", None)
                    task_id = str(meta.pop("a2nTaskId", "") or message.get("messageId") or CallRequest().task_id)
                    if len(task_id) > 200:
                        raise ValueError("任务标识过长")
                    request = CallRequest(skill=str(meta.pop("skill", "")), payload=payload,
                                          message=message, context_id=str(params.get("contextId") or ""),
                                          task_id=task_id, metadata=meta)
                    if binding and peer_proof is None:
                        request.metadata["_a2n_owner_invocation" if self._management_ok() else "_a2n_anonymous"] = True
                    if peer_proof is not None:
                        if not outer.peer_exchange:
                            raise PermissionError("本节点未启用签名调用")
                        request.metadata["_a2n_verified_peer"] = outer.peer_exchange.authenticate(
                            item_id, peer_proof, message=message,
                            context_id=request.context_id, metadata=meta,
                            task_id=task_id, skill=request.skill)
                        journal_id = request.metadata["_a2n_verified_peer"].get("journal_task_id", task_id)
                        if journal_id != task_id:
                            request.metadata["_a2n_wire_task_id"] = task_id
                            request.task_id = journal_id
                    blocking = (params.get("configuration") or {}).get("blocking", True) is not False
                    result = outer.calls.invoke(item_id, request, blocking=blocking)
                    return self._send(200, rpc_ok(rid, _task_view(result, request.context_id)))
                except (ValueError, TypeError, AttributeError) as exc:
                    return self._send(200, rpc_error(rid, -32602, str(exc)))

        self.http = _LocalServer((host, port), Handler)
        self.thread = None

    @property
    def base_url(self):
        host, port = self.http.server_address[:2]
        return f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"

    def start(self):
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name="a2n-local-a2a")
        self.thread.start()
        return self

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self._owned_store:
            self.calls.stop()
            self._owned_store.close()


def secrets_compare(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def card_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) == 4 and parts[0] == "a2a" and parts[2:] == [".well-known", "agent.json"] else None


def invoke_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "a2a" else None


def upstream_id_from_path(path):
    parts = path.strip("/").split("/")
    return parts[2] if len(parts) == 4 and parts[:2] == ["_a2n", "upstream"] and parts[3] == "invoke" else None


def request_from_plain(body):
    metadata = dict(body.get("metadata") or {})
    metadata.pop("_a2n_verified_peer", None)
    metadata.pop("a2nPeerRequest", None)
    return CallRequest(skill=str(body.get("skill") or ""), payload=body.get("payload"),
                       message=body.get("message"), context_id=str(body.get("context_id") or ""),
                       task_id=str(body.get("task_id") or CallRequest().task_id),
                       metadata=metadata)


def rpc_ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
