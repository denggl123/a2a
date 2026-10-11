"""Four installed nodes, an actual HTTP Agent and all three settlement methods.

Controlled engineering calls, never independent-user calibration. Owner policies
are restored and the test listing is removed at the end. Ledgers and mandatory
public samples remain as the real records of these calls.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
import uuid

from local_business_acceptance import api, get, start_agent
from payment_testchain_check import rpc, request, balance

ROOT = Path(__file__).resolve().parents[1]
BUYERS = ("desktop", "node-b", "node-c")


def main():
    run = uuid.uuid4().hex[:10]
    service = "settlement-text-" + run
    checks, policies, projections = [], {}, {}
    mounted = False
    provider = "node-a"

    def check(name, condition, **facts):
        checks.append({"name": name, "passed": bool(condition), "facts": facts})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            raise AssertionError(name)

    def set_policy(node, prefs, automatic=True):
        current = api(node, "/v1/payment-coordination")["settlement_policy"]
        return api(node, "/v1/payment-coordination/policy", {"expected_revision": current["revision"],
            "provider_methods": current["provider_methods"], "buyer_preferences": prefs, "automatic": automatic})

    def call(node, task, text, automatic=None):
        return api(node, "/v1/trades/call", {"scope": projections[node], "automatic": automatic,
            "request": {"task_id": task, "skill": "inspect", "payload": {"text": text},
                        "message": {"role": "user", "messageId": task,
                            "parts": [{"kind": "data", "data": {"text": text}}]}}})

    def await_call(node, task):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            out = api(node, "/v1/trades/call?" + urlencode({"scope": projections[node], "task_id": task}))
            if out and out["metadata"]["automatic_trade"]["finished"]:
                return out
            time.sleep(.2)
        raise TimeoutError("AUTOMATIC_ORIGINAL_CALL_DID_NOT_FINISH")

    def inspections():
        return get("http://127.0.0.1:18910/counts")["counts"]["successful_inspections"]

    token = request("http://127.0.0.1:18946/status")["token"]
    direct = "http://127.0.0.1:18945"
    original_config = {"allow_http": True, "native": [{"currency": "TETH", "network": "eip155:31337",
        "rpc_url": direct, "confirmations": 1, "allow_http": True}], "facilitator_url": "http://127.0.0.1:18946",
        "x402": {"allow_http": True, "assets": [{"currency": "TST", "network": "eip155:31337", "asset": token,
            "name": "A2N Test Token", "version": "1", "rpc_url": direct, "confirmations": 1}]}}
    failure = None
    config_changed = False
    try:
        start_agent()
        for node in (provider, *BUYERS):
            status = api(node, "/v1/payment-coordination")
            check(node + " 本地测试钱包可用", status.get("test_environment", {}).get("kind") == "LOCAL_ANVIL"
                  and bool(status["wallet_address"]))
        for node in BUYERS:
            policies[node] = api(node, "/v1/payment-coordination")["settlement_policy"]
        card = {"name": "真实文本检查与自动结算验收", "version": "1", "description": "受控工程调用，公开样品不代表独立用户认可。",
            "skills": [{"id": "inspect", "name": "文本检查"}], "x-a2n": {"price_book": {"inspect": {
                currency: {"dimensions": [{"key": "call_count", "amount": amount, "per": 1}]}
                for currency, amount in (("TETH", 1234), ("TST", 10))}}}}
        api(provider, "/v1/bindings/http", {"card": card, "endpoint": "http://a2n-business-text-agent:9000/invoke",
            "service_id": service, "protocol": "json", "listed": True})
        mounted = True
        api(provider, "/v1/points/services", {"service_id": service, "expected_revision": 0,
            "policy": {"enabled": True, "amount": 5, "modes": ["EARN"], "debt_limit": 0}})
        card = api(provider, "/v1/publish", {"service_id": service})["card"]
        for node in BUYERS:
            projections[node] = "settlement-use-" + run
            api(node, "/v1/projections", {"projection_id": projections[node], "card": card})
        did = api(provider, "/v1/runtime")["node_did"]
        before_points = api(provider, "/v1/points")["balances"]
        start_count = inspections()
        for i in range(10):
            node = BUYERS[i % 3]
            text = "实际样品文本 " + run + " " + str(i)
            out = call(node, "settlement-sample-" + run + "-" + str(i), text, automatic=True)
            check("样品 " + str(i + 1) + " 实际交付且跳过结算", out["ok"]
                  and out["result"]["characters"] == len(text) and out["settlement"]["state"] == "NOT_REQUIRED")
        check("前十次没有增加积分", api(provider, "/v1/points")["balances"] == before_points)
        # Trial samples are protected management reads, not a hand-written fixture.
        samples = api(provider, "/v1/trials", {"service_id": service})
        check("十次技术交付全部公开为样品", samples["status"]["completed"] == 10 and len(samples["samples"]) == 10)
        for node in BUYERS:
            set_policy(node, [{"method": "a2n-points/1", "mode": "EARN", "max_amount_minor": "5"}])
            task = "settlement-earned-" + run + "-" + node
            out = call(node, task, "实际积分服务 " + node)
            check(node + " 普通调用自动积分结算", out["ok"] and out["settlement"]["state"] == "CONFIRMED")
            before = inspections()
            replay = call(node, task, "实际积分服务 " + node)
            check(node + " SDK 原任务重试没有重新调用", replay["result"] == out["result"] and inspections() == before)
        point_balance = next(r["amount"] for r in api(provider, "/v1/points")["balances"] if r["holder_did"] == did)
        previous_amount = next((r["amount"] for r in before_points if r["holder_did"] == did), 0)
        check("供应方三次真实服务发行自己的十五积分", point_balance == previous_amount + 15)
        set_policy("desktop", [{"method": "evm-native/1", "currency": "TETH",
            "max_amount_minor": "1234", "fee_cap_minor": "1000000000000000"}])
        proxy_config = json.loads(json.dumps(original_config))
        proxy_config["native"][0]["rpc_url"] = "http://127.0.0.1:18946/rpc-loss"
        api("desktop", "/v1/payment-coordination/configure", {"config": proxy_config})
        config_changed = True
        request("http://127.0.0.1:18946/faults/reset", {"fault": "rpc_response"})
        wallet = api("desktop", "/v1/payment-coordination")["wallet_address"]
        nonce = int(rpc("eth_getTransactionCount", [wallet, "latest"]), 16)
        task = "settlement-native-" + run
        initial = call("desktop", task, "真实转账响应丢失恢复")
        check("真实转账响应被丢弃且保留 UNKNOWN", initial["metadata"]["automatic_trade"]["state"] == "UNKNOWN")
        out = await_call("desktop", task)
        check("原生币丢响应后后台恢复同一订单", initial["metadata"]["automatic_trade"]["plan_id"] == out["metadata"]["automatic_trade"]["plan_id"]
            and out["ok"] and out["settlement"]["state"] == "CONFIRMED")
        check("原生币只有一次转账", int(rpc("eth_getTransactionCount", [wallet, "latest"]), 16) == nonce + 1)
        set_policy("node-b", [{"method": "x402/2", "currency": "TST", "max_amount_minor": "10"}])
        payee = api(provider, "/v1/payment-coordination")["wallet_address"]
        before = balance(token, payee)
        out = call("node-b", "settlement-x402-" + run, "真实 EIP-3009 服务调用")
        check("x402 实际代币到账并交付", out["ok"] and out["settlement"]["state"] == "CONFIRMED" and balance(token, payee) == before + 10)
        check("总共十五次真实 Agent 执行", inspections() == start_count + 15)
    except Exception as exc:
        failure = str(exc)
        raise
    finally:
        cleanup_errors = []
        for node, old in policies.items():
            try:
                current = api(node, "/v1/payment-coordination")["settlement_policy"]
                api(node, "/v1/payment-coordination/policy", {"expected_revision": current["revision"],
                    **{k: old[k] for k in ("provider_methods", "buyer_preferences", "automatic")}})
            except Exception as exc:
                cleanup_errors.append({"node": node, "error_type": type(exc).__name__})
        if config_changed:
            try:
                api("desktop", "/v1/payment-coordination/configure", {"config": original_config})
            except Exception as exc:
                cleanup_errors.append({"node": "desktop", "error_type": type(exc).__name__})
        if mounted:
            try:
                api(provider, "/v1/unpublish", {"service_id": service})
            except Exception as exc:
                cleanup_errors.append({"node": provider, "error_type": type(exc).__name__})
        report = {"at": datetime.now(timezone.utc).isoformat(), "run_id": run,
            "passed": failure is None and not cleanup_errors, "service_id": service, "checks": checks,
            "failure": failure, "cleanup_errors": cleanup_errors, "controlled_engineering_calls": True,
            "independent_human_calibration": False}
        (ROOT / "artifacts/automatic-settlement-installed.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if cleanup_errors and failure is None:
            raise RuntimeError("ACCEPTANCE_CLEANUP_REQUIRED")


if __name__ == "__main__":
    main()
