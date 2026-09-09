"""a2n-deal（L3）：一期交易 —— 达成、双向计量、对账、出账。

**一期不碰钱。** 这一层只产出三样东西：
1. 一份条款快照（成交那一刻冻结，事后改条款无效）；
2. 双方各自上报的计量事实（A2N 只记录，不替任何一方下结论）；
3. 一份对账结果与账单（金额单位是分，**不是积分**）。

钱怎么付，是交易双方自己的事（对公转账、线下结清、或二期走托管商）。
二期接进来时：账单 → 分账指令 → 托管商执行 → 积分发行，这条链路上
本文件的输出就是输入，不用改。

状态机：AGREED → DELIVERED → RECONCILED → CLOSED
        任一步可 → DISPUTED（对账不一致） / CANCELED
"""
from __future__ import annotations

import json
from typing import Any

from a2n_kernel import new_id, now_iso, publish
from a2n_settlement.price import amount_of   # 金额算法唯一实现
from a2n_store import conn

STATE_AGREED = "AGREED"
STATE_DELIVERED = "DELIVERED"
STATE_RECONCILED = "RECONCILED"
STATE_DISPUTED = "DISPUTED"
STATE_CLOSED = "CLOSED"
STATE_CANCELED = "CANCELED"

NEXT_STATE = {
    STATE_AGREED: {STATE_DELIVERED, STATE_DISPUTED, STATE_CANCELED},
    STATE_DELIVERED: {STATE_RECONCILED, STATE_DISPUTED, STATE_CANCELED},
    STATE_RECONCILED: {STATE_CLOSED, STATE_DISPUTED},
    STATE_DISPUTED: {STATE_RECONCILED, STATE_CANCELED},
    STATE_CLOSED: set(),
    STATE_CANCELED: set(),
}

# 未结状态：这些 deal 的金额计入额度占用
OPEN_STATES = (STATE_AGREED, STATE_DELIVERED, STATE_RECONCILED)

# 对账容差：相对 10% 与绝对 1 元（100 分）取大者。
# 纯相对会在小额单上误判（1 分钱的差也算 100%），纯绝对会在大额单上失真。
RECON_REL_TOL = 0.10
RECON_ABS_TOL_FEN = 100


def compute_amount_fen(unit_prices: dict, dims: dict) -> int:
    """按条款单价 × 计量维度算金额（最小单位整数）。没有可计费维度 = 0，绝不兜底造钱。

    金额的算法只有一处实现（a2n_settlement.price.amount_of：可计费维度过滤 +
    定点取整）。这里只做形状适配 —— v2 价目条目 {"amount", "per"} 折算成单位单价。
    以前这里另写了一份循环：不过滤不可计费维度、还漏了 per，同一单在成交域
    与结算域能算出两个价。
    """
    flat: dict = {}
    for dim, price in (unit_prices or {}).items():
        if isinstance(price, dict):
            per = float(price.get("per") or 1) or 1.0
            flat[dim] = float(price.get("amount") or 0) / per
        else:
            flat[dim] = float(price)
    return amount_of(dims, flat)


