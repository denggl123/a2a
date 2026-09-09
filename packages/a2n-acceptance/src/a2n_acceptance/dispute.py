"""争议与仲裁：机器判不了的，交给人判，但必须留下完整材料。

为什么需要它：验收是策略自动判的，而策略必然有边界情况 ——
节点自报 3 秒、平台观测 9 秒，可能是网络抖动，也可能是节点谎报。
这条缝不该由算法硬判，也不该由平台单方说了算。

设计上的三条自律：
  1. **争议只记录与裁定，不自己动手退钱** —— 裁定是"应当如何"，
     执行划转是结算层的事（装配层接线）。判断与执行分离，才能各自被审计。
  2. **开单必须带证据快照** —— 自报计量、平台观测、方差、信誉、凭证，
     一次固化。事后再取的数据可能已经变了。
  3. **裁定结果写进凭证链** —— 谁裁的、依据什么、退了多少，
     都要能事后重放。仲裁员也是被监督的对象。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_store import conn
from a2n_kernel.events import publish
from a2n_kernel.hashing import new_id, now_iso

STATE_OPEN, STATE_RESOLVED = "OPEN", "RESOLVED"

# 裁定类型
UPHOLD_REJECT = "uphold_reject"     # 维持打回：节点确实没干好，不退
OVERTURN_PAY = "overturn_pay"       # 改判应付：验收误判，应补付节点
PARTIAL = "partial"                 # 部分退：双方各担一部分

RULINGS = {UPHOLD_REJECT, OVERTURN_PAY, PARTIAL}


class Disputes:
    def open(self, task_id: str, side: str, reason: str, opened_by: str = "system",
             evidence: dict | None = None) -> dict[str, Any]:
        """开单。side 说明是谁在申诉：requester（使用方）/ node（节点）。"""
        if side not in ("requester", "node"):
            raise ValueError("申诉方只能是 requester 或 node")
        did = new_id("ds")
        ts = now_iso()
        conn().execute(
            "INSERT INTO disputes (id, task_id, opened_by, side, reason, evidence, state,"
            " refund_fen, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (did, task_id, opened_by, side, reason,
             json.dumps(evidence or {}, ensure_ascii=False), STATE_OPEN, 0, ts),
        )
        conn().commit()
        publish("arbitration.opened", {"dispute_id": did, "task_id": task_id,
                                       "side": side, "reason": reason})
        return self.get(did)

    def get(self, dispute_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        return self._row(r) if r else None

    def _row(self, r) -> dict:
        d = dict(r)
        if d.get("evidence"):
            d["evidence"] = json.loads(d["evidence"])
        return d

    def list(self, state: str | None = None, task_id: str | None = None,
             limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM disputes"
        conds, args = [], []
        if state:
            conds.append("state=?")
            args.append(state)
        if task_id:
            conds.append("task_id=?")
            args.append(task_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY rowid DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in conn().execute(sql, args).fetchall()]

    def resolve(self, dispute_id: str, ruling: str, arbitrator: str,
                refund_points: int = 0, resolution: str = "") -> dict[str, Any]:
        """裁定。只产出"该怎么判"，不执行划转 —— 执行由装配层调用结算层完成。"""
        if ruling not in RULINGS:
            raise ValueError(f"未知裁定类型：{ruling}，可选 {sorted(RULINGS)}")
        d = self.get(dispute_id)
        if not d:
            raise ValueError("争议不存在")
        if d["state"] == STATE_RESOLVED:
            raise ValueError("争议已裁定，不可重复裁决")
        if ruling == UPHOLD_REJECT and refund_points:
            raise ValueError("维持打回时不应产生退款")
        if ruling in (OVERTURN_PAY, PARTIAL) and refund_points <= 0:
            raise ValueError("改判/部分退必须给出明确退款额")

        ts = now_iso()
        conn().execute(
            "UPDATE disputes SET state=?, ruling=?, refund_fen=?, arbitrator=?, resolution=?,"
            " resolved_at=? WHERE id=?",
            (STATE_RESOLVED, ruling, int(refund_points), arbitrator, resolution, ts, dispute_id),
        )
        conn().commit()
        payload = {"dispute_id": dispute_id, "task_id": d["task_id"], "ruling": ruling,
                   "refund_points": int(refund_points), "arbitrator": arbitrator,
                   "side": d["side"]}
        publish("arbitration.resolved", payload)
        return {**self.get(dispute_id), "event": payload}


disputes = Disputes()
