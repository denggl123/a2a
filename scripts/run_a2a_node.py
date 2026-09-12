"""A2A 协议层节点：本地服务额外挂 /invoke，承接 JSON-RPC message/send 的中继转发。

与 run_node.py 的区别：run_node 只演示平台→隧道的通用中继（/echo）；
本节点是**标准 A2A 客户端能直接调用**的节点——平台的 /a2a/{agent_id}
(message/send) 会把消息经隧道转发到本机 /invoke，就地跑技能并回包。

同一脚本用 A2N_ROLE 起三种**不一样**的节点，让演示里的市场不是三个克隆体：
    A2N_ROLE=charging  华东·按次计费（CNY，对等账户 + 直付渠道）
    A2N_ROLE=free      华北·公益免费（不声明 accepts、不标价）
    A2N_ROLE=x402      新加坡·请求即付（USDC，只收 x402 微支付）

环境变量：
    A2N_ROLE        预设档位（见上，默认 charging）
    A2N_LOCAL_PORT  本地服务端口（默认 9102）
    A2N_PRINCIPAL   节点主体（默认 acct:bob）
    A2N_FREE=1      强制免费（等价 free 档，向后兼容）
    A2N_SKILL       主技能 id（默认 ocr-pro；冒烟靠它找到节点，改前先改冒烟）
    A2N_NODE_NAME / A2N_REGION / A2N_LATENCY / A2N_PRICE / A2N_PRICE_CUR
                    逐项覆盖预设（都填 ASCII，中文描述写在预设表里）

启动：
    A2N_DB=data/e2e_a2a.db ./.venv/Scripts/python.exe scripts/run_a2a_node.py
"""
from __future__ import annotations

import os
import time
from decimal import Decimal

from a2n_sdk import Node

LOCAL_PORT = int(os.environ.get("A2N_LOCAL_PORT", "9102"))
PRINCIPAL = os.environ.get("A2N_PRINCIPAL", "acct:bob")
FREE_FORCED = os.environ.get("A2N_FREE") == "1"
SKILL = os.environ.get("A2N_SKILL", "ocr-pro")

# 币种指数：表单按主单位填，卡上按最小单位存 —— 换算只在这里发生
_CUR_EXP = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "JPY": 0, "USDC": 6}


def _minor(price: str, cur: str) -> int:
    return int((Decimal(price) * (10 ** _CUR_EXP.get(cur, 2))).to_integral_value())


# ---------------------------------------------------------------- 技能实现
def _ocr(payload) -> dict:
    # 标准 A2A 的 text part 进来是字符串，data part 进来是 dict —— 都得接住
    if isinstance(payload, str):
        text, pages = payload, 1
    else:
        text, pages = str((payload or {}).get("text", "")), (payload or {}).get("pages", 1)
    return {"text": text.upper(), "pages": pages}


def _deskew(payload) -> dict:
    body = payload if isinstance(payload, dict) else {}
    return {"skew_deg": body.get("skew_deg", 0.0), "straightened": True,
            "note": "演示实现：只回执纠偏结果，不真的处理图像"}


# 技能目录：id → (展示名, 标签, 处理函数, 计量维度)
SKILL_CATALOG = {
    "ocr-pro": ("OCR 识别", ["ocr", "document", "table"], _ocr,
                [("call_count", "call"), ("page_count", "page")]),
    "doc-deskew": ("文档纠偏", ["ocr", "preprocess"], _deskew,
                   [("call_count", "call")]),
    "ocr-batch": ("批量 OCR", ["ocr", "batch", "high-throughput"], _ocr,
                  [("call_count", "call")]),
}

