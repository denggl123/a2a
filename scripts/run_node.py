"""用 SDK 起一个常驻节点：注册 → 反向隧道 → 接任务/中继转发 → 计量上报。

这是"SDK 留给 agent / 程序调用"的用法示例。全程只有出站连接：
家里电脑没有公网 IP、不开端口、不装 frp，一样接单，还能被公网调用。

启动后自带本地管理台：http://127.0.0.1:8770
中继转发演示：curl -X POST http://127.0.0.1:18787/v1/relay/<agent_id>/echo \\
              -H "Content-Type: application/json" -d '{"hello":"world"}'
              —— 请求经平台公网入口 → 隧道 → 本机 9101 服务，回包原路返回。
"""
from __future__ import annotations

import sys
import time


from a2n_sdk import Node  # noqa: E402

CARD = {
    "name": "sdk-node-ocr",
    "version": "1.0.0",
    "url": None,
    "skills": [{"id": "ocr-pro", "name": "OCR 识别", "tags": ["ocr"],
                "inputModes": ["application/json"], "outputModes": ["application/json"]}],
    "x-a2n": {
        "deployment": {"region": "cn-east-2"},
        "sla": {"max_latency_ms": 5000, "availability_target": 0.95, "max_concurrent": 4},
        "price_hint": {"ocr-pro": {"amount": 1, "unit": "point_per_call"}},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True},
                                    {"key": "page_count", "unit": "page", "verifiable": True},
                                    {"key": "gpu_seconds", "unit": "s*gpu", "verifiable": False}]},
    },
}


def handle_ocr(payload: dict) -> dict:
    time.sleep(0.3)  # 假装在做推理
    return {"text": str(payload.get("text", "")).upper(), "pages": payload.get("pages", 1)}


def local_api(path: str, payload: dict) -> dict:
    """本机 HTTP 服务：供平台中继转发调用（外部只认平台公网入口）。"""
    if path == "/echo":
        return {"echo": payload, "host": "本机 9101，经平台中继转发", "ts": time.time()}
    return {"error": "unknown path", "path": path}


if __name__ == "__main__":
    node = Node(CARD, {"ocr-pro": handle_ocr}, principal="acct:bob",
                base_url="http://127.0.0.1:18787")
    print("[a2n] 节点启动，Ctrl+C 退出；本地管理台 http://127.0.0.1:8770")
    node.serve(console=True, local_agent=(9101, local_api))
