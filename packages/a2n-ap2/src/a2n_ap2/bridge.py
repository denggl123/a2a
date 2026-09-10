"""三个扩展点：预算 / 凭证 / 问责。

为什么正好是这三个：AP2 的授权链在"扣款"那一刻就结束了，
但 A2N 的世界里，扣款之前必须冻结、扣款之后必须能自证、出问题必须能追责。
三个扩展分别补上这三点，缺一个 A2N 就不能接 AP2。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from a2n_kernel.hashing import canonical_json, now_iso, sha256

from .mandates import CART, INTENT, PAYMENT, Mandate, MandateError

# A2N 积分锚定人民币分（1 积分 = 0.01 元 = 1 分）。
# 这不是巧合，是刻意设计：让 AP2 的授权额度（分）与 A2N 的预算（积分）1:1 可比，
# 翻译时不需要任何汇率换算 —— 换算就是出错与套利的空间。
FEN_PER_POINT = 1

X_BUDGET = "x-a2n-budget"
X_RECEIPT = "x-a2n-receipt"
X_ACCOUNTABILITY = "x-a2n-accountability"


class BudgetTranslator:
    """扩展一：Intent 授权 → A2N 冻结预算。

    关键语义：AP2 的 `max_amount_fen` 是**授权上限**，A2N 的 `budget` 是**真冻结**。
    翻译必须走"冻结"这一步，否则会变成"先干活后要钱"的信用模式，
    而这正是 A2N 从头到尾拒绝的东西。
    """

    @staticmethod
    def from_intent(intent: Mandate, skill: str,
                    price_hint_fen: int | None = None) -> dict:
        if intent.kind != INTENT:
            raise MandateError("预算翻译的输入必须是 intent 凭证")
        cap = intent.scope.get("max_amount_fen")
        if cap is None:
            raise MandateError("intent 缺少 max_amount_fen：没有上限的授权无法冻结")
        cap = int(cap)
        # 冻结额 = min(授权上限, 报价)；授权没那么多就冻不了那么多
        budget_points = cap if price_hint_fen is None else min(cap, int(price_hint_fen))
        budget_points = max(0, budget_points // FEN_PER_POINT)
        return {
            "skill": skill,
            "budget": budget_points,
            "mandate_ref": intent.id,
            "subject": intent.subject,
            "agent": intent.agent,
            "expires_at": intent.expires_at,
            "frozen": True,                       # 明确标注：这是冻结，不是承诺
            "acceptance": intent.scope.get("acceptance") or {},
        }

    @staticmethod
    def to_intent(subject: str, agent: str, skill: str, max_amount_fen: int,
                  expires_at: str | None = None, acceptance: dict | None = None,
                  issuer: str | None = None) -> Mandate:
        """反向：把一次 A2N 调用意图封装成 AP2 intent（供 agent 侧生成）。

        二期桩：一期运行路径只走 from_intent（routers/ap2.py）。
        """
        return Mandate(
            kind=INTENT, subject=subject, agent=agent, issuer=issuer or subject,
            scope={"max_amount_fen": int(max_amount_fen), "skill": skill,
                   "acceptance": acceptance or {}},
            expires_at=expires_at,
            x_a2n={"skill": skill, "budget_points": int(max_amount_fen) // FEN_PER_POINT},
        )


@dataclass
class ReceiptBuilder:
    """扩展二：A2N 结算 → 可回填进 Payment Mandate 的结算凭证。

    AP2 的问责链需要"钱花在哪了"的证据。A2N 提供的是**可独立验证**的四件套：
    结果哈希（干了什么）、双向计量（干了多少）、分账明细（给了谁）、
    公证章 + epoch（谁证明的、钉在哪一批）。
    """

    @staticmethod
    def build(task: dict[str, Any], settlement: dict[str, Any],
              notary_rid: str | None = None, epoch_id: str | None = None,
              usage: dict[str, Any] | None = None) -> dict:
        amount = int(settlement.get("amount") or task.get("amount") or 0)
        return {
            "task_id": task.get("id"),
            "skill": task.get("skill_id"),
            "node_id": task.get("node_id"),
            "result_hash": task.get("result_hash"),
            "amount_points": amount,
            "amount_fen": amount * FEN_PER_POINT,
            "splits": settlement.get("splits") or {},
            "rule_ref": settlement.get("rule_ref") or {},
            "usage": usage or {},
            "notary_rid": notary_rid,
            "epoch_id": epoch_id,
            "state": task.get("state"),
            "issued_at": now_iso(),
        }

    @staticmethod
    def attach(payment: Mandate, receipt: dict) -> Mandate:
        """把结算凭证挂到 payment 凭证上，形成完整问责链。

        二期桩：一期问责链在服务端组装（routers/ap2.py 的 build），
        agent 侧回填属二期。
        """
        payment.x_a2n[X_RECEIPT] = receipt
        return payment

    @staticmethod
    def digest(receipt: dict) -> str:
        return sha256(canonical_json(receipt))


@dataclass
class AccountabilityPack:
    """扩展三：出事之后拿得出什么。

    争议发生时，各方需要的不是"谁说得对"，而是**一份双方都无法否认的材料**：
    节点自报的计量、平台观测的计量、两者的方差、当时的信誉快照、公证章。
    这个包只负责把它组装成标准形状，不负责裁决 —— 裁决归仲裁工作台。
    """

    @staticmethod
    def build(task: dict[str, Any], usage_report: dict[str, Any] | None = None,
              reputation: float | None = None, dispute_id: str | None = None,
              notary_rid: str | None = None) -> dict:
        ur = usage_report or {}
        dims = ur.get("dims") if isinstance(ur.get("dims"), dict) else _safe_json(ur.get("dims"))
        observed = ur.get("observed") if isinstance(ur.get("observed"), dict) else _safe_json(ur.get("observed"))
        return {
            "task_id": task.get("id"),
            "node_id": task.get("node_id"),
            "requester": task.get("requester_id"),
            "reported_dims": dims or {},            # 节点自报（内部真相）
            "observed_dims": observed or {},        # 平台观测（外部真相）
            "variance": ur.get("variance"),
            "reputation_at_call": reputation,
            "result_hash": task.get("result_hash"),
            "contract": _safe_json(ur.get("contract")) or {},
            "notary_rid": notary_rid,
            "dispute_id": dispute_id,
            "built_at": now_iso(),
        }

    @staticmethod
    def to_evidence(pack: dict) -> str:
        return json.dumps(pack, ensure_ascii=False, sort_keys=True)


def _safe_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v


def new_payment(cart: Mandate, issuer: str, amount_fen: int,
                pub: str = "", sig: str = "") -> Mandate:
    """从 cart 生成 payment 凭证的骨架（签名由持有钥匙的一方补）。"""
    if cart.kind != CART:
        raise MandateError("payment 必须由 cart 生成")
    return Mandate(kind=PAYMENT, subject=cart.subject, agent=cart.agent, issuer=issuer,
                   scope={"amount_fen": int(amount_fen)}, parent=cart.id, pub=pub, sig=sig)


def new_cart(intent: Mandate, items: list[dict], issuer: str | None = None,
             pub: str = "", sig: str = "") -> Mandate:
    total = sum(int(i.get("amount_fen", 0)) for i in items)
    return Mandate(kind=CART, subject=intent.subject, agent=intent.agent,
                   issuer=issuer or intent.agent, parent=intent.id, pub=pub, sig=sig,
                   scope={"items": items, "total_fen": total})
