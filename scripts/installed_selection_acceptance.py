"""Selection acceptance on the installed Windows SDK and three Docker nodes.

Explicitly registers a usable Python text Agent and calls it with public test
inputs. Uses a new service and private profile; keeps existing user preferences,
financial records, network configuration and other services intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
NODES = ("desktop", "node-a", "node-b", "node-c")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(node, path, body=None, *, method=None, headers=None):
    args = ([sys.executable, "-m", "a2n_node.product_cli", "request"] if node == "desktop" else
            ["docker", "exec", "-i", "a2n-acceptance-" + node + "-1",
             "python", "-m", "a2n_node.product_cli", "request"])
    args += ["--path", path, "--method", method or ("POST" if body is not None else "GET")]
    if body is not None:
        args += ["--body", "-"]
    for name, value in (headers or {}).items():
        args += ["--header", name + ":" + str(value)]
    result = subprocess.run(args, input=json.dumps(body, ensure_ascii=False) if body is not None else None,
                            capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=45)
    if result.returncode:
        raise RuntimeError(node + " " + path + ": " + result.stderr[-500:])
    return json.loads(result.stdout)


def get(url):
    with OPENER.open(url, timeout=5) as response:
        return json.load(response)


def wait(fn, predicate, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        row = fn()
        if predicate(row):
            return row
        time.sleep(.25)
    raise TimeoutError("Expected installed node state did not arrive")


def main():
    run = uuid.uuid4().hex[:10]
    sid, projection = "selection-text-" + run, "selection-use-" + run
    profile_id = "acceptance_" + run
    checks = []
    report = {"at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "environment": "EXISTING_INSTALLED_NODES", "run_id": run,
              "service_id": sid, "checks": checks}
    path = ROOT / "artifacts" / "selection-installed-2026-10-09.json"
    registered = False

    def check(name, condition, **facts):
        checks.append({"name": name, "passed": bool(condition), "facts": facts})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            raise AssertionError(name)

    try:
        runtimes = {n: api(n, "/v1/runtime") for n in NODES}
        check("四个既有独立节点均启用本机评估和公共发现", len({r["node_did"] for r in runtimes.values()}) == 4
              and all(r["selection"]["algorithm"] == "a2n-selection/1" and
                      r["public_service"]["services"]["discovery"] for r in runtimes.values()))
        check("Windows 运行新版完整安装程序", runtimes["desktop"]["product_runtime"]["mode"] == "BUNDLED")
        original_profiles = {n: api(n, "/v1/selection/profiles/balanced") for n in NODES}
        original_policies = {n: api(n, "/v1/policies") for n in NODES}
        original_points = {n: api(n, "/v1/points") for n in NODES}
        counts_url = "http://127.0.0.1:18910/counts"
        count = get(counts_url)["counts"]["successful_inspections"]
        provider = "node-c"
        card = {"name": "文本结构分析 Agent（节点验收）", "version": "1.0.0-selection." + run,
                "description": "实际 Python 服务：字数、段落、词频和摘要。本批公开样品及评价使用受控验收输入。",
                "skills": [{"id": "inspect", "name": "分析文本结构"}],
                "x-a2n": {"uid": str(uuid.uuid4()), "price_book": {
                    "inspect": {"CNY": {"dimensions": [{"key": "call_count", "amount": 0, "per": 1}]}}}}}
        api(provider, "/v1/bindings/http", {"card": card, "service_id": sid,
            "endpoint": "http://a2n-business-text-agent:9000/invoke", "protocol": "json", "listed": True})
        registered = True
        published = api(provider, "/v1/publish", {"service_id": sid})["card"]
        search = api("desktop", "/v1/coord/searches", {"skill": "inspect", "required": {"version": card["version"]},
            "preferences": {"min_candidates": 1}, "round_budget": {
                "remote_operations": 64, "received_bytes": 4194304, "duration_ms": 15000,
                "introduction_depth": 8, "candidate_limit": 256, "probe_operations": 1, "max_concurrency": 2}},
            headers={"Idempotency-Key": "installed-discovery-" + run})
        search_id = search["search_id"]
        report["search_id"] = search_id
        session = wait(lambda: api("desktop", "/v1/coord/searches/" + search_id), lambda r: r["state"] != "RUNNING")

        def ranking(profile="balanced"):
            session = api("desktop", "/v1/coord/searches/" + search_id)
            ranked = api("desktop", "/v1/selection/rank", {"search_id": search_id,
                         "result_revision": session["result_revision"], "profile_id": profile,
                         "task": {"skill": "inspect", "workload_bucket": "small"}})
            row = next((r for r in ranked["items"] if r["service_id"] == sid), None)
            return ranked, row

        ranked, fresh = ranking()
        check("既有协调网络发现新上架的真实 Agent", fresh is not None, discovery_state=session["state"])
        public_base = runtimes[provider]["public_service"]["directory_url"].removesuffix("/public/v1/agents")
        samples_url = public_base + "/public/v1/samples?service_id=" + sid
        check("发现与本机排序没有执行服务或占用样品", get(samples_url)["count"] == 0
              and get(counts_url)["counts"]["successful_inspections"] == count)
        key = runtimes[provider]["node_did"] + "|" + sid
        projections = {n: api(n, "/v1/projections", {"card": published, "projection_id": projection})["projection_id"]
                       for n in NODES if n != provider}
        calls = []
        for i in range(12):
            buyer = ("desktop", "node-a", "node-b")[i % 3]
            task = "selection-" + run + "-" + str(i)
            text = f"Agent helps fair trade. Agent provides useful work.\n\nPublic acceptance case {i}."
            offer = api(buyer, "/v1/payment-coordination/quote", {"projection_id": projections[buyer],
                "request": {"task_id": task, "skill": "inspect", "payload": {"text": text}}})
            result = api(buyer, "/v1/payment-coordination/free-execute", {"offer_id": offer["offer_id"]})
            check("真实文本调用 " + str(i+1), result["ok"] and result["result"]["characters"] == len(text)
                  and result["result"]["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
                  and result["settlement"]["state"] == "NOT_REQUIRED", buyer=buyer, free_reason=offer["free_reason"])
            calls.append({"buyer": buyer, "task": task, "result": result, "offer": offer})
        report["actual_calls"] = len(calls)
        samples = get(samples_url)
        check("前十次成功服务固定公开且后两次不增加样品", samples["count"] == 10 and samples["completed"] == 12
              and samples["samples_total"] == 10 and get(counts_url)["counts"]["successful_inspections"] == count+12)
        first = calls[0]
        repeated = api("desktop", "/v1/payment-coordination/free-execute", {"offer_id": first["offer"]["offer_id"]})
        check("同一交易重试不会重复执行或占用样品", repeated["result"] == first["result"]["result"]
              and get(counts_url)["counts"]["successful_inspections"] == count+12)
        feedbacks = []
        for call, quality in zip(calls[:3], (2, 5, 4)):
            fb = api(call["buyer"], "/v1/feedback/open", {"scope": projections[call["buyer"]], "task_id": call["task"],
                 "dimensions": {"quality": quality, "honoring": 5}, "note": "受控安装节点验收意见，不代表真实用户满意度"})
            publication = api(call["buyer"], "/v1/feedback/publications", {"feedback_id": fb["feedback_id"],
                              "public_note": "受控安装节点验收；真实文本交付"})
            feedbacks.append(fb)
            check("版本二评价与签名公开记录 " + call["buyer"], fb["v"] == "a2n-feedback/2" and bool(publication.get("proof")))
        ranked, row = wait(lambda: ranking(), lambda pair: pair[1]["dimensions"]["quality"]["value"] is not None
                          and pair[1]["dimensions"]["time"]["value"] is not None)
        q_before = row["dimensions"]["quality"]["value"]
        check("已有评价和实际交互耗时自动进入推荐", row["dimensions"]["credit"]["value"] > .5
              and row["dimensions"]["reliability"]["value"] > .5, quality=q_before,
              time_status=row["dimensions"]["time"]["status"])
        # Revising one real opinion must replace its contribution, not add another vote.
        api("desktop", "/v1/feedback/revise", {"feedback_id": feedbacks[0]["feedback_id"],
            "dimensions": {"quality": 5, "honoring": 5}})
        _, revised = wait(lambda: ranking(), lambda pair: pair[1]["dimensions"]["quality"]["value"] > q_before)
        check("本机改评自动更新且保留单笔贡献", revised["dimensions"]["quality"]["support"]["local"]["samples"] == 1)
        provider_trade = next(t for t in api(provider, "/v1/trades")["trades"]
                              if t["trade_uid"] == first["result"]["metadata"]["trade_uid"])
        seller = api(provider, "/v1/feedback/open", {"scope": sid, "task_id": provider_trade["task_id"],
            "dimensions": {"honoring": 5, "cooperative": 4}, "note": "受控验收：使用方遵守约定"})
        from urllib.parse import urlencode
        subject_url = "/v1/selection/subjects?" + urlencode({"kind": "buyer", "buyer_did": runtimes["desktop"]["node_did"]})
        buyer_credit = wait(lambda: api(provider, subject_url), lambda r: r["credit"]["value"] is not None)
        check("供应方可以评价使用方并更新使用方信用", seller["direction"] == "seller_to_buyer"
              and buyer_credit["credit"]["value"] > .5)
        session = api("desktop", "/v1/coord/searches/" + search_id)
        body = {"search_id": search_id, "result_revision": session["result_revision"], "keys": [key],
                "budget": {"remote_operations": 12, "received_bytes": 262144, "duration_ms": 2000}}
        headers = {"If-Match": '"'+str(session["revision"])+'"', "Idempotency-Key": "selection-metadata-"+run}
        job = api("desktop", "/v1/selection/refresh", body, headers=headers)
        job = wait(lambda: api("desktop", "/v1/selection/refresh/"+job["job_id"]), lambda r: r["state"] != "RUNNING")
        budget_after = api("desktop", "/v1/coord/searches/"+search_id)["budget_used"]
        replay = api("desktop", "/v1/selection/refresh", body, headers=headers)
        check("补公开资料有累计额度并支持幂等重试", replay["job_id"] == job["job_id"] and
              job["used_operations"] <= 12 and job["used_bytes"] <= 262144 and
              api("desktop", "/v1/coord/searches/"+search_id)["budget_used"] == budget_after,
              used_operations=job["used_operations"], known_sources_complete=job["known_sources_complete"])
        _, enriched = wait(lambda: ranking(), lambda pair:
                           pair[1]["dimensions"]["quality"]["support"].get("external", {}).get("samples", 0) >= 2)
        external = enriched["dimensions"]["quality"]["support"]["external"]
        check("跨节点公开评价验签后自动进入本机评分", external["counterparty_groups"] == 2,
              counterparty_groups=external["counterparty_groups"], samples=external["samples"])
        check("补资料和重排没有新增 Agent 调用或样品", get(counts_url)["counts"]["successful_inspections"] == count+12
              and get(samples_url)["count"] == 10)
        observer = "node-b"
        custom = api(observer, "/v1/selection/profiles/"+profile_id)
        saved = api(observer, "/v1/selection/profiles/"+profile_id,
                    {"values": custom["values"], "expected_revision": custom["revision"]})
        subprocess.run(["docker", "restart", "a2n-acceptance-node-b-1"], check=True,
                       capture_output=True, text=True, timeout=60)
        end = time.monotonic()+30
        while time.monotonic() < end:
            try:
                if api(observer, "/v1/selection/profiles/"+profile_id) == saved:
                    break
            except RuntimeError:
                pass
            time.sleep(.5)
        check("常驻 Docker 节点重启保留私人偏好和实际交易", api(observer, "/v1/selection/profiles/"+profile_id) == saved
              and any(t["trade_uid"] == calls[2]["result"]["metadata"]["trade_uid"]
                      for t in api(observer, "/v1/trades")["trades"]))
        check("各节点默认偏好与原信誉策略保持一致", all(api(n, "/v1/selection/profiles/balanced") == original_profiles[n]
              and api(n, "/v1/policies") == original_policies[n] for n in NODES))
        check("全部受控调用均未产生积分结算", all(api(n, "/v1/points") == original_points[n] for n in NODES))
        report["passed"] = True
    finally:
        if registered and not report["passed"]:
            # Keep any real transaction/sample history, but avoid advertising an
            # interrupted acceptance run as an active catalogue entry.
            try:
                api("node-c", "/v1/unpublish", {"service_id": sid})
            except RuntimeError:
                report["cleanup"] = "UNPUBLISH_PENDING"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
