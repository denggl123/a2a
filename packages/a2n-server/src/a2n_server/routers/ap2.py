"""AP2 集成接口：授权翻译、结算凭证、授权链校验。

三个端点对应三个扩展，一一对应，不多不少：
  POST /budget    Intent 授权 → A2N 冻结预算（扩展一）
  POST /receipt   A2N 结算 → 可回填 Payment Mandate 的凭证（扩展二）
  POST /validate  授权链机器校验（链完整性 / 时效 / 金额 / 签名）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from a2n_ap2 import (AccountabilityPack, BudgetTranslator, Mandate,
                     MandateError, ReceiptBuilder, validate_chain)
from a2n_store import conn
from a2n_task import tasks

router = APIRouter(prefix="/v1/ap2", tags=["ap2"])


class BudgetIn(BaseModel):
    intent: dict                    # AP2 IntentMandate（或其 JSON 形态）
    skill: str
    price_hint_fen: int | None = None


class ValidateIn(BaseModel):
    payment: dict
    cart: dict | None = None
    intent: dict | None = None


class ReceiptIn(BaseModel):
    task_id: str
    payment_id: str | None = None   # 提供则把凭证挂回该 payment 凭证一并返回


def _mandate(d: dict, kind_hint: str) -> Mandate:
    try:
        return Mandate(**d)
    except TypeError as e:
        raise MandateError(f"{kind_hint} 凭证字段不合法：{e}")


@router.post("/budget")
def budget(body: BudgetIn):
    """把用户的 AP2 授权翻译成 A2N 可冻结的预算。

    返回的 dict 可直接喂给 POST /v1/tasks（skill/budget 同名）——
    授权不落地成冻结，就只是口头承诺。
    """
    try:
        intent = _mandate(body.intent, "intent")
        return BudgetTranslator.from_intent(intent, body.skill, body.price_hint_fen)
    except MandateError as e:
        raise HTTPException(400, str(e))


@router.post("/validate")
def validate(body: ValidateIn):
    try:
        payment = _mandate(body.payment, "payment")
        cart = _mandate(body.cart, "cart") if body.cart else None
        intent = _mandate(body.intent, "intent") if body.intent else None
        return validate_chain(payment, cart, intent)
    except MandateError as e:
        raise HTTPException(400, str(e))


@router.post("/receipt")
def receipt(body: ReceiptIn):
    """A2N 结算 → AP2 结算凭证（扩展二）。

    四件套证据：结果哈希 / 双向计量 / 分账明细 / 公证章 + epoch。
    AP2 的问责链到了 A2N 这一环不断。
    """
    t = tasks.get(body.task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    row = conn().execute(
        "SELECT * FROM settlement_orders WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (body.task_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "该任务尚未结算，没有凭证可出")

    notary_rid = _latest_notary_rid(body.task_id)
    epoch_id = _latest_anchored_epoch()
    usage = _usage_of(body.task_id)

    settlement = {"amount": row["amount"],
                  "splits": json.loads(row["splits"]),
                  "rule_ref": json.loads(row["rule_ref"])}
    pack = ReceiptBuilder.build(t, settlement, notary_rid=notary_rid,
                                epoch_id=epoch_id, usage=usage)

    out: dict = {"receipt": pack, "receipt_digest": ReceiptBuilder.digest(pack)}
    if body.payment_id:
        # 回填：payment 凭证由调用方持有，这里给出挂接后的 digest 供其比对
        out["payment_id"] = body.payment_id
        out["attach_hint"] = "调用 ReceiptBuilder.attach(payment, receipt) 完成回填"
    return out


class AccountabilityIn(BaseModel):
    task_id: str
    dispute_id: str | None = None


@router.post("/accountability")
def accountability(body: AccountabilityIn):
    """争议时的一页纸材料：自报计量 vs 平台观测 + 信誉快照 + 凭证。"""
    t = tasks.get(body.task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    row = conn().execute(
        "SELECT * FROM usage_reports WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (body.task_id,)).fetchone()
    rep = _latest_notary_rid(body.task_id)
    from a2n_registry import registry
    agent = registry.get(t["node_id"]) or {}
    pack = AccountabilityPack.build(t, dict(row) if row else None,
                                    reputation=agent.get("reputation"),
                                    dispute_id=body.dispute_id, notary_rid=rep)
    return pack


def _latest_notary_rid(task_id: str) -> str | None:
    row = conn().execute(
        "SELECT rid FROM receipts WHERE payload LIKE ? AND event_type IN"
        " ('settlement.settled','acceptance.failed','task.submitted')"
        " ORDER BY seq DESC LIMIT 1", (f'%{task_id}%',)).fetchone()
    return row["rid"] if row else None


def _latest_anchored_epoch() -> str | None:
    row = conn().execute(
        "SELECT epoch_id FROM epochs WHERE state='ANCHORED' ORDER BY seq DESC LIMIT 1").fetchone()
    return row["epoch_id"] if row else None


def _usage_of(task_id: str) -> dict:
    row = conn().execute(
        "SELECT dims, observed, variance FROM usage_reports WHERE task_id=?"
        " ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    if not row:
        return {}
    try:
        return {"dims": json.loads(row["dims"]),
                "observed": json.loads(row["observed"] or "{}"),
                "variance": row["variance"]}
    except (ValueError, TypeError):
        return {}
