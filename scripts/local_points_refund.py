"""Refund the original third-party issuer points after real four-node service."""
import json
from pathlib import Path
import uuid

from local_business_acceptance import api, wait, get, AGENT_PORT
from local_points_acceptance import ledgers

ROOT = Path(__file__).resolve().parents[1]


def run():
    acceptance = json.loads((ROOT / "artifacts/local-points-acceptance.json").read_text(encoding="utf-8"))
    assert acceptance["passed"]
    run_id = acceptance["run_id"]
    key = acceptance["three_hop_plan"]
    order = next(o for o in api("desktop", "/v1/points")["orders"] if o["record"]["plan_id"] == key)
    original = order["record"]["operations"][-1]
    assert original["issuer_did"] == acceptance["nodes"]["node-c"]
    dispute = api("desktop", "/v1/disputes/open", {"scope": "points-use-" + run_id + "-node-a",
        "task_id": run_id + "-three-hops", "reason": "本机积分验收：双方约定退回两份实收积分"})
    proposal = api("desktop", "/v1/disputes/" + dispute["id"] + "/messages", {"kind": "PROPOSAL",
        "body": {"action": "REFUND", "text": "按原单实际收到的发行方积分退款", "amount_minor": 2,
            "currency": "points:" + original["issuer_did"]}}, headers={"Idempotency-Key": run_id + "-refund-proposal"})

    def received():
        try:
            return api("node-a", "/v1/disputes/" + dispute["id"] + "/messages")
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            return {"messages": []}

    wait(received, lambda x: any(m["message_id"] == proposal["message_id"] for m in x["messages"]))
    for node in ("desktop", "node-a"):
        api(node, "/v1/disputes/" + dispute["id"] + "/agreements", {"proposal_id": proposal["message_id"]},
            headers={"Idempotency-Key": run_id + "-refund-" + node})
    for node in ("desktop", "node-a"):
        wait(lambda: api(node, "/v1/runtime"), lambda x: any(a["proposal"]["message_id"] == proposal["message_id"]
            and a["state"] == "BOTH_ACCEPTED" for a in x["resolution_agreements"]))
    before = ledgers()
    count = get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]
    refund = api("node-a", "/v1/points/refund", {"proposal_id": proposal["message_id"]})
    assert refund["state"] == "CONFIRMED" and refund["currency"] == "points:" + original["issuer_did"], refund
    expected = before
    expected[original["issuer_did"]][acceptance["nodes"]["node-a"]] -= 2
    buyer = acceptance["nodes"]["desktop"]
    ledger = expected[original["issuer_did"]]
    ledger[buyer] = ledger.get(buyer, 0) + 2
    after = ledgers()
    assert expected == after
    replay = api("node-a", "/v1/points/refund-reconcile", {"proposal_id": proposal["message_id"]})
    assert replay["reference"] == refund["reference"] and ledgers() == after
    assert get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] == count
    return {"passed": True, "checks": ["双方签名接受原积分退款", "退回原三跳交易实际收到的第三方积分",
        "所有发行账本精确回正", "重复退款核对不扣分或重跑 Agent"], "original_plan": key,
        "proposal_id": proposal["message_id"], "refund": refund}


if __name__ == "__main__":
    try:
        report = run()
    except Exception as exc:
        (ROOT / "artifacts/local-points-refund.json").write_text(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), encoding="utf-8")
        raise
    (ROOT / "artifacts/local-points-refund.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Actual four-node original points refund verified.")
