"""双方反馈账本（R2，自持模式）：一笔任务之后，买卖双方各留一份可核验的反馈。

口径与边界（`docs/FEEDBACK-RULES.md` v0.1 / `docs/FEEDBACK-API.md` a2n-feedback/1）
---------------------------------------------------------------------------
  · **身份 = 节点身份**：一个 did = 一个节点。作者用本机 ed25519 身份签名 ——
    签名/验签**由节点装配注入**（`signer` / `verifier`），本模块不直接依赖
    `a2n_p2p`（守住 SDK "零第三方依赖" 的边界）。不引入账户层身份、不引入多身份。
  · **双向**：买方评供给（交付质量 / 按时 / 沟通），供给评买方（需求按约 / 配合）。
  · **状态硬闸**：`quality`（交付质量）**只允许对已交付**（COMPLETED/ACCEPTED/SETTLED）的任务打；
    失败 / 取消 / 结果未知的单只能评可达性与体验 —— 网络失败不许被读成"质量差"。
  · **每方向一份当前有效反馈**；修改 = 新 `revision`（`prev_hash` 链），旧版只读不删。
    重复提交同一份内容幂等返回，不改版本，也不靠反复提交放大影响。
  · **没有调用关系不能评**：本机没有这条任务记录就不许开评（与 disputes 同一纪律）。
  · **R2 不算分、不改排序、不自动惩罚**（那是 R3）；反馈**不写账本** —— 与结算、
    与「这单我不认」三条线互不影响，反馈操作不产生任何资金变动。
  · 收到的对方反馈**验签后原样留存**：验签失败只留痕、标 `verified=false`，**不采信**
    也不静默丢弃。未评价保持「未评价」：不算好评，也不阻塞调用、试用完成或结账。

本模块使用节点自己的加密 LocalStore，是唯一业务实现。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Callable

from .privacy import scrub_text as _scrub_text  # 自由文本脱敏唯一源（邮箱/手机号/密钥/私钥）

NAMESPACE = "feedback"              # feedback_id -> 当前有效版本
VERSION_NS = "feedback_versions"    # feedback_id::revision -> 不可变版本（版本链）
INDEX_NS = "feedback_index"         # task_id::direction::author_did -> feedback_id
INBOX_NS = "feedback_received"      # author::task::direction -> 对方签名反馈（最新）
INBOX_VER_NS = "feedback_received_versions"  # 同上 + ::revision -> 每版原样留存

DIRECTIONS = ("buyer_to_seller", "seller_to_buyer")
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "buyer_to_seller": ("quality", "punctual", "communication"),
    "seller_to_buyer": ("on_spec", "cooperative"),
}
# 交付质量只能对**已交付**的任务打：失败/取消/结果未知不许被读成"质量差"。
QUALITY_DIMENSIONS = frozenset({"quality"})
DELIVERED_STATES = frozenset({"COMPLETED", "ACCEPTED", "SETTLED"})
MAX_NOTE = 500
KIND = "bilateral_feedback_not_reputation_score"

# 参与签名与内容指纹的字段（`proof` 之外的完整原对象）。
CORE_KEYS = ("v", "feedback_id", "task_id", "direction", "author_did",
             "counterparty_did", "provider_did", "service_id", "task_state",
             "dimensions", "note", "redactions", "source",
             "created_at", "at", "revision", "prev_hash")


def _now() -> float:
    import time
    return time.time()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _core(record: dict) -> dict:
    return {k: record.get(k) for k in CORE_KEYS}


def _digest(core: dict) -> str:
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class FeedbackBook:
    """节点本地的双方反馈账本。跑在节点自己的 LocalStore 上。

    `signer(core) -> {"pub": ..., "sig": ...}` 与 `verifier(proof, core) -> bool`
    由节点装配注入（a2n_node 侧持 ed25519 身份）。测试可注入确定性替身；
    不注入时反馈照常记录，只是 `proof=None`（界面对此勿谎报"已签名"）。
    """

    def __init__(self, store, *, signer: Callable[[dict], dict | None] | None = None,
                 verifier: Callable[[dict | None, dict], bool] | None = None,
                 now: Callable[[], float] | None = None):
        self.store = store
        self._sign = signer
        self._verify = verifier
        self._now = now or _now

    # ---------------- 写：本方反馈 ----------------

    def open(self, *, scope: str, task_id: str, direction: str, dimensions: Any = None,
             note: Any = "", author_did: str = "", counterparty_did: str = "",
             provider_did: str = "", service_id: str = "", task_state: str = "",
             at: float | None = None) -> dict[str, Any]:
        """留下本方对这笔任务的一份反馈（该方向首个版本）。"""
        tid = str(task_id or "").strip()
        if not tid:
            raise ValueError("必须指明是哪一笔任务（task_id）")
        d = str(direction or "").strip()
        if d not in DIRECTIONS:
            raise ValueError(f"反馈方向只能是 {' / '.join(DIRECTIONS)}")
        row = self.store.task(str(scope or ""), tid)
        if not row:
            raise ValueError("本机没有这条调用记录，无法对它写反馈")
        state = str(task_state or row.get("state") or "").upper()
        dims = self._clean_dimensions(d, dimensions)
        text, redactions = self._clean_note(note)
        if not dims and not text:
            raise ValueError("反馈至少要有维度分或一句原因，空反馈没有意义")
        if any(k in QUALITY_DIMENSIONS for k in dims) and state not in DELIVERED_STATES:
            raise ValueError("这一单没有完成交付，不能评价成品质量；"
                             "只能评可达性与沟通体验")

        author = str(author_did or "").strip()
        key = self._index_key(tid, d, author)
        existing_id = self.store.get(INDEX_NS, key)
        if existing_id:
            current = self.get(str(existing_id))
            if current and self._same_content(current, dims, text):
                return {**current, "idempotent": True}
            raise ValueError("这个方向已经有一份反馈了；要修改请用「改反馈」")

        ts = float(at if at is not None else self._now())
        fid = "fb_" + secrets.token_hex(8)
        record = self._build(
            feedback_id=fid, task_id=tid, direction=d, author_did=author,
            counterparty_did=str(counterparty_did or ""),
            provider_did=str(provider_did or ""), service_id=str(service_id or ""),
            task_state=state, dimensions=dims, note=text, redactions=redactions,
            source="self", created_at=_iso(ts), at=ts, revision=1, prev_hash="")
        with self._tx():
            self._save(record)
            self.store.put(INDEX_NS, key, fid)
        return record

    def revise(self, *, feedback_id: str, dimensions: Any = None, note: Any = None,
               at: float | None = None) -> dict[str, Any]:
        """修改已有反馈：新版本 `revision+1`，`prev_hash` 链上旧版；旧版只读不删。"""
        current = self.get(feedback_id)
        if not current:
            raise ValueError("没有这份反馈")
        d = str(current.get("direction") or "")
        state = str(current.get("task_state") or "").upper()
        dims = self._clean_dimensions(
            d, current.get("dimensions") if dimensions is None else dimensions)
        text, redactions = self._clean_note(
            current.get("note") if note is None else note)
        if not dims and not text:
            raise ValueError("反馈至少要有维度分或一句原因，空反馈没有意义")
        if any(k in QUALITY_DIMENSIONS for k in dims) and state not in DELIVERED_STATES:
            raise ValueError("这一单没有完成交付，不能评价成品质量")
        if self._same_content(current, dims, text):
            return {**current, "unchanged": True}

        ts = float(at if at is not None else self._now())
        record = self._build(
            feedback_id=str(current["feedback_id"]), task_id=current["task_id"],
            direction=d, author_did=current.get("author_did", ""),
            counterparty_did=current.get("counterparty_did", ""),
            provider_did=current.get("provider_did", ""),
            service_id=current.get("service_id", ""), task_state=state,
            dimensions=dims, note=text, redactions=redactions, source="self",
            created_at=str(current.get("created_at") or _iso(ts)), at=ts,
            revision=int(current.get("revision") or 1) + 1,
            prev_hash=str(current.get("digest") or ""))
        with self._tx():
            self._save(record)
        return record

    # ---------------- 写：收到对方的反馈（验签后原样留存） ----------------

    def ingest(self, signed: Any, *, at: float | None = None) -> dict[str, Any]:
        """收下对方捎带的签名反馈。

        **验签通过才算采信**（`verified=true`）；失败只留痕并标 `verified=false`，
        既不静默丢弃、也不混入有效统计。对方可以改自己的反馈：新版按 revision 覆盖
        最新视图，每版原样保存在版本命名空间里。
        """
        if not isinstance(signed, dict):
            raise ValueError("反馈必须是对象")
        d = str(signed.get("direction") or "")
        if d not in DIRECTIONS:
            raise ValueError(f"反馈方向只能是 {' / '.join(DIRECTIONS)}")
        tid = str(signed.get("task_id") or "").strip()
        author = str(signed.get("author_did") or "").strip()
        if not tid or not author:
            raise ValueError("反馈缺少 task_id 或 author_did")
        core = _core(signed)
        verified = False
        if callable(self._verify):
            try:
                verified = bool(self._verify(signed.get("proof"), core))
            except Exception:  # noqa: BLE001 - 脏输入不许把接收端拖垮
                verified = False
        subject = (signed.get("provider_did") if d == "buyer_to_seller"
                   else signed.get("counterparty_did"))
        ts = float(at if at is not None else self._now())
        revision = int(signed.get("revision") or 1)
        record = {**signed, "source": "counterparty",
                  "self_source": bool(subject) and str(subject) == author,
                  "verified": verified, "received_at": _iso(ts)}
        key = self._inbox_key(author, tid, d)
        existing = self.store.get(INBOX_NS, key)
        if existing and int(existing.get("revision") or 0) >= revision:
            if _core(existing) == core and existing.get("verified") == verified:
                return {**existing, "idempotent": True}
            if int(existing.get("revision") or 0) == revision:
                # 同版本不同内容：对方在改同一版 —— 留最新但如实标出冲突。
                record = {**record, "conflict": True}
        with self._tx():
            self.store.put(INBOX_VER_NS, f"{key}::{revision}", record)
            self.store.put(INBOX_NS, key, record)
        return record

    # ---------------- 读 ----------------

    def get(self, feedback_id: str) -> dict | None:
        return self.store.get(NAMESPACE, str(feedback_id or ""))

    def versions(self, feedback_id: str) -> list[dict]:
        fid = str(feedback_id or "")
        rows = [r for key, r in self.store.items(VERSION_NS).items()
                if str(key).startswith(fid + "::")]
        rows.sort(key=lambda r: int(r.get("revision") or 0))
        return rows

    def list(self, *, task_id: str | None = None, direction: str | None = None,
             counterparty: str | None = None, source: str | None = None,
             limit: int = 50) -> list[dict]:
        """本方写的 + 收到的，按时间倒序（界面一个列表看全）。最多 200 条。"""
        return self._all(task_id=task_id, direction=direction, counterparty=counterparty,
                         source=source)[:max(1, min(int(limit), 200))]

    def _all(self, *, task_id: str | None = None, direction: str | None = None,
             counterparty: str | None = None, source: str | None = None) -> list[dict]:
        """过滤 + 排序后的**全部**行（不分页）；`list` / `page` 共用，避免两处口径漂移。"""
        rows = [*self.store.items(NAMESPACE).values(),
                *self.store.items(INBOX_NS).values()]
        if task_id:
            rows = [r for r in rows if str(r.get("task_id")) == str(task_id)]
        if direction:
            rows = [r for r in rows if r.get("direction") == direction]
        if counterparty:
            rows = [r for r in rows if self._about(r, counterparty)]
        if source:
            rows = [r for r in rows if r.get("source") == source]
        rows.sort(key=lambda r: str(r.get("at") or r.get("received_at") or ""),
                  reverse=True)
        return rows

    def view(self, record: dict) -> dict:
        """`FeedbackView`（FEEDBACK-API §2）：签名反馈 + `verified` / `self_source`
        两个展示层标记，**键始终存在**，调用方不必猜缺键的含义。

        * 收到的（`source=counterparty`）：`verified` 忠实回显 `ingest` 的验签结果；
        * 本机自写的（`source=self`）：没有"对方签名"可验，`verified` 取**本机自检**
          （内容指纹 + 自己的签名），即"这份记录自身没坏"，不是"别人认过"。
        """
        rec = dict(record)
        if rec.get("source") == "counterparty":
            rec["verified"] = bool(rec.get("verified"))
        else:
            rec["verified"] = bool(self.verify_record(record))
        rec["self_source"] = bool(rec.get("self_source"))
        return rec

    def page(self, *, task_id: str | None = None, direction: str | None = None,
             counterparty: str | None = None, source: str | None = None,
             limit: int = 50, cursor: str = "") -> dict:
        """只读分页（`GET /v1/feedback`）：返回 FeedbackView[] + `next_cursor`。

        `cursor` 是不透明串（本实现里是 base64 的偏移量），空串=第一页；
        `next_cursor` 为空表示没有更多。**只读，不产生任何账本变动。**
        """
        step = max(1, min(int(limit), 200))
        offset = 0
        if cursor:
            try:
                offset = max(0, int(base64.urlsafe_b64decode(
                    cursor + "=" * (-len(cursor) % 4)).decode()))
            except (ValueError, TypeError, UnicodeDecodeError):
                offset = 0  # 坏游标按第一页处理，不 500
        rows = self._all(task_id=task_id, direction=direction,
                         counterparty=counterparty, source=source)
        window = rows[offset:offset + step]
        nxt = ""
        if offset + step < len(rows):
            nxt = base64.urlsafe_b64encode(str(offset + step).encode()).decode().rstrip("=")
        return {"feedback": [self.view(r) for r in window],
                "count": len(window), "next_cursor": nxt, "total": len(rows)}

    def received(self, *, limit: int = 50) -> list[dict]:
        return self.list(source="counterparty", limit=limit)

    def board(self, *, limit: int = 50) -> list[dict]:
        """控制台清单：在 `list()` 之上附一个**派生**字段 `versions`（版本数），
        供界面显示「vN · 共 M 版」。派生字段不落库，`list()` 语义保持不变。"""
        out: list[dict] = []
        for r in self.list(limit=limit):
            rec = dict(r)
            rec["versions"] = len(self.versions(str(rec.get("feedback_id"))))
            out.append(rec)
        return out

    def by_task(self, task_id: str, author_did: str = "") -> dict:
        """一笔任务的两个方向，各给「我写的 / 对方写的」（控制台行内用）。"""
        tid = str(task_id or "")
        out: dict[str, dict] = {}
        for d in DIRECTIONS:
            mine = theirs = None
            for rec in self.list(task_id=tid, direction=d, limit=200):
                if rec.get("source") == "self":
                    mine = rec
                else:
                    theirs = rec
            out[d] = {"mine": mine, "theirs": theirs}
        return {"task_id": tid, "author_did": author_did, "directions": out}

    def has_for(self, task_id: str, direction: str, author_did: str = "") -> dict | None:
        """这一笔本方有没有写过这个方向的反馈（控制台按钮状态用）。"""
        fid = self.store.get(INDEX_NS, self._index_key(task_id, direction, author_did))
        return self.get(str(fid)) if fid else None

    def summary(self, *, counterparty: str = "") -> dict:
        """**只给事实计数**，不给信誉分、不给等级 —— 那是 R3。

        针对某个交易对手时，取"指向它"的反馈（买方评它作为供给方，或它作为买方
        被评）；不传则统计本机全部。
        """
        rows = self.list(counterparty=counterparty or None, limit=200) \
            if counterparty else self.list(limit=200)
        dims: dict[str, dict] = {}
        latest = 0.0
        for rec in rows:
            if rec.get("source") == "counterparty" and not rec.get("verified"):
                continue
            try:
                latest = max(latest, float(rec.get("at") or 0))
            except (TypeError, ValueError):
                pass
            for key, value in (rec.get("dimensions") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    slot = dims.setdefault(str(key), {"count": 0, "sum": 0.0})
                    slot["count"] += 1
                    slot["sum"] += float(value)
        dimensions = {k: {"count": v["count"], "avg": round(v["sum"] / v["count"], 2)}
                      for k, v in sorted(dims.items()) if v["count"]}
        return {
            "counterparty": str(counterparty or ""), "total": len(rows),
            "self_written": sum(1 for r in rows if r.get("source") == "self"),
            "received": sum(1 for r in rows if r.get("source") == "counterparty"),
            "verified": sum(1 for r in rows if r.get("verified")),
            "unverified": sum(1 for r in rows
                              if r.get("source") == "counterparty"
                              and not r.get("verified")),
            "self_source": sum(1 for r in rows if r.get("self_source")),
            "dimensions": dimensions,
            "latest_at": _iso(latest) if latest else "",
            "kind": KIND,
            "notice": "只列事实计数与各维平均（含样本数），不是信誉分；"
                      "不同 DID 不证明背后是独立个人。",
        }

    def counts(self) -> dict[str, int]:
        mine = list(self.store.items(NAMESPACE).values())
        inbox = list(self.store.items(INBOX_NS).values())
        return {
            "total": len(mine) + len(inbox),
            "self_written": len(mine), "received": len(inbox),
            "buyer_to_seller": sum(1 for r in [*mine, *inbox]
                                   if r.get("direction") == "buyer_to_seller"),
            "seller_to_buyer": sum(1 for r in [*mine, *inbox]
                                   if r.get("direction") == "seller_to_buyer"),
            "unverified": sum(1 for r in inbox if not r.get("verified")),
        }

    def verify_record(self, record: dict) -> bool:
        """记录自检：内容指纹对不对 + 有签名的话验签过不过。"""
        if not isinstance(record, dict):
            return False
        core = _core(record)
        if record.get("digest") and _digest(core) != record.get("digest"):
            return False
        if record.get("proof") is not None and callable(self._verify):
            try:
                return bool(self._verify(record.get("proof"), core))
            except Exception:  # noqa: BLE001
                return False
        return True

    # ---------------- 内部 ----------------

    def _build(self, **kw) -> dict:
        core = {
            "v": "a2n-feedback/1", "feedback_id": kw["feedback_id"],
            "task_id": kw["task_id"], "direction": kw["direction"],
            "author_did": kw["author_did"], "counterparty_did": kw["counterparty_did"],
            "provider_did": kw["provider_did"], "service_id": kw["service_id"],
            "task_state": kw["task_state"], "dimensions": kw["dimensions"],
            "note": kw["note"], "redactions": kw["redactions"],
            "source": kw["source"], "created_at": kw["created_at"],
            "at": kw["at"], "revision": int(kw["revision"]),
            "prev_hash": kw["prev_hash"],
        }
        proof = None
        if callable(self._sign):
            try:
                proof = self._sign(dict(core))
            except Exception:  # noqa: BLE001 - 签名失败不许吞掉记录本身
                proof = None
        record = {**core, "proof": proof, "digest": _digest(core),
                  "versions": int(kw["revision"]), "updated_at": _iso(kw["at"])}
        return record

    def _save(self, record: dict) -> None:
        fid = str(record["feedback_id"])
        self.store.put(VERSION_NS, f"{fid}::{int(record['revision'])}", record)
        self.store.put(NAMESPACE, fid, record)

    def _tx(self):
        factory = getattr(self.store, "tx", None)
        if callable(factory):
            return factory()

        class _Null:
            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        return _Null()  # store 替身没有事务能力时退化为顺序写，行为不变

    def _clean_dimensions(self, direction: str, dimensions: Any) -> dict:
        allowed = DIMENSIONS.get(direction, ())
        if dimensions in (None, ""):
            return {}
        if not isinstance(dimensions, dict):
            raise ValueError("dimensions 必须是对象")
        out: dict[str, int] = {}
        for key, value in dimensions.items():
            k = str(key)
            if k not in allowed:
                raise ValueError(f"{direction} 不支持维度 {k}；可用：{'、'.join(allowed)}")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(f"维度 {k} 的分数必须是 1–5 的整数")
            out[k] = int(value)
        return out

    def _clean_note(self, note: Any) -> tuple[str, list[str]]:
        text = str(note or "").strip()
        if len(text) > MAX_NOTE:
            raise ValueError(f"原因不超过 {MAX_NOTE} 字")
        removed: list[str] = []
        text = _scrub_text(text, removed)
        return text, sorted(set(removed))

    @staticmethod
    def _same_content(record: dict, dimensions: dict, note: str) -> bool:
        return (dict(record.get("dimensions") or {}) == dict(dimensions or {})
                and str(record.get("note") or "") == str(note or ""))

    @staticmethod
    def _about(record: dict, did: str) -> bool:
        """这份反馈是不是"关于"这个 did（它当供给方被评，或它当买方被评）。"""
        target = str(did or "")
        if not target:
            return False
        if record.get("direction") == "buyer_to_seller":
            return str(record.get("provider_did") or "") == target
        return str(record.get("counterparty_did") or "") == target

    @staticmethod
    def _index_key(task_id: str, direction: str, author_did: str) -> str:
        return f"{task_id}::{direction}::{author_did}"

    @staticmethod
    def _inbox_key(author_did: str, task_id: str, direction: str) -> str:
        return f"{author_did}::{task_id}::{direction}"
