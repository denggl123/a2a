"""试用期与样品：把"前 N 次免费服务"变成 Agent 的**真实履历**。

为什么需要它（`docs/VISION.md` §1.2 主张 #12「样品即履历」/ §6.4）
-----------------------------------------------------------------
Agent 的展示有两半：**描述**与**案例**。而"案例"不能是卖方挑出来的宣传稿，
只能是**它真实交付过的样子**。所以：一个供给最初 N（默认 10）次"完成调用"
既是免费的、也**一定公开为样品** —— 履历不是写出来的，是干出来的。

**样品一定公开**（用户裁决 2026-10-06）
------------------------------------
"前 10 次免费"与"一定公开"是同一件事的两面：既然这 10 次不收钱，它就是拿真实交付
换来的履历，**没有不公开的选项**。所以这里不设"双方安全公开声明"这类前置门禁 ——
设了就等于允许"这 10 次白送但什么都不留下"，那免费就变成了白做，履历链条断掉。

公开的边界仍然在，只是它**不是"要不要公开"，而是"公开什么"**：
  · **脱敏照旧**：密钥、身份、邮箱电话等过一遍 `privacy`（`redactions` 记录移走了什么）。
    公开的是**可公开投影**，不是把买方原文和凭据贴出去。
  · **媒体占位照旧**：原始文件不直接公开；公开预览只由节点自己的有界图像编码器生成
    （`media_preview`），Agent 自带的 base64/URL 一律不作为公开预览。
  · **失败不占名额**：技术失败 / 超时 / 取消**不**计完成次数，样品也不会凭空生成；
    同一 task 重试、轮询、重复回调**只计一次**（靠 `MARK_NS` 幂等键）。
  · **计数按供给、不按买家**：一个供给的前 N 次完成调用免费，不是"每个买家各 N 次"。

本模块使用节点自己的加密 LocalStore，是唯一业务实现。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

TRIAL_NS = "trials"          # service_id -> 计数
MARK_NS = "trial_calls"      # service_id::task_id -> 幂等标记（这一单算过没有）
SAMPLE_NS = "samples"        # service_id::task_id -> 公开样品

DEFAULT_CAP = 10
# 只有真正交付成功才计完成次数（技术失败/超时/取消不占名额）。
DONE_STATES = {"COMPLETED"}

_SUMMARY_MAX = 120
_PREVIEW_MAX = 2000

from .privacy import scrub_text as _scrub_text, sample_projection, public_sample_media,public_sample_documents
from .trade_facts import delivered, axes

ADMISSION_NS = "trial_admissions"


def _now() -> float:
    import time
    return time.time()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "…（截断）"


def summarize(payload: Any, removed: list | None = None) -> str:
    """从买方输入里取一段**需求摘要**（不复制全文，够看懂要什么即可）。

    摘要也可能带邮箱/电话/密钥（"发给 a@b.com 的那种海报"），所以同样过一遍
    内容脱敏；命中的类别追加进 `removed`（调用方据此写进 `redactions`）。
    """
    bucket = removed if removed is not None else []
    payload, _ = sample_projection(payload, bucket)
    if payload is None:
        text = ""
    elif isinstance(payload, str):
        text = _clip(payload.strip(), _SUMMARY_MAX)
    elif isinstance(payload, dict):
        text = ""
        for key in ("text", "topic", "prompt", "query", "requirement", "需求"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                text = _clip(val.strip(), _SUMMARY_MAX)
                break
        else:
            text = _clip(payload, _SUMMARY_MAX)
    else:
        text = _clip(payload, _SUMMARY_MAX)
    return _scrub_text(text, bucket)


def _digest(core: dict) -> str:
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TrialBook:
    """节点本地的"试用期 + 样品"账本。跑在节点自己的 LocalStore 上。"""

    def __init__(self, store, *, cap: int = DEFAULT_CAP):
        self.store = store
        self.cap = max(1, int(cap))
        self.reconnect = None
        self._public_salt = self.store.get("sample_settings", "public_id_salt")
        if not self._public_salt:
            self._public_salt = secrets.token_hex(32)
            self.store.put("sample_settings", "public_id_salt", self._public_salt)

    def admit(self, service_id: str, task_id: str, *, caller_did="", provider_did="",
              voluntary_free=False, max_pending=2, rework_free=False) -> dict:
        """Freeze free eligibility before execution, with bounded outstanding work."""
        key = f"{service_id}::{task_id}"
        with self.store.tx():
            existing = self.store.get(ADMISSION_NS, key)
            if existing:
                return existing
            initial = self._trial(service_id)["completed"] < self.cap
            reconnect = not initial and not rework_free and self.reconnect and self.reconnect.available(service_id)
            free = bool(initial or reconnect or voluntary_free or rework_free)
            pending = sum(1 for row in self.store.items(ADMISSION_NS).values()
                          if row.get("service_id") == service_id and row.get("active")
                          and row.get("free"))
            if free and pending >= max_pending:
                raise ValueError("CAPACITY_LIMIT: 免费在途任务已达到上限")
            row = {"service_id": service_id, "task_id": task_id, "at": _now(),
                   "free": free, "free_reason": "FREE_REWORK" if rework_free else "FREE_INITIAL" if initial else
                   "FREE_RECONNECT" if reconnect else "FREE_VOLUNTARY" if voluntary_free else "UNSPECIFIED",
                   "active": True, "caller_did": caller_did, "provider_did": provider_did,
                   "source_kind": "SELLER_SELF" if caller_did and caller_did == provider_did
                   else "NODE_TRADE" if caller_did else "UNVERIFIED_EXTERNAL"}
            if reconnect:
                row["reconnect_grant_id"] = self.reconnect.reserve(service_id)
            self.store.put(ADMISSION_NS, key, row)
            return row

    def finish_admission(self, service_id, task_id, data):
        key = f"{service_id}::{task_id}"
        with self.store.tx():
            row = self.store.get(ADMISSION_NS, key)
            if row and row.get("active") and axes(data)["execution"] in {"DELIVERED", "FAILED", "CANCELED"}:
                if self.reconnect:
                    self.reconnect.finish(row, data)
                self.store.put(ADMISSION_NS, key, {**row, "active": False,
                                                  "execution": axes(data)["execution"]})

    # ---------------- 写：一次完成交付 ----------------

    def record(self, service_id: str, task_id: str, outcome: Any, *,
               request: Any = None, version: str = "",
               at: float | None = None) -> dict[str, Any] | None:
        """记一次交付。完成才计名额；前 cap 次免费并生成样品。返回样品或 None。

        `outcome` 可以是 CallOutcome 或它的 dict；`request` 是本次调用的请求
        （CallRequest 或 dict，用来生成需求摘要）。幂等：同一 (供给, 任务) 只算一次。

        **计数与样品必须一起可靠落库**：整个"幂等检查 → 计数 +1 → 写样品"跑在
        `store.tx()` 里（BEGIN IMMEDIATE）。以前分三次独立 `put`，两次并发完成会
        各自读到同一份旧计数再一起写回（丢一次），中途写样品失败更会留下"次数已增、
        样品缺失、重试也补不回"的半截账。口径：宁可偶发多送一次免费，也不让履历记错。
        """
        sid = str(service_id or "").strip()
        tid = str(task_id or "").strip()
        if not sid or not tid:
            raise ValueError("记录交付必须给出 service_id 与 task_id")
        data = self._as_dict(outcome)
        if not delivered(data):
            return None  # 失败/未完成不占名额，也不生成样品

        tx_factory = getattr(self.store, "tx", None)
        if not callable(tx_factory):
            # 没有事务能力的 store（测试替身）退化为顺序写，行为不变。
            return self._record(sid, tid, data, request=request,
                                version=version, at=at)
        with tx_factory():
            return self._record(sid, tid, data, request=request,
                                version=version, at=at)

    def _record(self, sid: str, tid: str, data: dict, *, request: Any,
                version: str, at: float | None) -> dict[str, Any] | None:
        key = f"{sid}::{tid}"
        if self.store.get(MARK_NS, key):
            return self.sample(sid, tid)  # 已经算过：不重复计数

        ts = at if at is not None else _now()
        trial = self._trial(sid)
        initial_sample = trial["completed"] < self.cap
        admission = self.store.get(ADMISSION_NS, key) or {}
        free = admission.get("free", initial_sample)
        trial["completed"] += 1
        trial["first_at"] = trial.get("first_at") or _iso(ts)
        trial["last_at"] = _iso(ts)
        if version:
            trial["version"] = str(version)
        self.store.put(MARK_NS, key, {"service_id": sid, "task_id": tid,
                                      "free": free, "free_reason": admission.get("free_reason", "FREE_INITIAL" if initial_sample else "UNSPECIFIED"), "at": ts})
        self.store.put(TRIAL_NS, sid, trial)

        supplemental = not initial_sample and admission.get("free_reason") in {"FREE_INITIAL", "FREE_RECONNECT"}
        if not initial_sample and not supplemental:
            return None  # 名额外的完成只计数，不再进样品（首批样品固定前 cap 次）

        removed: list = []
        request_data = request if isinstance(request, dict) else {"metadata": getattr(request, "metadata", {})}
        # 样品**一定公开**（用户裁决 2026-10-06）：前 cap 次免费交付就是履历，
        # 不设"是否同意公开"的前置门禁。仍然要过的是**脱敏**——公开的是可公开投影，
        # 不是买方原文和凭据。`consent` 只作为"调用方主动声明"记进事实，不参与判定。
        request_metadata = request_data.get("metadata")
        request_metadata = request_metadata if isinstance(request_metadata, dict) else {}
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        consent = request_metadata.get("a2nSampleConsent")
        consent = consent if isinstance(consent, dict) else {}
        policy = metadata.get("sample_policy")
        policy = policy if isinstance(policy, dict) else {}
        summary = summarize(self._payload(request), removed)
        result = data.get("result")
        # 媒体交付：公开预览走 `media_preview`（节点有界编码器重编码的缩略图），
        # 原始交付的句柄、URL 与字节不进 preview；列表和嵌套字段执行相同规则。
        preview_value, media_placeholder = sample_projection(result, removed)
        preview = _clip(preview_value, _PREVIEW_MAX) if preview_value is not None else ""
        hidden_reason = "" if preview else "这次交付没有可公开的内容"
        if media_placeholder:
            hidden_reason = "媒体占位：原始文件保留在私有交付中，公开预览需独立处理"
        # Only the node's bounded image encoder supplies this field. Raw media
        # URLs, paths or arbitrary Agent-provided base64 are never public previews.
        media_preview = public_sample_media(metadata.get("sample_media"))
        document_previews=public_sample_documents(metadata.get('sample_documents'),removed)
        core = {
            "id": "sp_" + secrets.token_hex(16),
            "service_id": sid, "task_id": tid,
            "version": str(version or trial.get("version") or ""),
            "at": _iso(ts), "free": bool(free),
            "summary": summary, "preview": preview,
            "redactions": sorted(set(removed)), "hidden_reason": hidden_reason,
            "kind": "real_delivery_sample_not_promotion",
            "v": 4, "slot": trial["completed"] if initial_sample else None,
            "sample_group": "INITIAL" if initial_sample else "RECONNECT" if admission.get("free_reason") == "FREE_RECONNECT" else "INITIAL_OVERFLOW",
            "reconnect_grant_id": admission.get("reconnect_grant_id", ""),
            "publication_policy": "ALWAYS_PUBLIC" if not media_placeholder else "PLACEHOLDER",
            "source_kind": admission.get("source_kind", "UNVERIFIED_EXTERNAL"),
            "free_reason": admission.get("free_reason", "FREE_INITIAL"),
            "quality": axes(data)["quality"],
            "media_preview": media_preview,
            "document_previews":document_previews,
            "buyer_declared_public": bool(isinstance(consent, dict)
                                         and consent.get("input_public") is True
                                         and consent.get("output_public") is True),
            "seller_declared_safe_output": policy.get("safe_output") is True,
        }
        sample = {**core, "digest": _digest(core)}
        self.store.put(SAMPLE_NS, key, sample)
        return sample

    # ---------------- 读 ----------------

    def trial(self, service_id: str) -> dict:
        return self._trial(str(service_id or ""))

    def status(self, service_id: str) -> dict:
        """给控制台/卡片看的试用进度（不合成质量结论，只报事实计数）。"""
        t = self._trial(str(service_id or ""))
        cap = t["cap"]
        completed = t["completed"]
        used = min(completed, cap)
        return {
            "service_id": t["service_id"], "cap": cap, "completed": completed,
            "used": used, "remaining": max(0, cap - completed),
            "ended": completed >= cap, "version": t.get("version", ""),
            "first_at": t.get("first_at", ""), "last_at": t.get("last_at", ""),
            "notice": self.notice(service_id),
            "reconnect": self.reconnect.summary(service_id) if self.reconnect else None,
            "historical_gap_count": sum(1 for r in self.store.items("historical_sample_gaps").values() if r.get("service_id") == service_id),
        }

    def samples(self, service_id: str, limit: int = 20) -> list[dict]:
        sid = str(service_id or "")
        rows = [s for s in self.store.items(SAMPLE_NS).values()
                if s.get("service_id") == sid]
        rows.sort(key=lambda s: str(s.get("at") or ""))
        return rows[:max(1, min(int(limit), 100))]

    def sample(self, service_id: str, task_id: str) -> dict | None:
        return self.store.get(SAMPLE_NS, f"{service_id}::{task_id}")

    def public_identifier(self, sample):
        import hmac
        return "ps_" + hmac.new(bytes.fromhex(self._public_salt), str(sample["id"]).encode(), hashlib.sha256).hexdigest()[:32]

    def all_for(self, service_ids) -> dict:
        """一次给出多个供给的 `{status, samples}`（控制台详情用）。"""
        return {sid: {"status": self.status(sid), "samples": self.samples(sid)}
                for sid in service_ids}

    def counts(self) -> dict:
        trials = list(self.store.items(TRIAL_NS).values())
        samples = list(self.store.items(SAMPLE_NS).values())
        return {
            "supplies": len(trials),
            "samples": len(samples),
            "in_trial": sum(1 for t in trials if t.get("completed", 0) < t.get("cap", self.cap)),
            "graduated": sum(1 for t in trials if t.get("completed", 0) >= t.get("cap", self.cap)),
        }

    # ---------------- 调用前声明（§6.4：不能调用后才知道） ----------------

    NOTICE = ("初始采样的前 {cap} 次技术交付不计费，且**一定公开为样品**"
              "（公开的是脱敏后的可公开投影；原始文件不进公开预览）。")

    def notice(self, service_id: str) -> str:
        if self._trial(service_id).get("completed", 0) >= self.cap:
            return ("初始免费采样已结束，首批样品保留；后续调用按照当前明确的免费、付费条件或有效重连额度接单。")
        return self.NOTICE.format(cap=self.cap)

    def verify(self, sample: dict) -> bool:
        """样品指纹自检：内容有没有被改过（删标题、换预览都会露馅）。"""
        if sample.get("v") in {2, 3, 4}:
            return _digest({k: v for k, v in sample.items() if k != "digest"}) == sample.get("digest")
        core = {k: sample.get(k) for k in
                ("id", "service_id", "task_id", "version", "at", "free",
                 "summary", "preview", "redactions", "hidden_reason", "kind")}
        return _digest(core) == sample.get("digest")

    # ---------------- 内部 ----------------

    def _trial(self, service_id: str) -> dict:
        row = self.store.get(TRIAL_NS, service_id)
        if not row:
            return {"service_id": service_id, "cap": self.cap, "completed": 0}
        row = dict(row)
        row.setdefault("cap", self.cap)
        row.setdefault("completed", 0)
        return row

    @staticmethod
    def _payload(request: Any) -> Any:
        if request is None:
            return None
        if isinstance(request, dict):
            return request.get("payload")
        return getattr(request, "payload", None)

    @staticmethod
    def _as_dict(outcome: Any) -> dict:
        if isinstance(outcome, dict):
            return outcome
        to_dict = getattr(outcome, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        raise TypeError("outcome 必须是 CallOutcome 或其 dict")
