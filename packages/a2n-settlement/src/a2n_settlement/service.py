"""M6 分账：规则可插拔且版本化（默认"全额归节点"—— 纯公益，网络不抽费用），
只生成指令，不碰钱。"""
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
               rule: PolicyRef | None = None, currency: str = "CNY",
               commit: bool = True) -> dict[str, Any]:
        """积分结算：冻结户 → 分账划转 → 差额退回 → 托管指令。

        commit=False 把提交权交给外层 store.tx()：结算与"任务推进到 SETTLED"
        必须同生共死 —— 钱划走了而任务停在 ACCEPTED，重试就会二次结算。
        """
        rule = rule or split_rule()
        parts = split_amount(amount, rule)

        # 运行时守卫：**先判规则，再动账**。
        # 顺序不能反 —— 若放进下面的划转循环里，commit=True 时冻结户的扣款已经
        # 落库，异常再抛出去就留下一笔永远不平的负余额：保护本身变成了破坏。
        # 默认道路上**不可能**把钱切给网络或任何第三方池；想恢复抽成必须显式改
        # 这一处 **并且** 改纲领（VISION §6.2），不能靠换一个 PolicyRef 悄悄生效。
        # 历史单子的重放走 `split_amount`（纯计算），与这条执行守卫互不影响。
        cut_out = sorted(k for k, v in parts.items() if k != "node" and v > 0)
        if cut_out:
            raise RuntimeError(
                f"分账规则切出了 {cut_out}：网络不抽任何费用是既定口径"
                f"（VISION §6.2「纯公益」）。若确需非默认分账，请显式改这一处。")

        hold = f"hold:{task_id}"

        # 收款账户先存在，再划转。**只建节点账户**：默认规则是"全额归节点"
        # （纯公益、网络不抽任何费用，发起人 2026-09-18 拍板）。旧版规则里的
        # author / fee / pool 三个池子**不再预建** —— 这是刻意的：让"要切出去"
        # 必须显式做一次，而不是继承一个默认。
        ensure_account(node_id, "node", node_id)

        # 分账：先从冻结账户支出，再按规则划转（网内流转，总量不变）。
        # 走到这里 parts 只剩 node（上面那道闸已拦下其余份额），所以这一笔
        # 必然是全额入节点。提交权交出时（commit=False）不落单笔，整段由外层 tx 一次提交。
        self.ledger.post(hold, -amount, "settlement", task_id, commit=commit)
        for val in parts.values():
            if val > 0:
                self.ledger.post(node_id, val, "settlement", task_id, commit=commit)

        # 差额退回使用方
        remaining = self.ledger.balance(hold)
        if remaining > 0:
            self.ledger.post(hold, -remaining, "unfreeze", task_id, commit=commit)
            self.ledger.post(requester_id, remaining, "unfreeze", task_id, commit=commit)
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
        publish("settlement.settled", {"task_id": task_id, "so_id": so_id, "amount": amount,
                                       "currency": (currency or "CNY").upper(), "splits": parts})
        if commit:                      # 提交权在外层时由外层统一提交（事件仍在事务内）
            conn().commit()
        return {"so_id": so_id, "amount": amount, "currency": (currency or "CNY").upper(),
                "splits": parts, "rule_ref": rule.to_dict(), "custodian_ref": custodian_ref}

    def refund(self, task_id: str, to_account: str, amount: int,
               from_accounts: list[str] | None = None, reason: str = "arbitration") -> dict[str, Any]:
        """仲裁改判后的逆向划转：把已经分出去的钱追回来，退回使用方。

        为什么按份额追回而不是只扣节点：**默认规则下钱全在节点手里**（纯公益，
        网络不抽任何费用）。但只要发生过一次非默认分账（2026-09-18 之前的历史单子），
        钱就可能散在作者池 / 服务费 / 激励池里，只扣节点等于让节点替别人背锅
        （且可能余额不足）。

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
        """按原分账顺序追回：节点 → 服务费 → 激励池 → 作者池。

        后三个是**历史遗留**：2026.09.18 之前的分账规则可能真的切过钱进去，
        追回时要一并考虑（余额为 0 的会被跳过）。默认规则下只有节点有余额。
        """
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
