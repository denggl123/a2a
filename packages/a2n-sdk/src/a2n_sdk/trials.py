"""试用期与样品：把"前 N 次免费服务"变成 Agent 的**真实履历**。

为什么需要它（`docs/VISION.md` §1.2 主张 #12「样品即履历」/ §6.4）
-----------------------------------------------------------------
Agent 的展示有两半：**描述**与**案例**。而"案例"不能是卖方挑出来的宣传稿，
只能是**它真实交付过的样子**。所以：一个供给最初 N（默认 10）次"完成调用"
既是免费的、也**默认沉淀为公开样品** —— 履历不是写出来的，是干出来的。

边界（写死在数据里，与 §6.4 对齐）
  · **样品锚定具体调用与版本**：一条样品 = 一次(task_id)交付 + 当时的 Agent 版本；
    卖方**删不掉**不理想的、也**不许**拿旧样品冒充新版本表现（版本随样品一起存）。
  · **公开的是可公开投影**：移除密钥、身份信息、业务隐私；移掉的键记进 `redactions`，
    不能安全公开时保留样品位置并给出 `hidden_reason` —— 但不许借"隐私"之名挑好评。
  · **失败不占名额**：技术失败 / 超时 / 取消**不**计完成次数，样品也不会凭空生成；
    同一 task 重试、轮询、重复回调**只计一次**（靠 `MARK_NS` 幂等键）。
  · **计数按供给、不按买家**：一个供给的前 N 次完成调用免费，不是"每个买家各 N 次"。

与旧 `a2n_registry.trial`（平台模式、按库计）的关系：那条跑在 `a2n_store` 上、
带毕业接口与建单收费判断，属**兼容通道**；本条跑在节点自己的 `LocalStore` 上，
只做"计数 + 样品"，是**自持模式**的口径，两者不共用一张表。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

TRIAL_NS = "trials"          # service_id -> 计数
MARK_NS = "trial_calls"      # service_id::task_id -> 幂等标记（这一单算过没有）
SAMPLE_NS = "samples"        # service_id::task_id -> 公开样品

DEFAULT_CAP = 10
# 只有真正交付成功才计完成次数（技术失败/超时/取消不占名额）。
DONE_STATES = {"COMPLETED"}

_SUMMARY_MAX = 120
_PREVIEW_MAX = 2000

_SECRET_SUBSTR = ("password", "passwd", "secret", "token", "apikey", "api_key",
                  "access_key", "private_key", "credential", "authorization",
                  "cookie", "email", "phone", "mobile", "ssn", "idcard",
                  "id_card", "passport",
                  "密码", "密钥", "令牌", "身份证", "手机号", "邮箱",
                  "私钥", "签名")
_SECRET_EXACT = {"sig", "key", "auth", "pwd", "secret", "sk", "pk"}
_REDACTED = "[已隐去]"


def _now() -> float:
    import time
    return time.time()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _is_secret(key: Any) -> bool:
    text = str(key or "").lower()
    return text in _SECRET_EXACT or any(part in text for part in _SECRET_SUBSTR)


def _redact(value: Any, removed: list) -> Any:
    """递归投影：隐去疑似密钥/隐私的**键**（连值一起），并记下移掉了什么。

    只按 **键名** 判定，不去猜自由文本里的内容 —— 猜错会把正常成品也删掉。
    真需要更强判断时由调用方在 `hidden_reason` 里如实说明，而不是静默隐藏。
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _is_secret(key):
                out[str(key)] = _REDACTED
                removed.append(str(key))
            else:
                out[str(key)] = _redact(item, removed)
        return out
    if isinstance(value, list):
        return [_redact(v, removed) for v in value]
    return value


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "…（截断）"


