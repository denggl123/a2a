"""Exercise useful schema-declared Agents on the four existing installed nodes."""
import json
from pathlib import Path
import uuid
from local_business_acceptance import api, start_agent, AGENT_NAME, AGENT_PORT, get

ROOT = Path(__file__).resolve().parents[1]
NODES = ("desktop", "node-a", "node-b", "node-c")
SID = "text-inspector-guided"


def main():
    before_count = start_agent()["counts"]["successful_inspections"]
    runtimes = {n: api(n, "/v1/runtime") for n in NODES}
    point_orders = {n: len(api(n, "/v1/points")["orders"]) for n in NODES}
    cards, projections, calls = {}, {}, []
    for node in NODES:
        status = api(node, "/v1/onboarding")
        assert status["node_did"] == runtimes[node]["node_did"]
        card = {"name": "文本结构分析（填写式）", "version": "1.0.0",
            "description": "代码 Agent：处理文字，返回字符、行、段落、关键词和摘要，不调用外部模型。",
            "skills": [{"id": "inspect", "name": "分析文本", "input_schema": {"type": "object",
                "properties": {"text": {"type": "string", "title": "要分析的文字", "minLength": 1,
                    "maxLength": 20000, "description": "前十次免费服务一定公开脱敏样品，请使用可公开文字。"}},
                "required": ["text"]}}]}
        endpoint = f"http://127.0.0.1:{AGENT_PORT}/invoke" if node == "desktop" else f"http://{AGENT_NAME}:9000/invoke"
        api(node, "/v1/bindings/http", {"card": card, "service_id": SID, "endpoint": endpoint, "protocol": "json", "listed": True})
        cards[node] = api(node, "/v1/publish", {"service_id": SID})["card"]
        assert cards[node]["x-a2n"]["settlement"]["protocol"] == "a2n-settlement/1"
        print("PASS new installed modules and signed schema card " + node, flush=True)
    for provider in ("node-a", "node-b", "node-c", "desktop"):
        buyer = "node-c" if provider == "desktop" else "desktop"
        projection = api(buyer, "/v1/projections", {"card": cards[provider],
            "projection_id": "guided-use-" + provider})["projection_id"]
        projections[provider] = {"buyer": buyer, "projection_id": projection}
        task = "completion-" + uuid.uuid4().hex
        offer = api(buyer, "/v1/trades/quote", {"projection_id": projection,
            "request": {"task_id": task, "skill": "inspect", "payload": {"text": "这是可以公开的验收文字。\n\nAgents provide useful services."}}})
        assert offer["payment_state"] == "NOT_REQUIRED" and offer["free_reason"] == "FREE_INITIAL"
        result = api(buyer, "/v1/trades/free-execute", {"offer_id": offer["offer_id"]})
        assert result["ok"] and result["metadata"]["verified_delivery"] and result["settlement"]["state"] == "NOT_REQUIRED"
        assert result["result"]["text_sha256"] and result["result"]["paragraphs"] == 2
        assert api(buyer, "/v1/trades/free-execute", {"offer_id": offer["offer_id"]})["result"] == result["result"]
        calls.append({"buyer": buyer, "provider": provider, "task_id": task, "verified_delivery": True,
            "settlement": "NOT_REQUIRED", "original_replay": True})
        print("PASS actual code Agent and original retry " + buyer + " -> " + provider, flush=True)
    assert get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] == before_count + 4
    assert {n: len(api(n, "/v1/points")["orders"]) for n in NODES} == point_orders
    report = {"passed": True, "actual_executions": 4, "replays": 4, "nodes": {n: runtimes[n]["node_did"] for n in NODES},
        "service_id": SID, "projections": projections, "calls": calls, "point_orders_unchanged": True}
    (ROOT / "artifacts/completion-live-business.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
