"""x402 协议形状：402 挑战体与支付凭证。

为什么放在持牌层：**x402 是外部支付网络的协议**，网络、资产、收款地址
都是资金方的知识。A2N 业务层只该知道"要付多少、付给哪个资源"，
不该知道链、资产合约、收款地址——那些一变就得改业务代码的，都属于这里。

x402 的语义正好补上 A2N 缺的一块：**请求即付，无需预先建立任何关系**。
对等账户要谈条款、直付要登记渠道，而 x402 是"带凭证来就能调"，
小额高频调用（几分钱一次）的天然解。

协议流程：
    客户端调用 → 无凭证 → 402 + accepts（挑战）
    客户端带 X-PAYMENT 重试 → 本层校验 → 通过则放行 → 成功后 settle（真扣款）
"""
from __future__ import annotations

import base64
import json
from typing import Any

X402_VERSION = 1
PAYMENT_HEADER = "X-PAYMENT"
DEFAULT_SCHEME = "exact"
DEFAULT_MAX_TIMEOUT_S = 60


def build_requirement(resource: str, amount_fen: int, description: str = "",
                      scheme: str = DEFAULT_SCHEME,
                      network: str = "a2n-settlement",
                      asset: str = "CNY-FEN",
                      pay_to: str = "custodian",
                      max_timeout_seconds: int = DEFAULT_MAX_TIMEOUT_S) -> dict[str, Any]:
    """生成 x402 支付要求（402 响应体）。

    金额单位统一为分：A2N 全程用分，不引入第二种货币单位——
    单位一多，换算就是出错与套利的空间。
    """
    return {
        "x402Version": X402_VERSION,
        "accepts": [{
            "scheme": scheme,
            "network": network,
            "maxAmountRequired": str(int(amount_fen)),
            "resource": resource,
            "description": description or f"A2N 调用 {resource}",
            "mimeType": "application/json",
            "payTo": pay_to,
            "maxTimeoutSeconds": max_timeout_seconds,
            "asset": asset,
            "extra": {"unit": "fen"},
        }],
    }


def parse_payment(header_value: str | None) -> dict | None:
    """解析 X-PAYMENT 头（base64(JSON)）。垃圾输入返回 None，不抛异常。"""
    if not header_value:
        return None
    try:
        raw = base64.b64decode(header_value, validate=False)
        obj = json.loads(raw.decode())
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def encode_payment(payload: dict) -> str:
    """给客户端/测试用：把凭证对象编码成 X-PAYMENT 头的值。"""
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()


def amount_of(payment: dict) -> int:
    """凭证声明的已付金额（分）。"""
    try:
        return int(((payment or {}).get("payload") or {}).get("amount") or 0)
    except (ValueError, TypeError):
        return 0
