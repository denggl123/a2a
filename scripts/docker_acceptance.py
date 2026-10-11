"""Real HTTP business acceptance through three independent Docker node processes.

Run with --no-build to test the already built current image. No demo catalog or
central platform is used. Existing production containers and volumes are untouched.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".tmp" / "acceptance.env"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker/acceptance.yaml"), "--env-file", str(ENV_FILE)]
CHECKS = []
RUN = uuid.uuid4().hex[:12]

def compose(*args, stdin=None, timeout=90):
    return subprocess.run([*COMPOSE, *args], input=stdin, capture_output=True,
        text=True, encoding="utf-8", cwd=ROOT, timeout=timeout)

def api(node, path, body=None, *, headers=None, fail=False):
    args = ["exec", "-T", node, "python", "-m", "a2n_node.product_cli", "request",
            "--method", "POST" if body is not None else "GET", "--path", path]
    if body is not None:
        args += ["--body", "-"]
    for key, value in (headers or {}).items():
        args += ["--header", key + ":" + value]
    result = compose(*args, stdin=json.dumps(body, allow_nan=False) if body is not None else None)
    if fail:
        if result.returncode == 0:
            raise AssertionError(f"{node} {path} 应拒绝该操作")
        return result.stderr[-1000:]
    if result.returncode:
        raise AssertionError(f"{node} {path}: {result.stderr[-1200:]}")
    return json.loads(result.stdout)

def check(name, condition, **facts):
    CHECKS.append({"name": name, "passed": bool(condition), "facts": facts})
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    if not condition:
        raise AssertionError(name + ": " + str(facts))

def http(port, path, body=None, *, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=8) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}

def rpc(scope, method, params):
    out = api("node-a", "/a2a/" + scope, {"jsonrpc": "2.0", "id": RUN, "method": method, "params": params})
    return out.get("result") or out

def call(scope, skill, payload, task):
    return rpc(scope, "message/send", {"message": {"role": "user", "messageId": task,
        "parts": [{"kind": "data", "data": payload}]}, "metadata": {"skill": skill}})

def mount(skill, *, suffix="", endpoint="/invoke", template=None, listed=True):
    uid = str(uuid.uuid4())
    card = {"name": "Acceptance " + skill, "version": "1.0.0+" + RUN, "description": "Docker HTTP acceptance upstream",
        "skills": [{"id": skill}], "x-a2n": {"uid": uid,
        "price_book": {skill: {"CNY": {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]}}}}}
    if template:
        card["x-a2n"]["acceptance_template"] = template
    sid = "svc_" + RUN + "_" + skill + suffix
    api("node-c", "/v1/bindings/http", {"card": card, "service_id": sid,
        "endpoint": "http://agents:9000" + endpoint, "protocol": "json", "listed": listed})
    public_card = api("node-c", "/v1/publish", {"service_id": sid})["card"] if listed else card
    return sid, public_card

def import_card(card, **selection):
    return api("node-a", "/v1/projections", {"card": card, **selection})["projection_id"]

def peer_get(provider_did, sid, task, *, node="node-a"):
    code = '''import base64,json,os,sys,urllib.request
from a2n_node.protection import system_protector
from a2n_sdk.storage import LocalStore
from a2n_p2p import Identity
from a2n_node.peer import sign_control
value=json.load(sys.stdin)
store=LocalStore(os.environ['A2N_HOME']+'/runtime.db',system_protector())
identity=Identity.from_private_bytes(base64.b64decode(store.get('identity','seed')))
store.close()
proof=sign_control(identity,provider_did=value['provider'],service_id=value['sid'],task_id=value['task'],method='tasks/get')
body={'jsonrpc':'2.0','id':'control','method':'tasks/get','params':{'id':value['task'],'a2nPeerControl':proof}}
request=urllib.request.Request('http://node-c:8788/a2a/'+value['sid'],data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request,timeout=8) as response: print(json.dumps({'status':response.status,'body':json.load(response)}))
except urllib.error.HTTPError as error: print(json.dumps({'status':error.code}))
'''
    out = compose("exec", "-T", node, "python", "-c", code, stdin=json.dumps({"provider": provider_did, "sid": sid, "task": task}))
    if out.returncode: raise AssertionError(out.stderr[-1000:])
    return json.loads(out.stdout)

def wait_ready(node):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        result = compose("exec", "-T", node, "python", "-m", "a2n_node.product_cli", "request", timeout=10)
        if result.returncode == 0:
            return json.loads(result.stdout)
        time.sleep(.3)
    raise AssertionError(node + " not ready: " + result.stderr[-600:])

def run():
    identities = {node: wait_ready(node)["node_did"] for node in ("node-a", "node-b", "node-c")}
    # Only retire previous fixtures in this dedicated acceptance stack.
    for binding in api("node-c", "/v1/runtime")["bindings"]:
        if binding.get("name", "").startswith("Acceptance "):
            api("node-c", "/v1/bindings/remove", {"service_id": binding["service_id"]})
    baseline = http(18884, "/counts")[1]["counts"].get("add", 0)
    api("node-c", "/v1/public-services", {"services": {"samples": True}})
    check("三个独立节点身份", len(set(identities.values())) == 3, identities=identities)
    for node in identities:
        snap = api(node, "/v1/runtime")
        check(node + " 强制公共发现", snap["public_service"]["services"]["discovery"] is True)
        api(node, "/v1/public-services", {"services": {"discovery": False}}, fail=True)
        check(node + " 拒绝关闭发现", True)
    api("node-a", "/v1/accounts", {"account_id": "acceptance-" + RUN, "headers": {"Authorization": "fixture-private-credential"}})
    check("凭据只返回字段名", "fixture-private-credential" not in json.dumps(api("node-a", "/v1/accounts")))
    api("node-a", "/v1/accounts/remove", {"account_id": "acceptance-" + RUN})
    check("凭据生命周期", True)
    api("node-a", "/v1/runtime", headers={"Host": "evil.example"}, fail=True)
    api("node-a", "/v1/node/stop", {}, headers={"Origin": "https://evil.example"}, fail=True)
    check("管理接口 Host 和 Origin 防护", True)
    sid, card = mount("add", template={"version": "1", "required_fields": ["sum"]})
    check("真实管理接口上架", card["url"] == f"http://node-c:8788/a2a/{sid}")
    check("上架幂等", api("node-c", "/v1/publish", {"service_id": sid})["service_id"] == sid)
    code, directory = http(18883, "/public/v1/agents?skill=add")
    check("公共目录含供给", code == 200 and any(c["url"] == card["url"] for c in directory["cards"]))
    code, _ = http(18883, "/v1/runtime")
    check("公共代理阻断管理面", code == 404)
    code, _ = http(18883, "/console")
    check("公共代理阻断控制台", code == 404)
    # B learns C through actual bounded coordination; A's only configured root is B.
    api("node-b", "/v1/discovery/search", {"skill": "add", "timeout": 3, "limit": 1})
    started = api("node-a", "/v1/coord/searches", {"skill": "add", "required": {"version": "1.0.0+" + RUN}, "preferences": {"min_candidates": 1}},
        headers={"Idempotency-Key": RUN + "search"})
    search_id = started["search_id"]
    check("搜索命令幂等", api("node-a", "/v1/coord/searches",
        {"skill": "add", "required": {"version": "1.0.0+" + RUN}, "preferences": {"min_candidates": 1}},
        headers={"Idempotency-Key": RUN + "search"})["search_id"] == search_id)
    for _ in range(30):
        snap = api("node-a", f"/v1/coord/searches/{search_id}")
        if snap["state"] != "RUNNING": break
        time.sleep(.1)
    page = api("node-a", f"/v1/coord/searches/{search_id}/candidates")
    item = next((i for i in page["items"] if i["key"]["service_id"] == sid), None)
    check("A 经 B 逐级发现 C 的供给", item is not None, state=snap["state"], candidate_count=len(page["items"]), errors=page.get("errors"))
    check("A 只配置 B", api("node-a", "/v1/runtime")["connections"]["public_nodes"] == ["http://node-b:8788"])
    check("发现过程没有调用 Agent", http(18884, "/counts")[1]["counts"].get("add", 0) == baseline)
    plan = api("node-a", f"/v1/coord/searches/{search_id}/route-plan", {"key": item["key"], "probe": True},
        headers={"Idempotency-Key": RUN + "plan", "If-Match": f'"{snap["revision"]}"'})
    check("协调通道计划", bool(plan["choices"]))
    # Hold real peers briefly so the pause command is tested during actual I/O.
    held = compose("pause", "node-b", "node-c")
    check("控制网络故障模拟", held.returncode == 0)
    try:
        resumed = api("node-a", f"/v1/coord/searches/{search_id}/resume", {"preferences": {"min_candidates": 100}},
            headers={"Idempotency-Key": RUN + "resume", "If-Match": f'"{snap["revision"]}"'})
        paused = api("node-a", f"/v1/coord/searches/{search_id}/pause", {},
            headers={"Idempotency-Key": RUN + "pause", "If-Match": f'"{resumed["revision"]}"'})
    finally:
        compose("unpause", "node-b", "node-c")
    check("满意后可续查和暂停", resumed["round"] == 2 and paused["state"] == "PAUSED")
    pid = import_card(card, search_id=search_id, key=item["key"], route_id=plan["choices"][0]["route_id"])
    check("工作台使用本地投影", any(p["projection_id"] == pid and p["url"].startswith("http://127.0.0.1:8771/")
        for p in api("node-a", "/v1/runtime")["projections"]))
    changed = json.loads(json.dumps(card)); changed["name"] = "tampered"
    api("node-a", "/v1/projections", {"card": changed}, fail=True)
    check("篡改签名卡片不能导入", True)
    quote = api("node-c", "/v1/quotes", {"service_id": sid, "skill": "add", "currency": "CNY", "dimensions": {"call_count": 2}})
    check("迁移计价规则", quote["amount_minor"] == 6 and quote["settlement_state"] == "NOT_CONFIGURED")
    api("node-c", "/v1/quotes", {"service_id": sid, "skill": "add", "currency": "USDC", "dimensions": {}}, fail=True)
    check("拒绝跨币种猜价", True)
    task = RUN + "first"
    result = call(pid, "add", {"a": 2, "b": 3, "token": "secret-never-public", "email": "buyer@example.com"}, task)
    check("实际 HTTP 调用和验收", result["status"]["state"] == "completed" and result["artifacts"][0]["parts"][0]["data"]["sum"] == 5, state=result["metadata"]["a2nState"])
    check("声明模板验收生效", result["metadata"]["acceptance"]["quality_measured"] is True)
    check("真实支付明确未配置", result["metadata"]["settlement"]["state"] == "NOT_CONFIGURED")
    check("双边签名收据", bool(result["metadata"].get("a2nReceipt")))
    before = http(18884, "/counts")[1]["counts"]["add"]
    replay = call(pid, "add", {"a": 2, "b": 3, "token": "secret-never-public", "email": "buyer@example.com"}, task)
    check("任务重放不执行第二次", replay["id"] == task and http(18884, "/counts")[1]["counts"]["add"] == before)
    conflict = call(pid, "add", {"a": 9, "b": 3}, task)
    check("同任务不同输入拒绝", "error" in conflict and http(18884, "/counts")[1]["counts"]["add"] == before)
    check("任务查询无重复执行", rpc(pid, "tasks/get", {"id": task})["id"] == task)
    fb = api("node-a", "/v1/feedback/open", {"scope": pid, "task_id": task, "dimensions": {"quality": 5}, "note": "按约完成"})
    same = api("node-a", "/v1/feedback/open", {"scope": pid, "task_id": task, "dimensions": {"quality": 5}, "note": "按约完成"})
    check("买方反馈幂等", same["feedback_id"] == fb["feedback_id"] and same["revision"] == 1)
    revised = api("node-a", "/v1/feedback/revise", {"feedback_id": fb["feedback_id"], "dimensions": {"quality": 4}, "note": "补充意见"})
    check("反馈保留版本链", revised["revision"] == 2 and api("node-a", f'/v1/feedback/{fb["feedback_id"]}/versions')["count"] == 2)
    seller = api("node-c", "/v1/feedback/open", {"scope": sid, "task_id": task, "dimensions": {"cooperative": 5}, "note": "买方配合"})
    delivered = api("node-a", "/v1/feedback/deliver", {"scope": pid, "task_id": task})
    check("双方反馈实际传送和验签", delivered["delivered"] and delivered["received"])
    check("卖方反馈使用节点身份", seller["author_did"] == identities["node-c"])
    settlements = api("node-a", "/v1/runtime")["settlements"]
    check("回执经公共代理确认", any(r["task_id"] == task and r["evidence_type"] == "receipt_bilateral_verified" for r in settlements))
    dispute = api("node-a", "/v1/disputes/open", {"scope": pid, "task_id": task, "reason": "希望补充交付说明"})
    withdrawn = api("node-a", "/v1/disputes/withdraw", {"dispute_id": dispute["id"], "note": "已解决"})
    check("争议撤回留痕", withdrawn["state"] == "WITHDRAWN")
    for i in range(1, 11):
        delivered = call(pid, "add", {"a": i, "b": 1}, RUN + "sample" + str(i))
        check("真实交付 " + str(i), delivered["status"]["state"] == "completed")
    trial = api("node-c", "/v1/trials", {"service_id": sid})
    check("前十次样品自动形成", trial["status"]["ended"] and len(trial["samples"]) == 10, completed=trial["status"]["completed"])
    code, samples = http(18883, "/public/v1/samples?service_id=" + sid)
    check("公共样品不泄漏凭据和邮箱", code == 200 and "secret-never-public" not in json.dumps(samples) and "buyer@example.com" not in json.dumps(samples))
    check("本地质量事实入口", api("node-a", "/v1/quality?scope=" + pid)["quality_measured"] >= 1)
    badsid, badcard = mount("wrong", endpoint="/wrong", template={"required_fields": ["sum"]})
    badpid = import_card(badcard)
    wrong = call(badpid, "wrong", {}, RUN + "bad")
    check("验收拒绝并保留成品", wrong["status"]["state"] == "rejected" and bool(wrong["artifacts"]) and not wrong["metadata"]["settlement"])
    fsid, fcard = mount("failure", endpoint="/failure")
    fpid = import_card(fcard)
    failure = call(fpid, "failure", {}, RUN + "failure")
    check("上游故障不冒充验收成功", failure["status"]["state"] != "completed" and not failure["metadata"]["acceptance"])
    check("故障不占完成样品名额", api("node-c", "/v1/trials", {"service_id": fsid})["status"]["completed"] == 0)
    ssid, scard = mount("slow", endpoint="/slow")
    spid = import_card(scard)
    slowtask = RUN + "slow"
    submitted = rpc(spid, "message/send", {"message": {"role": "user", "messageId": slowtask, "parts": [{"kind": "data", "data": {}}]},
        "metadata": {"skill": "slow"}, "configuration": {"blocking": False}})
    check("实际异步任务提交", submitted["status"]["state"] in {"submitted", "working"})
    canceled = rpc(spid, "tasks/cancel", {"id": slowtask})
    check("取消不伪造远端确认", canceled["status"]["state"] != "canceled" or canceled["metadata"].get("cancel_acknowledged") is True)
    time.sleep(3)
    late = rpc(spid, "tasks/get", {"id": slowtask})
    check("取消后保留晚到真实交付", late["status"]["state"] == "completed")
    api("node-c", "/v1/public-service", {"enabled": False})
    check("可选服务关闭不关闭基础发现", http(18883, "/public/v1/agents?skill=add")[0] == 200)
    api("node-c", "/v1/unpublish", {"service_id": sid})
    unsigned = {"jsonrpc": "2.0", "id": "external", "method": "message/send", "params": {"message": {"messageId": RUN + "unlisted", "parts": []}}}
    check("下架拒绝普通公共新调用", http(18883, "/a2a/" + sid, unsigned)[0] == 403)
    check("下架后历史任务可查", rpc(pid, "tasks/get", {"id": task})["id"] == task)
    check("原买方跨节点查询下架任务", peer_get(identities["node-c"], sid, task)["status"] == 200)
    check("其他节点不能查询此任务", peer_get(identities["node-c"], sid, task, node="node-b")["status"] == 403)
    api("node-c", "/v1/publish", {"service_id": sid})
    api("node-c", "/v1/bindings/state", {"service_id": sid, "enabled": False})
    api("node-c", "/v1/publish", {"service_id": sid}, fail=True)
    check("暂停接单禁止上架", True)
    api("node-c", "/v1/bindings/state", {"service_id": sid, "enabled": True})
    for node in ("node-a", "node-c"):
        result = compose("restart", node)
        check(node + " 正常重启", result.returncode == 0)
        check(node + " 身份持久化", wait_ready(node)["node_did"] == identities[node])
    check("重启恢复供给", any(b["service_id"] == sid for b in api("node-c", "/v1/runtime")["bindings"]))
    check("重启恢复投影和调用收据", rpc(pid, "tasks/get", {"id": task})["id"] == task)
    check("重启恢复样品", len(api("node-c", "/v1/trials", {"service_id": sid})["samples"]) == 10)
    check("重启恢复反馈", api("node-a", f'/v1/feedback/{fb["feedback_id"]}/versions')["count"] == 2)
    check("重启恢复争议", any(d["id"] == dispute["id"] for d in api("node-a", "/v1/disputes")["disputes"]))
    api("node-a", "/v1/projections/remove", {"projection_id": badpid})
    api("node-c", "/v1/bindings/remove", {"service_id": badsid})
    check("卸载供给清理活动目录", all(c["url"] != badcard["url"] for c in http(18883, "/public/v1/agents?skill=wrong")[1]["cards"]))

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("A2N_ACCEPTANCE_KEY=" + base64.b64encode(os.urandom(32)).decode() + "\n")
    failure = None
    try:
        if not args.no_build:
            build = compose("build", "node-a", timeout=300)
            if build.returncode: raise RuntimeError(build.stderr[-2000:])
        result = compose("up", "-d", "--no-build", timeout=90)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        run()
    except Exception as exc:
        failure = str(exc)
        print("FAILED " + failure, flush=True)
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    report = {"at": datetime.now(timezone.utc).isoformat(), "run": RUN,
        "passed": failure is None, "checks": CHECKS, "failure": failure,
        "boundary": ["Docker bridge network with signed real HTTP calls", "Public Internet NAT and long-term capacity not tested", "Payment and personalized selection require their separate acceptance suites; this script does not verify them"]}
    (output / "docker-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "docker-acceptance.md").write_text("# Docker 多节点实际验收\n\n" +
        f"时间：{report['at']}；结果：{'通过' if report['passed'] else '失败'}；检查数：{len(CHECKS)}。\n\n" +
        "三节点独立运行同一份节点镜像。A 只配置 B，B 引荐 C。测试 Agent 为实际 HTTP 上游，通过管理接口挂载，无预装演示目录。\n\n" +
        "| 检查 | 结果 |\n|---|---|\n" + ''.join(f"| {c['name']} | {'通过' if c['passed'] else '失败'} |\n" for c in CHECKS) +
        ("\n失败：" + failure if failure else "") +
        "\n\n边界：真实公网 NAT、跨运营商、长期容量未覆盖；支付和个性化选择已提供独立验收，本脚本不覆盖它们。\n", encoding="utf-8")
    return 0 if failure is None else 1

if __name__ == "__main__":
    raise SystemExit(main())