class Deals:
    def open(self, link_id: str, skill: str, task_id: str | None = None,
             currency: str | None = None) -> dict:
        """达成交易：配对必须是 ACTIVE 且未超额度，条款当场快照冻结。

        currency 进条款快照：成交那一刻用什么币种计价，事后换币无效。
        """
        from a2n_account import peers

        lk = peers.get(link_id)
        if not lk:
            raise ValueError("配对不存在")
        ok, why = peers.usable(link_id)
        if not ok:
            raise ValueError(f"不能达成交易：{why}")
        if not skill:
            raise ValueError("skill 必填")

        terms = dict(lk["terms"])
        terms["currency"] = (currency or terms.get("currency") or "CNY").upper()
        ts = now_iso()
        deal_id = f"dl_{new_id('')}"
        conn().execute(
            "INSERT INTO deals (deal_id, link_id, account_id, agent_id, skill, terms, state,"
            " task_id, opened_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (deal_id, link_id, lk["account_id"], lk["agent_id"], skill,
             json.dumps(terms, ensure_ascii=False), STATE_AGREED, task_id, ts, ts),
        )
        conn().commit()
        publish("deal.agreed", {"deal_id": deal_id, "link_id": link_id, "skill": skill,
                                "account_id": lk["account_id"], "agent_id": lk["agent_id"],
                                "currency": terms["currency"]})
        return self.get(deal_id)

    def report(self, deal_id: str, party: str, dims: dict,
               amount_fen: int | None = None, evidence: dict | None = None,
               currency: str | None = None) -> dict:
        """一方上报计量。两方都上报后进入 DELIVERED。

        amount_fen 为 None 时按成交条款的单价算——用谁的计量都行，
        关键是**双方各自报一份**，对账时才看得出分歧在哪。
        币种来自条款快照（成交时点冻结），显式传入必须与条款一致 ——
        同一笔交易不能一方报美元一方报人民币，那不是对账是鸡同鸭讲。
        """
        d = self.get(deal_id)
        if not d:
            raise ValueError("交易不存在")
        if d["state"] not in {STATE_AGREED, STATE_DELIVERED}:
            raise ValueError(f"当前状态 {d['state']} 不接受上报")
        if party not in {"requester", "provider"}:
            raise ValueError("party 只能是 requester 或 provider")

        cur = (currency or d["terms"].get("currency") or "CNY").upper()
        if cur != (d["terms"].get("currency") or "CNY").upper():
            raise ValueError(f"上报币种 {cur} 与条款币种 {d['terms'].get('currency')} 不符")

        if amount_fen is None:
            amount_fen = compute_amount_fen(d["terms"].get("unit_prices", {}), dims)

        conn().execute(
            "INSERT INTO deal_reports (id, deal_id, party, dims, amount_fen, evidence,"
            " reported_at, currency, amount_minor) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"dr_{new_id('')}", deal_id, party, json.dumps(dims or {}, ensure_ascii=False),
             int(amount_fen), json.dumps(evidence or {}, ensure_ascii=False), now_iso(),
             cur, int(amount_fen)),
        )
        parties = {r["party"] for r in conn().execute(
            "SELECT party FROM deal_reports WHERE deal_id=?", (deal_id,))}
        if len(parties) >= 2 and d["state"] == STATE_AGREED:
            self._set_state(deal_id, STATE_DELIVERED)
        conn().commit()
        publish("deal.reported", {"deal_id": deal_id, "party": party, "amount_fen": amount_fen})
        return self.get(deal_id)

    def reconcile(self, deal_id: str) -> dict:
        """对账：两份自报差在容差内 → 认定较小者（对使用方有利），否则转争议。"""
        d = self.get(deal_id)
        if not d:
            raise ValueError("交易不存在")
        if d["state"] not in {STATE_DELIVERED, STATE_DISPUTED}:
            raise ValueError(f"当前状态 {d['state']} 不能对账")

        rows = conn().execute(
            "SELECT party, amount_fen, dims, currency FROM deal_reports WHERE deal_id=?",
            (deal_id,)).fetchall()
        if len(rows) < 2:
            raise ValueError("双方都上报后才能对账")

        amounts = {r["party"]: int(r["amount_fen"] or 0) for r in rows}
        req, prov = amounts.get("requester", 0), amounts.get("provider", 0)
        delta = abs(req - prov)
        base = max(req, prov)
        tol = max(int(base * RECON_REL_TOL), RECON_ABS_TOL_FEN)
        matched = delta <= tol
        # 有分歧取较小者：宁可少收，不可多记。差额留给人工仲裁。
        amount = min(req, prov) if not matched else (req + prov) // 2
        # 一张对账只能有一个币种：双方上报币种不一致时不开认定 ——
        # 跨币种的"一致"是没有意义的（1 USDC ≠ 1 CNY）。
        curs = {(r["currency"] or "CNY").upper() for r in rows}
        if len(curs) > 1:
            raise ValueError(f"双方上报币种不一致：{sorted(curs)}，跨币种不能对账")
        cur = curs.pop()

        conn().execute(
            "INSERT OR REPLACE INTO deal_recons (deal_id, amount_fen, delta_fen, matched, policy,"
            " created_at, currency, amount_minor, delta_minor) VALUES (?,?,?,?,?,?,?,?,?)",
            (deal_id, amount, delta, 1 if matched else 0,
             f"rel{RECON_REL_TOL}/abs{RECON_ABS_TOL_FEN}fen", now_iso(),
             cur, amount, delta),
        )
        self._set_state(deal_id, STATE_RECONCILED if matched else STATE_DISPUTED)
        conn().commit()
        publish("deal.reconciled" if matched else "deal.disputed",
                {"deal_id": deal_id, "amount_fen": amount, "delta_fen": delta,
                 "currency": cur,
                 "requester_fen": req, "provider_fen": prov})
        return self.get(deal_id)

    def set_state(self, deal_id: str, state: str) -> dict:
        d = self.get(deal_id)
        if not d:
            raise ValueError("交易不存在")
        if state not in NEXT_STATE.get(d["state"], set()):
            raise ValueError(f"非法状态迁移：{d['state']} → {state}")
        self._set_state(deal_id, state)
        conn().commit()
        return self.get(deal_id)

    def cancel(self, deal_id: str) -> dict:
        return self.set_state(deal_id, STATE_CANCELED)

    def close(self, deal_id: str) -> dict:
        return self.set_state(deal_id, STATE_CLOSED)

    def get(self, deal_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["terms"] = json.loads(d["terms"] or "{}")
        d["reports"] = [dict(x) for x in conn().execute(
            "SELECT party, dims, amount_fen, evidence, reported_at, currency, amount_minor"
            " FROM deal_reports WHERE deal_id=? ORDER BY reported_at", (deal_id,))]
        for rp in d["reports"]:
            rp["dims"] = json.loads(rp["dims"] or "{}")
        rc = conn().execute("SELECT * FROM deal_recons WHERE deal_id=?", (deal_id,)).fetchone()
        d["recon"] = dict(rc) if rc else None
        return d

    def list_by_link(self, link_id: str, state: str | None = None) -> list[dict]:
        q = "SELECT deal_id FROM deals WHERE link_id=?"
        args: list[Any] = [link_id]
        if state:
            q += " AND state=?"
            args.append(state)
        rows = conn().execute(q + " ORDER BY opened_at", args).fetchall()
        return [self.get(r["deal_id"]) for r in rows]

    def exposure_fen(self, link_id: str) -> int:
        """该配对下已开未结的金额（分）—— 供额度检查用。"""
        rows = conn().execute(
            "SELECT d.deal_id FROM deals d WHERE d.link_id=? AND d.state IN (?,?,?)"
            " AND d.statement_id IS NULL", (link_id, *OPEN_STATES),
        ).fetchall()
        total = 0
        for r in rows:
            rc = conn().execute("SELECT amount_fen FROM deal_recons WHERE deal_id=?",
                                (r["deal_id"],)).fetchone()
            if rc:
                total += int(rc["amount_fen"] or 0)
            else:
                p = conn().execute(
                    "SELECT amount_fen FROM deal_reports WHERE deal_id=? AND party='provider'",
                    (r["deal_id"],)).fetchone()
                total += int(p["amount_fen"] or 0) if p else 0
        return total

    def _set_state(self, deal_id: str, state: str) -> None:
        conn().execute("UPDATE deals SET state=?, updated_at=? WHERE deal_id=?",
                       (state, now_iso(), deal_id))


deals = Deals()
