"""程序化调用入口：`POST /v1/invoke` —— 与 A2A 入口走**同一条治理链**。

为什么需要它：`a2n-gateway.call.invoke` 是那条唯一编排链（门禁 → 建任务 →
经通道执行 → 验收 → 按结算方式记账）。A2A JSON-RPC 是它的一个协议适配层；
SDK / 控制台这类"程序化调用方"如果没有对应入口，就会退化去走
`/v1/relay` 那条裸中继——而裸中继的收费语义只有对等账户（见 transport.py），
于是"绑了直付渠道"的使用方被 403 卡死，明明 A2A 入口能调通。

这个入口把"是不是有资格调"这件事交还给**同一个门禁**（gate.resolve）：
免费的放行；收费的看有没有可用支付方式——对等账户配对过、或绑定了该 agent
accepts 里的直付渠道（交集非空）、或该 agent 接受 x402（没带凭证就发 402 挑战）。
**对等账户是默认匹配，不是门槛**：agent 没声明 accepts 时按 peer_account 处理，
但绝不会因为"没配对"就把别的可用方式一并否掉。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from a2n_account import compatible_with
from a2n_gateway import PaymentRequired, invoke
from a2n_registry import registry

router = APIRouter(prefix="/v1", tags=["invoke"])


class InvokeIn(BaseModel):
    agent_id: str
    skill: str = ""
    payload: Any = None
    message: dict | None = None
    currency: str | None = None      # 想用什么币种结；不指定则按 agent 价目默认
    settle_points: bool = False      # 二期：走托管积分结算（需先冻结）


@router.post("/invoke")
async def invoke_agent(body: InvokeIn,
                       principal: str = Header(default="", alias="X-Principal"),
                       x_payment: str = Header(default="", alias="X-PAYMENT")) -> dict:
    """调用一个 agent，走统一治理链。返回规范结论（任务 id / 状态 / 结果 / 记账）。

    错误语义：
      402  该 agent 接受 x402 且没带凭证 → 挑战体在 `requirement`（带钱重来）
      403  收费且你还没有任何可用支付方式 → 带 `hint`（差哪样、补哪样）
      404  agent 不存在
    """
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    if not registry.get(body.agent_id):
        raise HTTPException(404, "agent 不存在")

    from a2n_custodian import parse_payment
    payment = parse_payment(x_payment)

    try:
        call = await run_in_threadpool(
            invoke, body.agent_id, principal, body.skill, body.message,
            body.payload, payment, "api", body.settle_points, body.currency)
    except PaymentRequired as e:
        # 不是"没资格"，是"还没付"——把挑战体原样交回，客户端带上凭证重来
        raise HTTPException(402, detail={"error": "payment required",
                                         "requirement": e.requirement})
    except PermissionError as e:
        # 没资格：把"对方收什么、你有什么、去补什么"一次说清（最小上手成本）
        raise HTTPException(403, detail={"error": str(e),
                                         "hint": compatible_with(body.agent_id, principal)})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return call.to_dict()
