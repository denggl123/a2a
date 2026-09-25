"""直连通道：调用不经任何第三方。

平台模式下，调用要经平台的公网入口（中继 / 隧道）。无托管模式下没有平台，
所以每个节点自己开一个入口，对方直接打过来 —— **节点即服务，谁也不是谁的中间人**。

    发现（UDP gossip：轻、可多跳、容忍丢包）   ← a2n-p2p
    调用（HTTP 直连：重、单跳、要可靠）        ← 本模块

大负载不走 gossip 是既有的分层纪律（UDP 控制面保持在 MTU 安全范围），这里只是把它落到调用上：
真实交付物（图片、文档、模型输出）根本不进 UDP。

请求**自带身份与签名**（DID 取代平台模式下的 X-Principal 明文声明）。要说清它的
边界：签名只证明"消息出自持该私钥者"，**不证明"这人可信"**。后者是节点自己的
准入策略（node.py 的 policy）—— 去掉中心之后，"信不信你"本来就该由本地决定，
而不是假装有一个全局信誉在替所有人担保。
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from a2n_kernel.hashing import new_id, now_iso
from a2n_p2p import Identity, verify_pub

from .card import did_from_pub, pub_b64, pub_unb64
from .receipt import hash_payload

V = 1
TS_WINDOW = 300          # 秒：请求的时间窗。太窄会把时钟漂移的节点全拒掉
MAX_BODY = 8 * 1024 * 1024


# ---------------- 反重放 ----------------

class ReplayGuard:
    """nonce + 时间窗。

    只防"同一份请求被原样重发"：签名有效但 nonce 见过 = 重放；时间戳越窗 = 重放。
    它防不住"同一份语义被签两次"（那本来就需要签两次，是真调用）。
    """

    def __init__(self, window: int = TS_WINDOW) -> None:
        self.window = window
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, nonce: str, ts: float | None, now: float | None = None) -> tuple[bool, str]:
        now = time.time() if now is None else now
        try:
            stamp = float(ts)
        except (TypeError, ValueError, OverflowError):
            return False, "请求时间戳无效"
        if not math.isfinite(stamp) or abs(now - stamp) > self.window:
            return False, "请求时间戳越窗（可能是重放或时钟不同步）"
        if not isinstance(nonce, str) or not nonce or len(nonce) > 200:
            return False, "请求 nonce 无效"
        with self._lock:
            self._prune(now)
            if nonce in self._seen:
                return False, "这个 nonce 已经用过（重放）"
            self._seen[nonce] = now
        return True, "ok"

    def _prune(self, now: float) -> None:
        dead = [k for k, t in self._seen.items() if now - t > self.window]
        for k in dead:
            self._seen.pop(k, None)

    def size(self) -> int:
        with self._lock:
            return len(self._seen)


# ---------------- 请求 / 应答 ----------------

def _request_domain(req: dict) -> dict:
    """请求签名域。**只签头部 + 载荷指纹，不签载荷原文**：

      - 载荷可以很大（MB 级），每次 canonical 一遍是白烧 CPU；
      - 头小且固定，将来换成只传头（载荷另走分片）时时序不变；
      - 载荷的完整性由 payload_hash 保证 —— 验签后立刻比对指纹，
        对不上说明载荷在途中被改过（HTTP 明文下的 MITM）。
    """
    domain = {
        "v": V, "msg_id": req.get("msg_id"), "task_id": req.get("task_id"),
        "caller_did": req.get("caller_did"), "provider_did": req.get("provider_did"),
        "skill": req.get("skill"), "payload_hash": req.get("payload_hash"),
        "ts": req.get("ts"),
    }
    # Persistent multi-Agent nodes must bind a signature to one exact mounted
    # service. Older single-Agent sovereign messages omit this field.
    if "service_id" in req:
        domain["service_id"] = req["service_id"]
    return domain


def sign_request(identity: Identity, *, provider_did: str, skill: str,
                 payload, task_id: str | None = None,
                 service_id: str | None = None) -> dict:
    """调用方签一份调用请求。caller_did 由身份推出，不由调用方自报。"""
    req = {
        "v": V, "msg_id": new_id("call"), "task_id": task_id or new_id("t"),
        "caller_did": identity.did, "provider_did": provider_did,
        "skill": skill, "payload_hash": hash_payload(payload),
        "payload": payload, "ts": time.time(), "sent_at": now_iso(),
        "pub": pub_b64(identity.pub_raw),
    }
    if service_id is not None:
        req["service_id"] = service_id
    req["sig"] = identity.sign(_request_domain(req))
    return req


def verify_request(req: dict, *, guard: ReplayGuard | None = None,
                   now: float | None = None,
                   expected_provider: str | None = None) -> tuple[bool, str]:
    """验请求：字段、双方身份、签名、载荷完整性，最后才提交防重放 nonce。"""
    if not isinstance(req, dict):
        return False, "请求必须是 JSON 对象"
    missing = [k for k in ("msg_id", "task_id", "caller_did", "provider_did",
                           "skill", "payload_hash", "pub", "sig") if not req.get(k)]
    if missing:
        return False, "请求缺字段：" + "/".join(missing)
    try:
        pub_raw = pub_unb64(req["pub"])
    except (ValueError, TypeError):
        return False, "请求里的公钥无法解码"
    if did_from_pub(pub_raw) != req["caller_did"]:
        return False, "caller_did 与公钥指纹不符"
    if expected_provider is not None and req["provider_did"] != expected_provider:
        return False, "请求指定的供给节点不是本节点"
    if not verify_pub(pub_raw, _request_domain(req), req["sig"]):
        return False, "请求签名无效"
    # 载荷完整性：签名只覆盖了指纹，这里必须真比一次，
    # 否则"签名有效"就成了一句与载荷无关的空话。
    if hash_payload(req.get("payload")) != req["payload_hash"]:
        return False, "载荷与签名覆盖的指纹不符（途中被改过）"
    # Only a fully valid request may consume its nonce.  Otherwise a tampered
    # payload could burn the legitimate request's nonce before it arrives.
    if guard is not None:
        ok, why = guard.check(req["msg_id"], req.get("ts"), now)
        if not ok:
            return False, why
    return True, "ok"


def _control_domain(proof: dict) -> dict:
    return {key: proof.get(key) for key in (
        "v", "purpose", "msg_id", "method", "task_id", "service_id",
        "caller_did", "provider_did", "ts")}


def sign_control(identity: Identity, *, provider_did: str, service_id: str,
                 task_id: str, method: str) -> dict:
    """Authorize one query/cancel of a task previously submitted by this DID."""
    if method not in {"tasks/get", "tasks/cancel"}:
        raise ValueError("不支持的节点任务控制方法")
    proof = {"v": V, "purpose": "a2n-peer-task-control",
             "msg_id": new_id("control"), "method": method,
             "task_id": task_id, "service_id": service_id,
             "caller_did": identity.did, "provider_did": provider_did,
             "ts": time.time(), "pub": pub_b64(identity.pub_raw)}
    proof["sig"] = identity.sign(_control_domain(proof))
    return proof


def verify_control(proof: dict, *, expected_provider: str,
                   guard: ReplayGuard | None = None) -> tuple[bool, str]:
    if not isinstance(proof, dict) or set(proof) != {
            "v", "purpose", "msg_id", "method", "task_id", "service_id",
            "caller_did", "provider_did", "ts", "pub", "sig"}:
        return False, "任务控制签名结构无效"
    if (proof.get("v") != V or proof.get("purpose") != "a2n-peer-task-control"
            or proof.get("method") not in {"tasks/get", "tasks/cancel"}
            or not all(proof.get(key) for key in (
                "msg_id", "task_id", "service_id", "caller_did", "pub", "sig"))
            or not all(isinstance(proof.get(key), str) for key in (
                "msg_id", "task_id", "service_id", "caller_did",
                "provider_did", "pub", "sig"))
            or proof.get("provider_did") != expected_provider):
        return False, "任务控制签名与节点或方法不符"
    try:
        pub_raw = pub_unb64(proof["pub"])
    except (ValueError, TypeError):
        return False, "任务控制公钥无效"
    if did_from_pub(pub_raw) != proof["caller_did"]:
        return False, "任务控制 DID 与公钥不符"
    if not verify_pub(pub_raw, _control_domain(proof), proof["sig"]):
        return False, "任务控制签名无效"
    if guard is not None:
        return guard.check(proof["msg_id"], proof.get("ts"))
    return True, "ok"


def sign_response(identity: Identity, *, req: dict, state: str, result=None,
                  usage: dict | None = None, receipt: dict | None = None,
                  error: str = "") -> dict:
    body = {
        "v": V, "msg_id": req.get("msg_id"), "task_id": req.get("task_id"),
        "provider_did": identity.did, "caller_did": req.get("caller_did"),
        "skill": req.get("skill"), "state": state, "result": result,
        "output_hash": hash_payload(result) if result is not None else "",
        "usage": usage or {}, "receipt": receipt, "error": error,
        "ts": time.time(), "pub": pub_b64(identity.pub_raw),
    }
    body["sig"] = identity.sign({k: v for k, v in body.items() if k != "sig"})
    return body


def verify_response(resp: dict, *, req: dict | None = None) -> tuple[bool, str]:
    """验一份应答：签名有效 + 身份自洽 + （可选）确实是回给我这份请求的。"""
    if not isinstance(resp, dict):
        return False, "应答必须是 JSON 对象"
    for k in ("msg_id", "task_id", "provider_did", "state", "pub", "sig"):
        if not resp.get(k):
            return False, f"应答缺字段：{k}"
    try:
        pub_raw = pub_unb64(resp["pub"])
    except (ValueError, TypeError):
        return False, "应答里的公钥无法解码"
    if did_from_pub(pub_raw) != resp["provider_did"]:
        return False, "provider_did 与公钥指纹不符"
    if not verify_pub(pub_raw, {k: v for k, v in resp.items() if k != "sig"}, resp["sig"]):
        return False, "应答签名无效"
    if req is not None:
        if resp["msg_id"] != req.get("msg_id"):
            return False, "应答回的不是我这份请求"
        if resp["provider_did"] != req.get("provider_did"):
            return False, "应答方不是我打的那个节点"
    return True, "ok"


# ---------------- 客户端 ----------------

def _opener():
    """绕开系统代理：本机回环被代理拦下的症状是 502，且极难联想到代理。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_direct(url: str, req: dict, timeout: float = 20.0) -> dict:
    """把请求打到对方节点的 /a2n/call。返回应答体（未验签，由调用方验）。"""
    data = json.dumps({"request": req}, ensure_ascii=False).encode()
    r = urllib.request.Request(url.rstrip("/") + "/a2n/call", data=data,
                               headers={"Content-Type": "application/json"},
                               method="POST")
    try:
        with _opener().open(r, timeout=timeout) as f:
            return json.loads(f.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except (ValueError, OSError):
            body = {"error": f"HTTP {e.code}"}
        body["_http_status"] = e.code
        return body


def send_ack(url: str, ack: dict, timeout: float = 10.0,
             service_id: str | None = None,
             witness_claim: dict | None = None) -> bool:
    """把回执送回供给方 —— 双向各持一份带对方签名的东西，闭环才成立。"""
    body = {"ack": ack}
    if service_id is not None:
        body["service_id"] = service_id  # lookup hint; signature is still verified
    if witness_claim is not None:
        body["witness_claim"] = witness_claim
    data = json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(url.rstrip("/") + "/a2n/ack", data=data,
                               headers={"Content-Type": "application/json"},
                               method="POST")
    try:
        with _opener().open(r, timeout=timeout) as f:
            return bool(json.loads(f.read().decode()).get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return False


def fetch_card(base_url: str, timeout: float = 10.0) -> dict | None:
    """取对方的卡（A2A 标准位置：/.well-known/agent.json）。未验签，由调用方验。"""
    try:
        with _opener().open(base_url.rstrip("/") + "/.well-known/agent.json",
                            timeout=timeout) as f:
            return json.loads(f.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None


def fetch_chain(base_url: str, limit: int = 20, timeout: float = 10.0) -> dict | None:
    """取对方的本地凭证链视图（核对"它那边也记了这一笔"）。"""
    try:
        with _opener().open(f"{base_url.rstrip('/')}/a2n/receipts?limit={int(limit)}",
                            timeout=timeout) as f:
            return json.loads(f.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None


# ---------------- 服务端 ----------------

class _Handler(BaseHTTPRequestHandler):
    node = None                # 由 serve() 注入
    server_version = "a2n-node/0.1"

    def log_message(self, fmt, *args):        # 静音：节点自己决定要不要说话
        if getattr(self.node, "verbose", False):
            print(f"[{self.node.name}] {fmt % args}", flush=True)

    # ---- 工具 ----
    def _send(self, code: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n).decode())
        except (ValueError, OSError):
            return None

    # ---- 路由 ----
    def do_GET(self) -> None:                  # noqa: N802 - BaseHTTPRequestHandler 约定
        path = self.path.split("?", 1)[0]
        if path == "/.well-known/agent.json":
            return self._send(200, self.node.card)
        if path == "/a2n/health":
            return self._send(200, {"did": self.node.identity.did, "name": self.node.name,
                                    "skills": self.node.skills, "server": False})
        if path == "/a2n/receipts":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            limit = 20
            for part in q.split("&"):
                if part.startswith("limit="):
                    limit = max(1, min(200, int(part[6:] or 20)))
            return self._send(200, self.node.chain_view(limit))
        return self._send(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:                 # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/a2n/ack":
            body = self._read_json()
            if not isinstance(body, dict) or not isinstance(body.get("ack"), dict):
                return self._send(400, {"error": "请求体必须是 {\"ack\": {...}}"})
            code, out = self.node.handle_ack(body["ack"])
            return self._send(code, out)
        if path != "/a2n/call":
            return self._send(404, {"error": "not found"})
        body = self._read_json()
        if not isinstance(body, dict) or not isinstance(body.get("request"), dict):
            return self._send(400, {"error": "请求体必须是 {\"request\": {...}}"})
        code, out = self.node.handle_call(body["request"])
        return self._send(code, out)


class _NodeServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 允许两个进程悄悄绑同一个端口——那比报错糟糕得多
    # （第二个"启动成功"实际上收不到任何连接）。端口冲突必须当场失败，
    # 让 CLI 的 OSError 提示能接住；POSIX 保留 reuse 以便快速重启。
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def serve(node, host: str, port: int) -> _NodeServer:
    """把一个节点挂到它自己的 HTTP 入口上。

    ThreadingHTTPServer：一个慢调用不该把整个节点的入口堵住
    （节点是自己唯一的服务进程，没有第二副本可以顶）。
    """
    handler = type("_NodeHandler", (_Handler,), {"node": node})
    srv = _NodeServer((host, port), handler)
    t = threading.Thread(target=srv.serve_forever, name=f"a2n-node-http-{port}", daemon=True)
    t.start()
    return srv
