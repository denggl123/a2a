"""M6 分账：规则可插拔（90/2/3/5 只是默认实现），只生成指令，不碰钱。"""
from __future__ import annotations

import json
from typing import Any

from a2n_custodian import get_custodian
from a2n_store import conn
from a2n_kernel.events import publish
from a2n_kernel.hashing import new_id, now_iso
from a2n_kernel.policy import PolicyRef, split_amount, split_rule
from a2n_ledger import Ledger, ensure_account

# 计量维度改成注册表（a2n_settlement.dims）：新增维度 = 注册一条，
# 下面这些代码不用改就认识它。BILLABLE_DIMS 由注册表派生，保持向后兼容。
from .dims import BILLABLE_DIMS, is_billable, register_dimension  # noqa: F401
from .price import amount_of


def compute_amount(unit_prices: dict[str, float], dims: dict[str, float], budget: int) -> int:
    """按实结算，且永不超过冻结预算。

    **账单为 0 是合法的返回值，不是异常**：它意味着"这次调用没有任何
    可计费的计量维度"。以前这里兜底成 1 积分，等于凭空造出一笔没有
    对应计量的收入 —— 对账时它会表现为"来源不明的 1 分钱"。
    现在交回上层判为计量缺失并打回退款。

    金额本身由 .price.amount_of 算（唯一实现）；这里只加结算域自己的策略：封顶预算。
    """
    amount = amount_of(dims, unit_prices)
    if amount <= 0:
        return 0
    return min(amount, budget)


class Settlement:
    def __init__(self) -> None:
        self.ledger = Ledger()

    def settle(self, task_id: str, requester_id: str, node_id: str, amount: int,
               rule: PolicyRef | None = None, currency: str = "CNY") -> dict[str, Any]:
        rule = rule or split_rule()
        parts = split_amount(amount, rule)
        hold = f"hold:{task_id}"

        # 收款账户先存在，再划转
        ensure_account("acct:author", "author", "样本作者池")
        ensure_account("acct:fee", "fee", "网络服务费")
        ensure_account("acct:pool", "pool", "冷启动激励池")
        ensure_account(node_id, "node", node_id)

        # 分账：先从冻结账户支出，再按规则划转（网内流转，总量不变）
        self.ledger.post(hold, -amount, "settlement", task_id)
        for key, val in parts.items():
            if val <= 0:
                continue
            target = {"node": node_id, "author": "acct:author",
                      "fee": "acct:fee", "pool": "acct:pool"}.get(key, "acct:pool")
            self.ledger.post(target, val, "settlement", task_id)

        # 差额退回使用方
        remaining = self.ledger.balance(hold)
        if remaining > 0:
            self.ledger.post(hold, -remaining, "unfreeze", task_id)
            self.ledger.post(requester_id, remaining, "unfreeze", task_id)
        elif remaining < 0:  # 理论上不会发生，兜底保护
            raise RuntimeError(f"冻结账户透支：{hold} = {remaining}")

        custodian_ref = get_custodian().create_settlement_order(parts, task_id)
        so_id = new_id("so")
        conn().execute(
            "INSERT INTO settlement_orders (id, task_id, amount, splits, rule_ref, custodian_ref,"
            " state, created_at, currency, amount_minor) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (so_id, task_id, amount, json.dumps(parts, ensure_ascii=False),
             json.dumps(rule.to_dict(), ensure_ascii=False), custodian_ref, "SETTLED", now_iso(),
             (currency or "CNY").upper(), amount),
        )
        conn().commit()
        publish("settlement.settled", {"task_id": task_id, "so_id": so_id, "amount": amount,
                                       "currency": (currency or "CNY").upper(), "splits": parts})
        return {"so_id": so_id, "amount": amount, "currency": (currency or "CNY").upper(),
                "splits": parts, "rule_ref": rule.to_dict(), "custodian_ref": custodian_ref}

    def refund(self, task_id: str, to_account: str, amount: int,
               from_accounts: list[str] | None = None, reason: str = "arbitration") -> dict[str, Any]:
        """仲裁改判后的逆向划转：把已经分出去的钱追回来，退回使用方。

        为什么按份额追回而不是只扣节点：钱已经分给了作者池、服务费与激励池，
        只扣节点等于让节点替别人背锅（且可能余额不足）。

        为什么宁可追不回也不透支：账户可以被扣到 0，但不允许为负。
        负余额意味着"凭空欠账"，那比追不回更危险 —— 差额记为 shortfall，
        由运营账面承担，绝不伪造一条平衡的记录。
        """
        if amount <= 0:
            return {"task_id": task_id, "refunded": 0, "shortfall": 0, "recovered_from": {}}
        targets = from_accounts or self._beneficiaries(task_id)
        recovered: dict[str, int] = {}
        left = int(amount)
        for acc in targets:
            if left <= 0:
                break
            avail = self.ledger.balance(acc)
            if avail <= 0:
                continue
            take = min(avail, left)
            self.ledger.post(acc, -take, "refund", task_id)
            recovered[acc] = take
            left -= take
        back = int(amount) - left
        if back > 0:
            self.ledger.post(to_account, back, "refund", task_id)
        publish("settlement.refunded", {"task_id": task_id, "to": to_account, "amount": back,
                                        "shortfall": left, "reason": reason})
        return {"task_id": task_id, "refunded": back, "shortfall": left,
                "recovered_from": recovered, "reason": reason}

    def _beneficiaries(self, task_id: str) -> list[str]:
        """按原分账顺序追回：节点 → 服务费 → 激励池 → 作者池。"""
        row = conn().execute(
            "SELECT node_id FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        node_id = row["node_id"] if row else None
        order = [x for x in (node_id, "acct:fee", "acct:pool", "acct:author") if x]
        return order

    def list_orders(self, limit: int = 50) -> list[dict]:
        rows = conn().execute("SELECT * FROM settlement_orders ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["splits"] = json.loads(d["splits"])
            d["rule_ref"] = json.loads(d["rule_ref"])
            out.append(d)
        return out


settlement = Settlement()
