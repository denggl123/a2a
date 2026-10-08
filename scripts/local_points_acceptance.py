"""Real code Agent, installed Windows SDK and three persistent Docker nodes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from local_business_acceptance import api, start_agent, get, AGENT_NAME, AGENT_PORT

ROOT = Path(__file__).resolve().parents[1]
RUN = uuid.uuid4().hex[:10]
NODES = ("desktop", "node-a", "node-b", "node-c")
CHECKS = []


def check(name, condition, **facts):
    CHECKS.append({"name": name, "passed": bool(condition), "facts": facts})
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    if not condition:
        raise AssertionError(name)


def endpoint(node, runtimes, *, caller="desktop"):
    if caller == "desktop" and node != "desktop":
        return "http://127.0.0.1:" + str({"node-a": 18881, "node-b": 18882, "node-c": 18883}[node])
    return runtimes[node]["public_service"]["directory_url"].removesuffix("/public/v1/agents")


def policy(node, issuer, ratio=1):
    state = api(node, "/v1/points")
    current = next((r for r in state["acceptance"] if r["issuer_did"] == issuer), {})
    return api(node, "/v1/points/acceptance", {"issuer_did": issuer, "expected_revision": current.get("revision", 0),
        "policy": {"enabled": True, "numerator": ratio, "denominator": 1, "per_trade": 100, "daily": 1000, "max_holding": 1000}})


def grant(node, holder, amount, mode="ISSUE"):
    return api(node, "/v1/points/grants", {"holder_did": holder, "amount": str(amount), "mode": mode,
        "command_id": RUN + uuid.uuid4().hex, "memo": "本机积分业务验收"})


def quote(buyer, projection, task):
    return api(buyer, "/v1/trades/quote", {"projection_id": projection, "request": {"task_id": task,
        "skill": "inspect", "payload": {"text": "Agents exchange useful services.\n\n联系 alpha@example.com"}}})


def call(buyer, projection, mode, task, **options):
    offer = quote(buyer, projection, task)
    i = next(i for i, o in enumerate(offer["options"]) if o.get("mode") == mode)
    row = api(buyer, "/v1/trades/prepare", {"offer_id": offer["offer_id"], "option_index": i,
        "fee_cap_minor": "0", "command_id": task, **options})
    check("实际节点预留 " + task, row["state"] == "PREPARED")
    key = row["record"]["plan_id"]
    out = api(buyer, "/v1/trades/plans/" + key + "/execute", {})
    check("真实 Agent 与积分结算 " + task, out["payment"]["state"] == "CONFIRMED" and out["delivery"]["ok"]
          and out["delivery"]["metadata"].get("verified_delivery") and len(out["delivery"]["result"]["text_sha256"]) == 64)
    return out, row, offer


def mount(provider, runtimes, amount=5):
    sid = "points-text-" + RUN + "-" + provider
    card = {"name": "文本结构分析 · 积分验收 " + provider, "version": "1.0.0",
        "description": "真实 Python 文本分析服务；本机积分发行、欠账与兑换验证。",
        "skills": [{"id": "inspect", "name": "分析文本"}], "x-a2n": {"uid": str(uuid.uuid4())}}
    api(provider, "/v1/bindings/http", {"card": card, "service_id": sid,
        "endpoint": "http://" + AGENT_NAME + ":9000/invoke", "protocol": "json", "listed": True})
    api(provider, "/v1/points/services", {"service_id": sid, "expected_revision": 0,
        "policy": {"enabled": True, "amount": amount, "modes": ["EARN", "DEBT", "PAY"], "debt_limit": 20}})
    card = api(provider, "/v1/publish", {"service_id": sid})["card"]
    p = api("desktop", "/v1/projections", {"card": card, "projection_id": "points-use-" + RUN + "-" + provider})["projection_id"]
    first = None
    for i in range(10):
        offer = quote("desktop", p, sid + "-sample-" + str(i))
        out = api("desktop", "/v1/trades/free-execute", {"offer_id": offer["offer_id"]})
        if not out["ok"] or out["settlement"]["state"] != "NOT_REQUIRED":
            raise AssertionError("free sample delivery failed")
        first = first or offer
    samples = get(endpoint(provider, runtimes) + "/public/v1/samples?service_id=" + sid)
    check("前十次公开脱敏样品 " + provider, samples["count"] == 10 and "alpha@example.com" not in json.dumps(samples))
    before = get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]
    api("desktop", "/v1/trades/free-execute", {"offer_id": first["offer_id"]})
    check("样品重试不重新执行 " + provider,
        get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] == before)
    return sid, p


def own(state):
    return next((b["amount"] for b in state["balances"] if b["holder_did"] == state["issuer_did"]), 0)


def ledgers():
    states = [api(n, "/v1/points") for n in NODES]
    return {s["issuer_did"]: {b["holder_did"]: b["amount"] for b in s["balances"] if b["amount"]} for s in states}


def run():
    initial_calls = start_agent()["counts"]["successful_inspections"]
    runtimes = {n: api(n, "/v1/runtime") for n in NODES}
    dids = {n: r["node_did"] for n, r in runtimes.items()}
    before = {n: api(n, "/v1/points") for n in NODES}
    check("四个独立节点均安装积分模块", len(set(dids.values())) == 4 and all(s["method"] == "a2n-points/1" for s in before.values()))
    for n in NODES:
        api(n, "/v1/points/configure", {"issuance_enabled": True, "allow_http": True})
        for peer in NODES:
            if peer != n:
                connected = api(n, "/v1/points/peers", {"endpoint": endpoint(peer, runtimes, caller=n)})
                check("签名积分节点连接 " + n + "→" + peer, connected["did"] == dids[peer])
    sid_a, use_a = mount("node-a", runtimes)
    a = api("node-a", "/v1/points")
    check("首十次样品没有积分发行或结算订单", own(a) == own(before["node-a"]) and len(a["orders"]) == len(before["node-a"]["orders"]))
    out, row, offer = call("desktop", use_a, "EARN", RUN + "-earn")
    a = api("node-a", "/v1/points")
    check("真实服务增加供应方自家积分", own(a) == own(before["node-a"]) + 5)
    count = get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]
    api("desktop", "/v1/trades/plans/" + row["record"]["plan_id"] + "/execute", {})
    check("重复结算不增发或重跑", own(api("node-a", "/v1/points")) == own(a)
        and get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] == count)
    call("desktop", use_a, "DEBT", RUN + "-debt")
    a = api("node-a", "/v1/points")
    check("欠账与供应方发行分别入账", a["debts"][dids["desktop"]] == 5 and own(a) == own(before["node-a"]) + 10)
    receipt = grant("node-a", dids["desktop"], 5, "TRANSFER")
    check("供应方向使用方转移自己的存量积分", receipt["issuer_did"] == dids["node-a"] and receipt["holder_did"] == dids["desktop"])
    repayment = api("desktop", "/v1/points/repay", {"issuer_did": dids["node-a"], "amount": "5", "command_id": RUN + "-repay"})
    check("原积分还账成功", repayment["state"] == "CONFIRMED" and api("node-a", "/v1/points")["debts"][dids["desktop"]] == 0)
    grant("node-a", dids["desktop"], 10)
    before_payment = ledgers()
    _, paid_row, _ = call("desktop", use_a, "PAY", RUN + "-paid", funding_issuer=dids["node-a"], max_cost="5")
    after_payment = ledgers()
    check("已有积分支付只转移，不重复发行", all(o["kind"] == "TRANSFER" for o in paid_row["record"]["operations"])
        and {i: sum(b.values()) for i, b in before_payment.items()} == {i: sum(b.values()) for i, b in after_payment.items()})
    # Supply real services on the two other nodes to create their exchange stock.
    for node in ("node-b", "node-c"):
        sid, projection = mount(node, runtimes, amount=10)
        old = own(api(node, "/v1/points"))
        call("desktop", projection, "EARN", RUN + "-earn-" + node)
        check("兑换库存来自真实服务 " + node, own(api(node, "/v1/points")) == old + 10)
    policy("node-b", dids["node-a"], ratio=10)
    policy("node-c", dids["node-b"])
    policy("node-a", dids["node-c"])
    # All participants prepare first; an actual container outage interrupts the
    # original COMMIT after the real provider has executed, then durable recovery
    # must complete that same plan without another Agent invocation.
    task = RUN + "-three-hops"
    original_ledgers = ledgers()
    offer = quote("desktop", use_a, task)
    i = next(i for i, o in enumerate(offer["options"]) if o.get("mode") == "PAY")
    row = api("desktop", "/v1/trades/prepare", {"offer_id": offer["offer_id"], "option_index": i,
        "fee_cap_minor": "0", "command_id": task, "funding_issuer": dids["node-a"], "max_cost": "1"})
    check("三跳结算所有参与方先预留", row["state"] == "PREPARED")
    key = row["record"]["plan_id"]
    count = get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]
    container = "a2n-acceptance-node-c-1"
    subprocess.run(["docker", "stop", "--time", "15", container], check=True, capture_output=True)
    try:
        out = api("desktop", "/v1/trades/plans/" + key + "/execute", {})
        check("中间节点离线保留原提交决定", out["order_state"] == "COMMITTING"
            and out["payment"]["state"] == "PENDING" and out["delivery"]["ok"],
            order_state=out["order_state"], financial_state=out["payment"]["state"])
    finally:
        subprocess.run(["docker", "start", container], check=True, capture_output=True)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        try:
            recovered = api("desktop", "/v1/trades/plans/" + key + "/reconcile", {})
            if recovered["order_state"] == "CONFIRMED":
                break
        except RuntimeError:
            pass
        time.sleep(.5)
    else:
        raise TimeoutError("Original three-hop payment did not recover")
    settled = next(o for o in api("desktop", "/v1/points")["orders"] if o["record"]["plan_id"] == key)
    check("重启后原三跳交易全部回正", recovered["payment"]["reference"] == key
        and len(settled["receipts"]) == 4)
    for operation in row["record"]["operations"]:
        ledger = original_ledgers[operation["issuer_did"]]
        for holder, delta in ((operation["from_did"], -operation["amount"]), (operation["to_did"], operation["amount"])):
            ledger[holder] = ledger.get(holder, 0) + delta
    expected = {issuer: {holder: amount for holder, amount in ledger.items() if amount} for issuer, ledger in original_ledgers.items()}
    settled_ledgers = ledgers()
    check("各发行方余额严格符合原三跳条件", settled_ledgers == expected)
    api("desktop", "/v1/trades/plans/" + key + "/execute", {})
    check("断线恢复不重跑真实 Agent", get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] == count + 1)
    check("重复恢复不重复扣分", ledgers() == settled_ledgers)
    check("四节点三跳选最低成本的自愿接受路径", len(row["record"]["operations"]) == 3
        and row["record"]["funding_cost_decimal"] == "1")
    operations = [{"kind": "TRANSFER", "issuer_did": dids[source], "from_did": dids[source], "to_did": dids[target], "amount": "2"}
        for source, target in (("node-a", "node-b"), ("node-b", "node-c"), ("node-c", "node-a"))]
    swap = api("node-a", "/v1/points/swap", {"operations": operations, "command_id": RUN + "-ring"})
    check("三方循环按一笔整体交易成功", swap["state"] == "CONFIRMED" and len(swap["receipts"]) == 3)
    replay = api("node-a", "/v1/points/swap", {"operations": operations, "command_id": RUN + "-ring"})
    check("三方循环重复请求返回原交易", replay["record"]["plan_id"] == swap["record"]["plan_id"] and replay["receipts"] == swap["receipts"])
    holdings = api("desktop", "/v1/points/holdings", {})
    check("使用方核对发行方签名余额", not holdings["missing_issuers"] and len(holdings["balances"]) == 4)
    samples = get(endpoint("node-a", runtimes) + "/public/v1/samples?service_id=" + sid_a)
    check("后续积分服务保留原十个公开样品", samples["samples_total"] == 10 and samples["completed"] == 14)
    return {"run_id": RUN, "passed": True, "at": datetime.now(timezone.utc).isoformat(), "checks": CHECKS,
        "nodes": dids, "supply": sid_a, "three_hop_plan": row["record"]["plan_id"], "ring_plan": swap["record"]["plan_id"],
        "real_agent_calls_this_run": get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"] - initial_calls}


if __name__ == "__main__":
    try:
        report = run()
    except Exception as exc:
        report = {"run_id": RUN, "passed": False, "checks": CHECKS, "error": str(exc)}
        (ROOT / "artifacts/local-points-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    (ROOT / "artifacts/local-points-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
