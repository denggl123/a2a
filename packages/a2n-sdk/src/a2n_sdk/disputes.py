"""本机「这单我不认」记录：用户对某笔交付说不认，并留下可核验的那一份。

为什么需要它（`docs/PRODUCT.md` 时刻 4）：**"没有仲裁员"不等于"用户没处说话"** ——
前者是对第三方的拒绝，后者是对用户的失职。所以结果不好时，用户必须有
**"这单我不认"** 这个动作，而这个动作本身要产生一份凭证。

**它是什么 / 不是什么（边界写死在数据里）**
  · 是：把"谁、对哪一笔、为什么不认、什么时候说的"固化成一条本机留存记录，
    带一个内容指纹 `digest`，事后谁都能拿它对一遍"这份记录有没有被改过"。
  · 不是：不是一个仲裁系统。自持模式**没有第三方裁决**（`docs/SOVEREIGN.md`
    第四节明说撤回/争议/仲裁不在本模式内），所以这里**不自动退钱、不代对方裁定、
    不假装有裁决结果**。记录里 `kind="local_rejection_not_arbitration"` 把这条边界
    机器可读地标出来 —— 前端不许把"我留了一份不认记录"画成"平台判我赢了"。
  · 用户改主意可以**撤回**：撤回同样是留痕的（`state=WITHDRAWN` + `withdrawn_at`），
    不是把记录抹掉。抹掉 = 事后翻脸无据，那是这个产品最不能做的事。

本模块使用节点自己的加密 LocalStore，是唯一业务实现。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

# 记录在本机存储里的命名空间（值会被 LocalStore 的加密器封存）。
NAMESPACE = "disputes"

STATE_OPEN, STATE_WITHDRAWN = "OPEN", "WITHDRAWN"
SIDES = ("requester", "node")
KIND = "local_rejection_not_arbitration"
MAX_REASON = 500


def _now() -> float:
    import time
    return time.time()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _claim_digest(core: dict) -> str:
    """对"不认的那件事"取指纹：任务、申诉方、理由、细节、开口时间。

    刻意**不含 state / withdrawn_at** —— 撤回是这条记录之上新发生的事实，
    不改变"当初不认的是什么"。指纹稳定，才能拿它对"东西有没有被换过"。
    """
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class DisputeBook:
    """节点本地的"我不认"账本。没有仲裁，只有留痕与撤回。"""

    def __init__(self, store):
        self.store = store

    # ---------------- 写 ----------------

    def open(self, scope: str, task_id: str, side: str, reason: str,
             details: dict | None = None) -> dict[str, Any]:
        """记下"这单我不认"。

        只有**本机真有这条调用记录**才允许开单 —— 对一个查无此单的任务说不认，
        等于伪造一份无对象的指控。同一笔已有未撤回的记录时**幂等返回**，
        不重复堆同一份指控。
        """
        scope = str(scope or "").strip()
        task_id = str(task_id or "").strip()
        if side not in SIDES:
            raise ValueError(f"申诉方只能是 {SIDES} 之一")
        text = str(reason or "").strip()
        if not text:
            raise ValueError("必须写清为什么不认（哪怕一句话）")
        if len(text) > MAX_REASON:
            raise ValueError(f"理由不超过 {MAX_REASON} 字")
        if not scope or not task_id:
            raise ValueError("必须指明是哪一笔（scope + task_id）")
        row = self.store.task(scope, task_id)
        if not row:
            raise ValueError("本机没有这条调用记录，无法对它说不认")

        existing = self._find_open(scope, task_id, side)
        if existing:
            return existing

        ts = _now()
        did = "ds_" + secrets.token_hex(8)
        core = {"id": did, "scope": scope, "task_id": task_id, "side": side,
                "reason": text, "details": dict(details or {}), "created_at": _iso(ts)}
        record = {**core, "state": STATE_OPEN, "withdrawn_at": None, "note": "",
                  "updated_at": _iso(ts), "kind": KIND,
                  "digest": _claim_digest(core)}
        self._save(record)
        return record

    def withdraw(self, dispute_id: str, note: str = "") -> dict[str, Any]:
        """撤回（用户改主意）。留痕，不删记录。"""
        rec = self.get(dispute_id)
        if not rec:
            raise ValueError("没有这条不认记录")
        if rec["state"] == STATE_WITHDRAWN:
            return rec
        ts = _now()
        rec = {**rec, "state": STATE_WITHDRAWN, "withdrawn_at": _iso(ts),
               "note": str(note or "").strip()[:MAX_REASON], "updated_at": _iso(ts)}
        self._save(rec)
        return rec

    # ---------------- 读 ----------------

    def get(self, dispute_id: str) -> dict | None:
        return self.store.get(NAMESPACE, str(dispute_id or ""))

    def list(self, *, state: str | None = None, task_id: str | None = None,
             scope: str | None = None, limit: int = 50) -> list[dict]:
        rows = list(self.store.items(NAMESPACE).values())
        if state:
            rows = [r for r in rows if r.get("state") == state]
        if task_id:
            rows = [r for r in rows if r.get("task_id") == task_id]
        if scope:
            rows = [r for r in rows if r.get("scope") == scope]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[:max(1, min(int(limit), 200))]

    def open_for(self, scope: str, task_id: str) -> dict | None:
        """这一笔当前有没有未撤回的不认记录（控制台行内徽标用）。"""
        for rec in self.list(scope=scope, task_id=task_id):
            if rec.get("state") == STATE_OPEN:
                return rec
        return None

    def counts(self) -> dict[str, int]:
        rows = list(self.store.items(NAMESPACE).values())
        opened = sum(1 for r in rows if r.get("state") == STATE_OPEN)
        return {"total": len(rows), "open": opened,
                "withdrawn": len(rows) - opened}

    # ---------------- 内部 ----------------

    def _find_open(self, scope: str, task_id: str, side: str) -> dict | None:
        for rec in self.list(scope=scope, task_id=task_id):
            if rec.get("state") == STATE_OPEN and rec.get("side") == side:
                return rec
        return None

    def _save(self, record: dict) -> None:
        self.store.put(NAMESPACE, record["id"], record)

    def verify(self, record: dict) -> bool:
        """指纹自检：记录有没有被改过。控制台/测试都可调。"""
        core = {k: record.get(k) for k in
                ("id", "scope", "task_id", "side", "reason", "details", "created_at")}
        return _claim_digest(core) == record.get("digest")
