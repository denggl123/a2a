"""演示数据：充值 → 注册多个节点 → 发现 → 发任务 → 执行 → 分账 → 提现。

用法：先启动服务（uvicorn a2n_server.app:app），再运行本脚本。
"""
from __future__ import annotations

import sys
import time


from a2n_sdk.client import Client  # noqa: E402

BASE = "http://127.0.0.1:8137"
USER = "acct:alice"
PROVIDER = "acct:bob"


def card(name: str, skill: str, price: int, gpu: str = "4090", region: str = "cn-east-2",
         extra_dims: list | None = None) -> dict:
    dims = [{"key": "call_count", "unit": "call", "verifiable": True},
            {"key": "gpu_seconds", "unit": "s*gpu", "verifiable": False}]
    dims += extra_dims or []
    return {
        "name": name, "version": "1.0.0", "url": "http://127.0.0.1:9000/a2a",
        "skills": [{"id": skill, "name": skill, "tags": ["demo"], "inputModes": ["application/json"],
                    "outputModes": ["application/json"]}],
        "x-a2n": {
            "compute": {"gpu": gpu, "vram_gb": 24, "cpu_cores": 16, "region": region, "concurrency": 4},
            "sla": {"max_latency_ms": 5000, "availability_target": 0.95, "max_concurrent": 4},
            "price_hint": {skill: {"amount": price, "unit": "point_per_call"}},
            "metering": {"dimensions": dims},
        },
    }


def main() -> None:
    user = Client(BASE, principal=USER)
    provider = Client(BASE, principal=PROVIDER)

    print("1) 充值（持牌方回调）")
    print("  ", user.deposit(USER, 10000))  # ¥100
    print("  ", user.deposit(PROVIDER, 2000))

    print("2) 注册 3 个节点")
    nodes = []
    for name, skill, price in [("bob-ocr-01", "ocr-pro", 1),
                               ("bob-translate-01", "translate-v2", 2),
                               ("bob-ocr-02", "ocr-pro", 3)]:
        c = provider.register(card(name, skill, price))
        nodes.append(c["agent_id"])
        print(f"   {c['agent_id']} {name} 状态={c['status']}")

    print("3) 使用方按需发现")
    found = user.discover("ocr-pro")
    for a in found:
        print(f"   {a['agent_id']} {a['name']} 信誉={a['reputation']} 价={a['price_hint']}")
    if found:
        print("   加入我的市场列表:", user._req("POST", f"/v1/roster/{found[0]['agent_id']}", {}))

    print("4) 发任务 + 节点执行")
    node_client = Client(BASE, principal=PROVIDER, node_id=nodes[0])
    for skill in ("ocr-pro", "translate-v2", "ocr-pro"):
        task = user.create_task(skill, {"text": "hello a2n", "pages": 3}, budget=100)
        print(f"   任务 {task['id']} -> {task['node_id']}")
        started = time.time()
        time.sleep(0.2)
        usage = node_client.meter(started, call_count=1, output_tokens=512,
                                  gpu_seconds=0.6, page_count=3)
        res = Client(BASE, principal=PROVIDER, node_id=task["node_id"]).submit(
            task["id"], {"out": "HELLO A2N"}, usage)
        print(f"   实付 {res.get('amount')} 积分 · 验收 {res.get('passed')}")

    print("5) 节点提现")
    bal = provider.balance(nodes[0])
    if bal > 0:
        wd = Client(BASE, principal=nodes[0]).withdraw(bal)
        print("  ", wd)
        print("  ", user._req("POST", "/v1/custodian/payout-callback",
                              {"withdrawal_id": wd["withdrawal_id"]}))

    print("6) 对账")
    print("  ", user._req("GET", "/v1/ops/reconcile"))
    print("  ", user._req("POST", "/v1/ledger/verify"))
    print("  ", user._req("POST", "/v1/notary/verify"))
    print("\n完成：打开 http://127.0.0.1:8000/console 查看管理台")


if __name__ == "__main__":
    main()
