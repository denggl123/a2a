"""容器里的一个 A2N 节点 —— 用来验证"跨网络、跨主机"的互通。

全程只有出站连接：注册、心跳、建隧道、回包都由容器主动连平台。
容器不映射任何端口，外面打不进来；外部调用经平台 relay 入口 → 隧道 → 容器内本地服务。

环境变量：
  A2N_PLATFORM      平台地址（容器内用 http://host.docker.internal:8000）
  A2N_NAME/SKILL    节点名与能力 id
  A2N_PRINCIPAL     主体
  A2N_REGION        部署属地（数据驻留/合规；算力是黑盒不声明）
  A2N_LOCAL_PORT    容器内本地服务端口
"""
from __future__ import annotations

import os
import socket
import time

from a2n_sdk import Node

PLATFORM = os.environ.get("A2N_PLATFORM", "http://host.docker.internal:8000")
NAME = os.environ.get("A2N_NAME", "docker-agent")
SKILL = os.environ.get("A2N_SKILL", "ocr-pro")
PRINCIPAL = os.environ.get("A2N_PRINCIPAL", "acct:docker")
REGION = os.environ.get("A2N_REGION", "cn-docker")
LOCAL_PORT = int(os.environ.get("A2N_LOCAL_PORT", "8787"))
LATENCY = int(os.environ.get("A2N_MAX_LATENCY_MS", "3000"))
PRICE = int(os.environ.get("A2N_PRICE_FEN", "3"))


def _respond(path: str, payload: dict) -> dict:
    """真正的能力本体。返回里带上 hostname，让人一眼看出是哪个容器干的活。"""
    return {
        "skill": SKILL,
        "by": NAME,
        "hostname": socket.gethostname(),
        "path": path,
        "echo": payload,
        "at": time.strftime("%H:%M:%S"),
    }


# 两个签名各就各位：
#   handle(payload)       —— 平台派单（隧道/长轮询）的任务处理器
#   handle_http(path, payload) —— 本地 HTTP 服务（relay 中继转发）的入口
# 以前两个都挂同一个 fn(path, payload)：派单路径进来必 TypeError（静默卡死）。
def handle(payload: dict) -> dict:
    return _respond("/task", payload)


def handle_http(path: str, payload: dict) -> dict:
    return _respond(path, payload)


card = {
    "name": NAME, "version": "1.0.0", "url": f"http://{socket.gethostname()}:{LOCAL_PORT}/a2a",
    "accepts": ["peer_account"],
    "skills": [{"id": SKILL, "name": SKILL, "tags": ["docker", SKILL],
                "inputModes": ["application/json"]}],
    "x-a2n": {
        "deployment": {"region": REGION},
        "sla": {"max_latency_ms": LATENCY},
        "price_hint": {SKILL: {"amount": PRICE, "unit": "fen_per_call"}},
        "metering": {"dimensions": [{"key": "call_count", "verifiable": True},
                                    {"key": "output_tokens", "verifiable": True}]},
    },
}

if __name__ == "__main__":
    print(f"[docker-agent] {NAME} skill={SKILL} -> {PLATFORM}", flush=True)
    Node(card, {SKILL: handle}, PRINCIPAL, PLATFORM).serve(
        local_agent=(LOCAL_PORT, handle_http), console=False)
