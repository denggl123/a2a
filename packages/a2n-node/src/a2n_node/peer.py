"""节点请求与任务控制的签名、反重放，以及双边回执传送。HTTP 服务仅由统一节点网关提供。"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request

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


def send_ack(url: str, ack: dict, timeout: float = 10.0,
             service_id: str | None = None,
             witness_claim: dict | None = None,
             feedback: dict | None = None) -> bool:
    """把回执送回供给方 —— 双向各持一份带对方签名的东西，闭环才成立。

    只关心"送到没有"的调用方用这个；要拿供给方随响应捎回的反馈（R2）用
    `send_ack_result`。
    """
    return bool(send_ack_result(url, ack, timeout=timeout, service_id=service_id,
                                witness_claim=witness_claim, feedback=feedback))


def send_ack_result(url: str, ack: dict, timeout: float = 10.0,
                    service_id: str | None = None,
                    witness_claim: dict | None = None,
                    feedback: dict | None = None) -> dict | None:
    """同 `send_ack`，但返回供给方响应体（含随响应捎带的 `feedback`）。

    `feedback`（可选）= 调用方要随回执捎给供给方的 `buyer_to_seller` 反馈对象
    （R2，FEEDBACK-API §4）。失败回 `None`（不假装送达、不吞成空成功）。
    """
    body = {"ack": ack}
    if service_id is not None:
        body["service_id"] = service_id  # lookup hint; signature is still verified
    if witness_claim is not None:
        body["witness_claim"] = witness_claim
    if feedback is not None:
        body["feedback"] = feedback
    data = json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(url.rstrip("/") + "/a2n/ack", data=data,
                               headers={"Content-Type": "application/json"},
                               method="POST")
    try:
        with _opener().open(r, timeout=timeout) as f:
            return json.loads(f.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None
