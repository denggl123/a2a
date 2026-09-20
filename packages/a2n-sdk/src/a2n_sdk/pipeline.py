"""调用业务层：网络、验收、结算的唯一编排点。"""
from __future__ import annotations

from typing import Any, Callable

from .ports import (AcceptancePort, AgentTarget, CallOutcome, CallRequest,
                    CallResponse, SettlementPort, TransportPort)


class DeliveryAcceptance:
    """保守默认：只确认对端声明交付成功，不冒充质量评估。

    真正的结构检查、AI 评审或人工规则通过 ``AcceptancePort`` 注入。默认结论里的
    ``quality_measured=False`` 是刻意的：网络通了不等于成品质量已经被测过。
    """

    def evaluate(self, target: AgentTarget, request: CallRequest,
                 response: CallResponse) -> dict[str, Any]:
        passed = bool(response.ok)
        return {
            "passed": passed,
            "policy": "delivery-only",
            "quality_measured": False,
            "reasons": [] if passed else [str(response.error or "对端未交付")],
        }


class NoSettlement:
    """默认不碰钱；明确返回未配置，而不是静默假装已结。"""

    def settle(self, target: AgentTarget, request: CallRequest,
               response: CallResponse, verdict: dict[str, Any]) -> dict[str, Any]:
        return {"state": "NOT_CONFIGURED", "mode": "none", "amount_minor": 0}


class CallbackAcceptance:
    """把现有验收函数接进端口，避免业务层反向依赖平台包。"""

    def __init__(self, fn: Callable[[AgentTarget, CallRequest, CallResponse], dict[str, Any]]):
        self.fn = fn

    def evaluate(self, target: AgentTarget, request: CallRequest,
                 response: CallResponse) -> dict[str, Any]:
        return dict(self.fn(target, request, response))


class CallbackSettlement:
    """把任意支付/双边记账实现接进端口。"""

    def __init__(self, fn: Callable[[AgentTarget, CallRequest, CallResponse,
                                    dict[str, Any]], dict[str, Any]]):
        self.fn = fn

    def settle(self, target: AgentTarget, request: CallRequest,
               response: CallResponse, verdict: dict[str, Any]) -> dict[str, Any]:
        return dict(self.fn(target, request, response, verdict))


class CallPipeline:
    """调用方流水线。

    纪律：网络失败不验收；验收失败不结算；结算异常必须作为 ``PENDING`` 返回，
    不能把已经发生的交付悄悄丢掉。
    """

    def __init__(self, transport: TransportPort,
                 acceptance: AcceptancePort | None = None,
                 settlement: SettlementPort | None = None) -> None:
        self.transport = transport
        self.acceptance = acceptance or DeliveryAcceptance()
        self.settlement = settlement or NoSettlement()

    def invoke(self, target: AgentTarget, request: CallRequest) -> CallOutcome:
        try:
            response = self.transport.invoke(target, request)
        except Exception as exc:  # 适配器边界：第三方错误必须归一化后再往上走
            response = CallResponse.failure(
                f"{type(exc).__name__}: {exc}", metadata={"stage": "transport"})

        if not response.ok:
            return CallOutcome(
                ok=False, task_id=request.task_id, state=response.state or "FAILED",
                result=response.result, error=response.error, usage=response.usage,
                receipt=response.receipt, target_ref=target.ref)

        try:
            verdict = dict(self.acceptance.evaluate(target, request, response))
        except Exception as exc:
            verdict = {"passed": False, "policy": "error",
                       "reasons": [f"验收器异常：{type(exc).__name__}: {exc}"]}
        if not verdict.get("passed"):
            return CallOutcome(
                ok=False, task_id=request.task_id, state="REJECTED",
                result=response.result, error={"reasons": verdict.get("reasons") or []},
                usage=response.usage, receipt=response.receipt, verdict=verdict,
                target_ref=target.ref)

        try:
            settlement = dict(self.settlement.settle(target, request, response, verdict))
        except Exception as exc:
            settlement = {"state": "PENDING", "mode": "unknown",
                          "reason": f"{type(exc).__name__}: {exc}"}

        settlement_state = str(settlement.get("state") or "").upper()
        settled = settlement_state in {"SETTLED", "CAPTURED"}
        if settled:
            outcome_state = "SETTLED"
            outcome_ok = True
        elif settlement_state in {"PENDING", "PROCESSING"}:
            outcome_state = "SETTLEMENT_PENDING"
            outcome_ok = True       # 交付已验收；钱的状态单独表达，结果仍可使用
        elif settlement_state in {"FAILED", "REJECTED", "CANCELED", "CANCELLED"}:
            outcome_state = "SETTLEMENT_FAILED"
            outcome_ok = False
        else:
            outcome_state = "ACCEPTED"
            outcome_ok = True
        return CallOutcome(
            ok=outcome_ok, task_id=request.task_id, state=outcome_state,
            result=response.result,
            error=(settlement.get("reason") if not outcome_ok else None),
            usage=response.usage, receipt=response.receipt,
            verdict=verdict, settlement=settlement, target_ref=target.ref)
