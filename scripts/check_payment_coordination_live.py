"""Verify the installed four nodes and one free coordinated call per node."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from network_acceptance import api


def main():
    nodes=[]
    for name in ("desktop","node-a","node-b","node-c"):
        status=api(name,"/v1/payment-coordination")
        runtime=api(name,"/v1/runtime")
        assert status["implemented"] and status["platform_commission_minor"]==0
        assert status["automatic_payment"] is False and status["unknown_locks_order"]
        assert status["wallet_address"] is None and not status["orders"]
        projections=runtime["projections"]
        target=next(p for p in projections if "Network HTTP arithmetic" in p["name"])
        offer=api(name,"/v1/payment-coordination/quote",{"projection_id":target["projection_id"],
            "request":{"task_id":"coord-free-"+uuid.uuid4().hex,"skill":"add","payload":{"a":5,"b":9}}})
        assert offer["payment_state"]=="NOT_REQUIRED" and not offer["options"]
        outcome=api(name,"/v1/payment-coordination/free-execute",{"offer_id":offer["offer_id"]})
        assert outcome["ok"] and outcome["result"]=={"sum":14}
        assert outcome["metadata"]["verified_delivery"]
        assert outcome["settlement"]["state"]=="NOT_REQUIRED"
        assert outcome["metadata"]["trade_facts"]["payment"]=="NOT_REQUIRED"
        after=api(name,"/v1/payment-coordination")
        assert not after["orders"]
        nodes.append({"node":name,"did":runtime["node_did"],"passed":True,
            "module":status["version"],"wallet":"NOT_CONFIGURED","commission_minor":0,
            "free_quote":offer["free_reason"],"signed_delivery":True,"payment_orders":0})
    report={"at":datetime.now(timezone.utc).isoformat(),"passed":True,"nodes":nodes,
        "scope":"Installed Windows bundle and three production Docker nodes; real signed free calls. Paid channels verified separately on a private EVM chain."}
    (Path(__file__).resolve().parents[1]/"artifacts/payment-coordination-live-2026-10-08.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