def summarize(payload: Any) -> str:
    """从买方输入里取一段**需求摘要**（不复制全文，够看懂要什么即可）。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return _clip(payload.strip(), _SUMMARY_MAX)
    if isinstance(payload, dict):
        for key in ("text", "topic", "prompt", "query", "requirement", "需求"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return _clip(val.strip(), _SUMMARY_MAX)
    return _clip(payload, _SUMMARY_MAX)


def _digest(core: dict) -> str:
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TrialBook:
    """节点本地的"试用期 + 样品"账本。跑在节点自己的 LocalStore 上。"""

    def __init__(self, store, *, cap: int = DEFAULT_CAP):
        self.store = store
        self.cap = max(1, int(cap))

    # ---------------- 写：一次完成交付 ----------------

    def record(self, service_id: str, task_id: str, outcome: Any, *,
               request: Any = None, version: str = "",
               at: float | None = None) -> dict[str, Any] | None:
        """记一次交付。完成才计名额；前 cap 次免费并生成样品。返回样品或 None。

        `outcome` 可以是 CallOutcome 或它的 dict；`request` 是本次调用的请求
        （CallRequest 或 dict，用来生成需求摘要）。幂等：同一 (供给, 任务) 只算一次。
        """
        sid = str(service_id or "").strip()
        tid = str(task_id or "").strip()
        if not sid or not tid:
            raise ValueError("记录交付必须给出 service_id 与 task_id")
        data = self._as_dict(outcome)
        state = str(data.get("state") or "").upper()
        ok = bool(data.get("ok", True))
        if state not in DONE_STATES or not ok:
            return None  # 失败/未完成不占名额，也不生成样品

        key = f"{sid}::{tid}"
        if self.store.get(MARK_NS, key):
            return self.sample(sid, tid)  # 已经算过：不重复计数

        ts = at if at is not None else _now()
        trial = self._trial(sid)
        free = trial["completed"] < self.cap
        trial["completed"] += 1
        trial["first_at"] = trial.get("first_at") or _iso(ts)
        trial["last_at"] = _iso(ts)
        if version:
            trial["version"] = str(version)
        self.store.put(MARK_NS, key, {"service_id": sid, "task_id": tid,
                                      "free": free, "at": ts})
        self.store.put(TRIAL_NS, sid, trial)

        if not free:
            return None  # 名额外的完成只计数，不再进样品（首批样品固定前 cap 次）

        removed: list = []
        summary = summarize(self._payload(request))
        result = data.get("result")
        preview_value = _redact(result, removed) if result is not None else None
        preview = _clip(preview_value, _PREVIEW_MAX) if preview_value is not None else ""
        hidden_reason = "" if preview else "这次交付没有可公开的内容"
        core = {
            "id": "sp_" + hashlib.sha256(f"{sid}::{tid}".encode()).hexdigest()[:16],
            "service_id": sid, "task_id": tid,
            "version": str(version or trial.get("version") or ""),
            "at": _iso(ts), "free": True,
            "summary": summary, "preview": preview,
            "redactions": sorted(set(removed)), "hidden_reason": hidden_reason,
            "kind": "real_delivery_sample_not_promotion",
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
        }

    def samples(self, service_id: str, limit: int = 20) -> list[dict]:
        sid = str(service_id or "")
        rows = [s for s in self.store.items(SAMPLE_NS).values()
                if s.get("service_id") == sid]
        rows.sort(key=lambda s: str(s.get("at") or ""))
        return rows[:max(1, min(int(limit), 100))]

    def sample(self, service_id: str, task_id: str) -> dict | None:
        return self.store.get(SAMPLE_NS, f"{service_id}::{task_id}")

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

    NOTICE = ("本供给处于免费试用期：前 {cap} 次完成调用不计费，"
              "其交付内容默认形成公开样品（可公开投影，隐去密钥与隐私）。")

    def notice(self, service_id: str) -> str:
        return self.NOTICE.format(cap=self.cap)

    def verify(self, sample: dict) -> bool:
        """样品指纹自检：内容有没有被改过（删标题、换预览都会露馅）。"""
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
