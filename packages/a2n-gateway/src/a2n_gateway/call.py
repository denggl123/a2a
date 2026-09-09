"""调用编排：入口无关的一条链。

    A2A / 内部 API / 未来的别的协议
        → 门禁（收费才验支付能力）
        → 建一等公民任务（tasks，source 标记入口）
        → 经通道执行（隧道/中继）
        → 提交 + 验收（a2n-acceptance，事件进公证与信誉）
        → 按结算方式记账（deals / pay_charges / 积分）

**这一层存在的唯一理由**：以前 A2A 入口是自己另起一套（单独建表、自己记状态、
不过验收、不发事件），于是"A2N 主张调用+验收+多分账"，而流量最大的入口
恰好绕过了验收与公证。编排下沉到这里之后，**换入口不换规矩**：
任何协议进来都走同一条链，验收、公证、信誉、仲裁一个都少不了。

编排刻意不知道任何协议细节（contextId / artifact 之类留给各自的适配层），
它只产出规范事实：task_id + 结果 + 验收结论 + 记账凭据。
"""
from __future__ import annotations

from typing import Any

from a2n_account import PEER_ACCOUNT, PREPAID_POINTS
from a2n_registry import registry
from a2n_settlement import unit_price_of
from a2n_task import tasks
from a2n_transport import hub

from .gate import FREE, choose_currency, resolve, unit_price_fen
from .settle import dispatch

USAGE_DIMS = {"call_count": 1}        # 一期只计调用次数（可验证维度）


class CallResult:
    """一次调用的规范结论。协议适配层拿它去渲染各自的视图。"""

    def __init__(self, task_id: str, state: str, result: Any = None,
                 error: dict | None = None, settle: dict | None = None,
                 verdict: dict | None = None, capability: str = "none",
                 amount_fen: int = 0, currency: str | None = None) -> None:
        self.task_id = task_id
        self.state = state              # 规范状态（A2N 词汇）
        self.result = result
        self.error = error
        self.settle = settle or {}
        self.verdict = verdict or {}
        self.capability = capability
        self.amount_fen = amount_fen
        self.currency = currency        # 本次成交的结算币种（免费为 None）

    @property
    def ok(self) -> bool:
        return self.state in ("ACCEPTED", "SETTLED")

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "state": self.state, "ok": self.ok,
                "result": self.result, "error": self.error, "settle": self.settle,
                "capability": self.capability, "amount_fen": self.amount_fen,
                "currency": self.currency}


def _card_of(agent_id: str) -> dict:
    a = registry.get(agent_id)
    if not a:
        raise ValueError("agent 不存在")
    import json
    return json.loads(a["card_json"] or "{}")


def invoke(agent_id: str, principal: str, skill: str = "", message: dict | None = None,
           payload: Any = None, payment: dict | None = None, source: str = "a2a",
           settle_points: bool = False, currency: str | None = None) -> CallResult:
    """执行一次调用。任何入口都走这里。

    settle_points：是否走 A2N 积分结算（二期预付费模式）。
    一期对等账户与直付都不动积分 —— 积分是钱，没冻结就不能结算。
    currency：想用什么币种结（agent 价目表里必须有这个币种；不指定则默认
    CNY，价目表没有 CNY 时取它的第一个币种）。选币规则见 gate.choose_currency。
    """
    if not principal:
        raise ValueError("缺少调用主体")
    card = _card_of(agent_id)
    skill = skill or ((card.get("skills") or [{}])[0].get("id") or "")
    cur = choose_currency(card, skill, currency)
    # 单价必须跟着选出的币种走：USDC 单价绝不能当 CNY 用
    unit = unit_price_of(card, skill, cur) if cur else 0
    resource = f"/a2a/{agent_id}"

    # ① 门禁：免费放行；收费则必须有可用支付方式（x402 无凭证 → 402 挑战）
    cap = resolve(agent_id, principal, card, payment=payment, resource=resource,
                  currency=cur)

    # ② 建一等公民任务：budget 只作价格上限（防事后涨价），不冻结积分。
    # delivery="inline"：这份活由 ③ 的通道转发就地交付、由这里代节点提交，
    # 绝不进推送/取活通道 —— 否则同一份活被干两遍，账就成了一笔糊涂账。
    task = tasks.create(requester_id=principal, skill=skill,
                        payload={"message": message, "payload": payload},
                        budget=unit, preferred_agents=[agent_id], source=source,
                        hold_budget=False, delivery="inline", currency=cur or "CNY")

    # ③ 经通道执行
    out = hub.forward(agent_id, "POST", "/invoke",
                      {"message": message, "payload": payload, "skill": skill},
                      caller=principal)
    if out.get("error"):
        # 送不到/没回音：如实记为失败，不假装完成，也不记账
        tasks.fail(task["id"], f"通道不可达：{out.get('error')}")
        return CallResult(task["id"], "FAILED", error=out, capability=cap.token)
    if int(out.get("status", 200)) >= 400:
        body = out.get("body") or {}
        reason = body.get("error") if isinstance(body, dict) else str(body)
        tasks.fail(task["id"], f"节点执行失败：{reason}")
        return CallResult(task["id"], "FAILED",
                          error={"error": reason or "upstream error",
                                 "status": out.get("status")},
                          capability=cap.token)

    body = out.get("body", out)
    result = body.get("body") if isinstance(body, dict) and "body" in body else body

    # ④ 提交 + 验收：事件在这里发出去，公证与信誉自动跟上
    submitted = tasks.submit(task["id"], agent_id, result,
                             {"dims": dict(USAGE_DIMS)},
                             settle=settle_points or cap.mode == PREPAID_POINTS)
    if not submitted.get("passed"):
        return CallResult(task["id"], "REJECTED", result=result,
                          error={"reasons": submitted.get("reasons") or []},
                          verdict=submitted, capability=cap.token)

    # ⑤ 按结算方式记账（免费 = 不记账）。币种用门禁选出的结算币种：
    # 任务、凭证、账单三处必须是同一个币种，否则对账时三本账对不上。
    amount = int(submitted.get("amount") or 0)
    settle_rec = {"kind": "none"}
    if cap.mode is not None:
        settle_rec = dispatch(cap, task, agent_id, principal, skill, amount or unit,
                              currency=(cap.currency or cur or "CNY"))

    return CallResult(task["id"], "SETTLED" if settle_points else "ACCEPTED",
                      result=result, settle=settle_rec, verdict=submitted,
                      capability=cap.token, amount_fen=amount,
                      currency=cap.currency or cur)