# ---------------------------------------------------------------- 档位预设
PRESETS = {
    "charging": {
        "name": "华东-精算OCR", "region": "cn-east-2",
        "latency": 5000, "availability": 0.95, "concurrent": 4, "sleep": 0.3,
        "price": "0.03", "cur": "CNY", "accepts": ["peer_account", "direct_pay:alipay"],
        "skills": ["ocr-pro"], "tags": ["ocr", "发票", "合同"],
        "desc": "高精度版面还原：发票 / 合同 / 表格，返回结构化字段。按次计费，"
                "对等账户（先用后结）与直付渠道都能结算。",
    },
    "free": {
        "name": "华北-公益OCR", "region": "cn-north-1",
        "latency": 8000, "availability": 0.90, "concurrent": 2, "sleep": 0.2,
        "price": None, "cur": None, "accepts": [],
        "skills": ["ocr-pro", "doc-deskew"], "tags": ["ocr", "试用", "低价"],
        "desc": "公益版 OCR：免费开放，适合小批量试用与联调。不承诺 SLA，"
                "别放在产线关键路径上。",
    },
    "x402": {
        "name": "新加坡-极速OCR", "region": "ap-southeast-1",
        "latency": 1500, "availability": 0.99, "concurrent": 8, "sleep": 0.05,
        "price": "0.05", "cur": "USDC", "accepts": ["x402"],
        "skills": ["ocr-pro", "ocr-batch"], "tags": ["ocr", "高频", "海外"],
        "desc": "海外极速节点：请求即付（x402），不用预先建立账户关系，"
                "适合高频小额的流水线场景。",
    },
}

ROLE = os.environ.get("A2N_ROLE", "free" if FREE_FORCED else "charging")
if ROLE not in PRESETS:
    raise SystemExit(f"A2N_ROLE 只认 {sorted(PRESETS)}，收到 {ROLE!r}")
P = dict(PRESETS[ROLE])

# 逐项覆盖（全部 ASCII，避免中文过 shell）
NAME = os.environ.get("A2N_NODE_NAME") or P["name"]
REGION = os.environ.get("A2N_REGION") or P["region"]
LATENCY = int(os.environ.get("A2N_LATENCY") or P["latency"])
PRICE = os.environ.get("A2N_PRICE", P["price"] or "")
PRICE_CUR = os.environ.get("A2N_PRICE_CUR", P["cur"] or "")
if FREE_FORCED:
    PRICE, PRICE_CUR, P["accepts"] = "", "", []

SKILL_IDS = [SKILL] + [s for s in P["skills"] if s != SKILL]

X_A2N = {
    "deployment": {"region": REGION},
    "sla": {"max_latency_ms": LATENCY, "availability_target": P["availability"],
            "max_concurrent": P["concurrent"]},
    "metering": {"dimensions": [
        {"key": k, "unit": u, "verifiable": True}
        for s in SKILL_IDS for k, u in SKILL_CATALOG[s][3]
    ]},
}
if PRICE and PRICE_CUR:
    # v2 价目表：价目事实只在这一处（v1 price_hint 已不再写入）
    X_A2N["price_book"] = {SKILL: {PRICE_CUR: {"dimensions": [
        {"key": "call_count", "amount": _minor(PRICE, PRICE_CUR), "per": 1}]}}}

CARD = {
    "name": NAME,
    "description": P["desc"],
    "version": "1.0.0",
    "url": None,
    "skills": [{"id": s, "name": SKILL_CATALOG[s][0], "tags": SKILL_CATALOG[s][1],
                "inputModes": ["application/json"], "outputModes": ["application/json"]}
               for s in SKILL_IDS],
    "x-a2n": X_A2N,
}
ACCEPTS = [s.strip() for s in os.environ.get("A2N_ACCEPTS", "").split(",") if s.strip()]
if ACCEPTS:
    CARD["accepts"] = ACCEPTS
elif P["accepts"]:
    CARD["accepts"] = list(P["accepts"])


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
        entry = SKILL_CATALOG.get(skill)
        if not entry or skill not in SKILL_IDS:
            raise ValueError(f"unknown skill {skill}")
        time.sleep(P["sleep"])  # 假装在做推理：各档位耗时不一样，好比较
        return entry[2](body or {})
    if path == "/echo":
        return {"echo": payload, "host": f"本机（{NAME}）", "ts": time.time()}
    return {"error": "unknown path", "path": path}


HANDLERS = {s: SKILL_CATALOG[s][2] for s in SKILL_IDS}

if __name__ == "__main__":
    node = Node(CARD, HANDLERS, principal=PRINCIPAL, base_url="http://127.0.0.1:8000")
    price_txt = f"{PRICE} {PRICE_CUR}/次" if PRICE else "免费"
    print(f"[a2n] A2A 节点启动：{NAME}（{ROLE} · {REGION} · {price_txt} · Ctrl+C 退出）")
    node.serve(console=False, local_agent=(LOCAL_PORT, local_api))
