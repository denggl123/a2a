"""账单：把一个账期内已对账的交易聚成一张单。

一期账单的意义：**告诉双方"这个周期你该付/该收多少"，以及凭什么。**
它是一张对账凭证，不是付款指令。钱仍由双方自行结清。

二期：托管商接入后，账单变成分账指令的输入
（`state` 从 ISSUED → SETTLED 由托管方回执驱动），积分在这一步才被发行。
"""
from __future__ import annotations

from typing import Any

from a2n_kernel import new_id, now_iso, publish
from a2n_store import conn

STATE_OPEN = "OPEN"
STATE_ISSUED = "ISSUED"
STATE_SETTLED = "SETTLED"      # 二期：托管商实际划转后

NEXT_STATE = {
    STATE_OPEN: {STATE_ISSUED},
    STATE_ISSUED: {STATE_SETTLED},
    STATE_SETTLED: set(),
}


def current_period() -> str:
    return now_iso()[:7]


class Statements:
    def issue(self, link_id: str, period: str | None = None) -> dict:
        """出账。已存在同账期账单则直接返回（幂等）。"""
        from a2n_account import peers

        if not peers.get(link_id):
            raise ValueError("配对不存在")
        period = period or current_period()

        exists = conn().execute(
            "SELECT * FROM statements WHERE link_id=? AND period=?", (link_id, period)
        ).fetchone()
        if exists:
            return self.get(exists["statement_id"])

        rows = conn().execute(
            "SELECT deal_id FROM deals WHERE link_id=? AND state='RECONCILED'"
            " AND (statement_id IS NULL OR statement_id='')", (link_id,)
        ).fetchall()
        if not rows:
            raise ValueError("该账期没有已对账的交易，无需出账")

        total = 0
        curs: set[str] = set()
        for r in rows:
            rc = conn().execute(
                "SELECT amount_fen, currency FROM deal_recons WHERE deal_id=?",
                (r["deal_id"],)).fetchone()
            total += int(rc["amount_fen"] or 0) if rc else 0
            if rc:
                curs.add((rc["currency"] or "CNY").upper())
        # 一张账单一种币：把两种币加在一个 total 里不是汇总，是造假。
        # 混币时拒绝出单，让不同币种各出各的（二期可按币种分单出账）。
        if len(curs) > 1:
            raise ValueError(f"账期内存在多种币种的已对账交易 {sorted(curs)}，"
                             f"混币不能出一张账单")
        currency = curs.pop() if curs else "CNY"

        sid = f"st_{new_id('')}"
        conn().execute(
            "INSERT INTO statements (statement_id, link_id, period, total_fen, deal_count,"
            " state, created_at, currency, total_minor) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, link_id, period, total, len(rows), STATE_ISSUED, now_iso(),
             currency, total),
        )
        for r in rows:
            conn().execute("UPDATE deals SET statement_id=?, updated_at=? WHERE deal_id=?",
                           (sid, now_iso(), r["deal_id"]))
        conn().commit()
        publish("statement.issued", {"statement_id": sid, "link_id": link_id,
                                     "period": period, "total_fen": total,
                                     "currency": currency,
                                     "deal_count": len(rows)})
        return self.get(sid)

    def get(self, statement_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM statements WHERE statement_id=?", (statement_id,)).fetchone()
        if not r:
            return None
        s = dict(r)
        s["deals"] = [x["deal_id"] for x in conn().execute(
            "SELECT deal_id FROM deals WHERE statement_id=?", (statement_id,))]
        return s

    def list_by_link(self, link_id: str) -> list[dict]:
        rows = conn().execute(
            "SELECT statement_id FROM statements WHERE link_id=? ORDER BY period", (link_id,)
        ).fetchall()
        return [self.get(r["statement_id"]) for r in rows]

    def mark_settled(self, statement_id: str, ref: str | None = None) -> dict:
        """二期入口：托管商完成划转后回执调用。一期不应触发。"""
        s = self.get(statement_id)
        if not s:
            raise ValueError("账单不存在")
        if s["state"] != STATE_ISSUED:
            raise ValueError(f"当前状态 {s['state']} 不能标记已结清")
        conn().execute("UPDATE statements SET state=? WHERE statement_id=?",
                       (STATE_SETTLED, statement_id))
        conn().commit()
        publish("statement.settled", {"statement_id": statement_id, "ref": ref,
                                      "total_fen": s["total_fen"]})
        return self.get(statement_id)


statements = Statements()
