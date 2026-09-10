"""A2A 协议层节点：本地服务额外挂 /invoke，承接 JSON-RPC message/send 的中继转发。

与 run_node.py 的区别：run_node 只演示平台→隧道的通用中继（/echo）；
本节点是**标准 A2A 客户端能直接调用**的节点——平台的 /a2a/{agent_id}
(message/send) 会把消息经隧道转发到本机 /invoke，就地跑技能并回包。

环境变量（同一脚本可起收费/免费两种节点）：
    A2N_NODE_NAME   card 名称（默认 a2a-node-ocr）
    A2N_LOCAL_PORT  本地服务端口（默认 9102）
    A2N_PRINCIPAL   节点主体（默认 acct:bob）
    A2N_FREE=1      免费节点：不声明 accepts、不标价（发现开放，调用免门禁）
    A2N_SKILL       技能 id（默认 ocr-pro）

启动：
    A2N_DB=data/e2e_a2a.db ./.venv/Scripts/python.exe scripts/run_a2a_node.py
"""
from __future__ import annotations

import os
import time

from a2n_sdk import Node

NAME = os.environ.get("A2N_NODE_NAME", "a2a-node-ocr")
LOCAL_PORT = int(os.environ.get("A2N_LOCAL_PORT", "9102"))
PRINCIPAL = os.environ.get("A2N_PRINCIPAL", "acct:bob")
SKILL = os.environ.get("A2N_SKILL", "ocr-pro")
FREE = os.environ.get("A2N_FREE") == "1"

X_A2N = {
    "deployment": {"region": "cn-east-2"},
    "sla": {"max_latency_ms": 5000, "availability_target": 0.95, "max_concurrent": 4},
    "price_hint": {SKILL: {"amount": 1, "unit": "point_per_call"}},
    "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True},
                                {"key": "page_count", "unit": "page", "verifiable": True}]},
}
if FREE:
    # 免费：去掉价格与结算声明——发现照常可见，调用无需任何支付关系
    X_A2N.pop("price_hint")

CARD = {
    "name": NAME,
    "version": "1.0.0",
    "url": None,
    "skills": [{"id": SKILL, "name": "OCR 识别", "tags": ["ocr"],
                "inputModes": ["application/json"], "outputModes": ["application/json"]}],
    "x-a2n": X_A2N,
}
ACCEPTS = [s.strip() for s in os.environ.get("A2N_ACCEPTS", "").split(",") if s.strip()]
if not FREE:
    # 默认同时接受对等账户（双边记账）与直付（渠道限定符）——使用方补上任意
    # 一种即可调用；以后加微信/银联，只改这行数据，代码零改动。
    # A2N_ACCEPTS=x402 可起一个"只收微支付"的小额高频节点。
    CARD["accepts"] = ACCEPTS or ["peer_account", "direct_pay:alipay"]


def handle_ocr(payload) -> dict:
    time.sleep(0.3)  # 假装在做推理
    # 标准 A2A 的 text part 进来是字符串，data part 进来是 dict —— 都得接住
    if isinstance(payload, str):
        text, pages = payload, 1
    else:
        text, pages = str((payload or {}).get("text", "")), (payload or {}).get("pages", 1)
    return {"text": text.upper(), "pages": pages}


def local_api(path: str, payload: dict) -> dict:
    """本机服务：平台中继转发进来的所有 POST 都在这里落地。

    /invoke 语义 = 标准 A2A agent 的执行入口：拿到消息就地跑技能，
    返回体就是 A2A Task 的 artifact data（不包信封）；处理不了就抛错，
    平台会把 HTTP 500 如实映射成 failed Task，而不是假装完成。
    """
    if path == "/invoke":
        skill = payload.get("skill") or SKILL
        body = payload.get("payload")
        if body is None and payload.get("message"):
            # 从 A2A message parts 里取 data/text
            for p in payload["message"].get("parts", []):
                if "data" in p:
                    body = p["data"]
                    break
                if "text" in p:
                    body = p["text"]
                    break
        handler = {SKILL: handle_ocr}.get(skill)
        if not handler:
            raise ValueError(f"unknown skill {skill}")
        return handler(body or {})
    if path == "/echo":
        return {"echo": payload, "host": "本机，经 A2A 中继转发", "ts": time.time()}
    return {"error": "unknown path", "path": path}


if __name__ == "__main__":
    node = Node(CARD, {SKILL: handle_ocr}, principal=PRINCIPAL,
                base_url="http://127.0.0.1:8000")
    print(f"[a2n] A2A 节点启动（{'免费' if FREE else '收费'}，Ctrl+C 退出）")
    node.serve(console=False, local_agent=(LOCAL_PORT, local_api))
