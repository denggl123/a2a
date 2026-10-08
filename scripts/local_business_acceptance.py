"""Real code Agent, four installed nodes and the complete local business flow."""
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
RUN = uuid.uuid4().hex[:10]
CHECKS = []
NODES = ("desktop", "node-a", "node-b", "node-c")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
AGENT_NAME = "a2n-business-text-agent"
AGENT_PORT = 18910


def api(node, path, body=None, *, method=None, headers=None):
    args = ([sys.executable,"-m","a2n_node.product_cli","request"] if node=="desktop" else
        ["docker","exec","-i","a2n-acceptance-"+node+"-1","python","-m","a2n_node.product_cli","request"])
    args += ["--path",path,"--method",method or ("GET" if body is None else "POST")]
    if body is not None:
        args += ["--body","-"]
    for key,value in (headers or {}).items():
        args += ["--header",key+":"+str(value)]
    result=subprocess.run(args,input=json.dumps(body,ensure_ascii=False) if body is not None else None,
        capture_output=True,text=True,encoding="utf-8",cwd=ROOT,timeout=45)
    if result.returncode:
        raise RuntimeError(node+" "+path+": "+result.stderr[-800:])
    return json.loads(result.stdout)


def check(name, condition, **facts):
    CHECKS.append({"name":name,"passed":bool(condition),"facts":facts})
    print(("PASS " if condition else "FAIL ")+name,flush=True)
    if not condition:
        raise AssertionError(name)


def get(url):
    with OPENER.open(url,timeout=5) as response:
        return json.load(response)


def wait(fn, predicate, seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=fn()
            if predicate(value):
                return value
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(.1)
    raise TimeoutError("business state did not settle")


def start_agent():
    network=subprocess.run(["docker","inspect","--format","{{range $name, $value := .NetworkSettings.Networks}}{{$name}}{{end}}",
        "a2n-acceptance-node-a-1"],capture_output=True,text=True,check=True).stdout.strip()
    existing=subprocess.run(["docker","inspect","--format","{{index .Config.Labels \"a2n.purpose\"}}",AGENT_NAME],capture_output=True,text=True)
    if existing.returncode:
        subprocess.run(["docker","run","-d","--name",AGENT_NAME,"--restart","unless-stopped",
            "--label","a2n.purpose=local-business-acceptance-agent","--network",network,
            "-p",f"127.0.0.1:{AGENT_PORT}:9000","--mount",f"type=bind,source={ROOT/'examples/text_inspector/agent.py'},target=/agent.py,readonly",
            "--entrypoint","python","a2n-node-test","/agent.py","--host","0.0.0.0"],check=True,capture_output=True,text=True)
    elif existing.stdout.strip()!="local-business-acceptance-agent":
        raise RuntimeError("Agent container name belongs to another service")
    else:
        subprocess.run(["docker","start",AGENT_NAME],check=True,capture_output=True,text=True)
    return wait(lambda:get(f"http://127.0.0.1:{AGENT_PORT}/counts"),lambda x:x["version"]=="text-inspector/1")


def invoke(buyer, projection, task, text):
    offer=api(buyer,"/v1/payment-coordination/quote",{"projection_id":projection,
        "request":{"task_id":task,"skill":"inspect","payload":{"text":text}}})
    result=api(buyer,"/v1/payment-coordination/free-execute",{"offer_id":offer["offer_id"]})
    return result,offer


def query_reputation(node, subject, *, partial=False):
    spec={"subject":subject}
    if partial:
        spec["budget"]={"remote_operations":1}
    query=api(node,"/v1/experience/queries",spec,headers={"Idempotency-Key":uuid.uuid4().hex})
    row=wait(lambda:api(node,"/v1/experience/queries/"+query["query_id"]),lambda x:x["state"]!="RUNNING")
    view=api(node,"/v1/reputation/rebuild",{"subject":subject,"current_version":"1.0.0"},headers={"Idempotency-Key":uuid.uuid4().hex})
    return row,view


def feedback(buyer, projection, task, quality):
    record=api(buyer,"/v1/feedback/open",{"scope":projection,"task_id":task,
        "dimensions":{"quality":quality},"note":"受控业务验收意见；不代表真实用户评价"})
    public=api(buyer,"/v1/feedback/publications",{"feedback_id":record["feedback_id"],"public_note":"受控本机业务验收"})
    return (buyer,record["feedback_id"]),public


def agree(provider,buyer,dispute,action):
    proposal=api(buyer,"/v1/disputes/"+dispute["id"]+"/messages",{"kind":"PROPOSAL",
        "body":{"action":action,"text":"受控验收：双方同意"+action,"amount_minor":0,"currency":""}},headers={"Idempotency-Key":uuid.uuid4().hex})
    def received():
        try:
            return api(provider,"/v1/disputes/"+dispute["id"]+"/messages")
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            return {"messages":[]}
    wait(received,lambda x:any(m["message_id"]==proposal["message_id"] for m in x["messages"]))
    for node in (buyer,provider):
        api(node,"/v1/disputes/"+dispute["id"]+"/agreements",{"proposal_id":proposal["message_id"]},headers={"Idempotency-Key":uuid.uuid4().hex})
    # Delivery runs asynchronously; both journals must contain the agreement
    # before the explicit next operation is submitted.
    for node in (buyer,provider):
        wait(lambda:api(node,"/v1/runtime"),lambda x:any(a["proposal"]["message_id"]==proposal["message_id"] and a["state"]=="BOTH_ACCEPTED" for a in x["resolution_agreements"]))
    return proposal


def run():
    initial=start_agent()
    runtimes={n:api(n,"/v1/runtime") for n in NODES}
    check("四个独立节点及强制公共服务",len({r["node_did"] for r in runtimes.values()})==4 and
        all(r["public_service"]["services"]["discovery"] and r["public_service"]["services"]["samples"] for r in runtimes.values()))
    # Explore the known referral graph without invoking a business service.
    search=api("desktop","/v1/coord/searches",{"skill":"local-business-experience-sources",
        "preferences":{"min_candidates":1}},headers={"Idempotency-Key":uuid.uuid4().hex})
    wait(lambda:api("desktop","/v1/coord/searches/"+search["search_id"]),lambda x:x["state"]!="RUNNING")
    provider="node-a"
    sid="text-inspector-"+RUN
    card={"name":"文本结构分析 Agent · 本机业务验收","version":"1.0.0",
        "description":"真实 Python 服务：字数、段落、词频、短摘要。中文按单字统计，英文按单词统计。",
        "skills":[{"id":"inspect","name":"分析文本结构"}],"x-a2n":{"uid":str(uuid.uuid4()),
            "price_book":{"inspect":{"CNY":{"dimensions":[{"key":"call_count","amount":0,"per":1}]}}},
            "acceptance_template":{"version":"1","required_fields":["characters","keywords","text_sha256"]}}}
    api(provider,"/v1/bindings/http",{"card":card,"service_id":sid,"endpoint":"http://"+AGENT_NAME+":9000/invoke","protocol":"json","listed":True})
    published=api(provider,"/v1/publish",{"service_id":sid})["card"]
    buyers=("desktop","node-b","node-c")
    projections={n:api(n,"/v1/projections",{"card":published,"projection_id":"text-use-"+RUN})["projection_id"] for n in buyers}
    calls=[]
    for i in range(12):
        buyer=buyers[i%3]
        text=f"Agent helps people.\n\nAgent brings fair trade.\n联系 alpha@example.com；案例 {i}"
        result,offer=invoke(buyer,projections[buyer],"text-"+RUN+"-"+str(i),text)
        check("真实文本交付 "+str(i+1),result["ok"] and result["metadata"].get("verified_delivery") and
            result["result"]["characters"]==len(text) and result["result"]["paragraphs"]==2 and
            result["result"]["text_sha256"]==hashlib.sha256(text.encode()).hexdigest() and
            {k["token"]:k["count"] for k in result["result"]["keywords"]}.get("agent")==2 and
            result["settlement"]["state"]=="NOT_REQUIRED",free_reason=offer["free_reason"])
        calls.append({"buyer":buyer,"task":"text-"+RUN+"-"+str(i),"text":text,"outcome":result,"offer":offer})
    first=calls[0]
    count_before=get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]
    duplicate=api(first["buyer"],"/v1/payment-coordination/free-execute",{"offer_id":first["offer"]["offer_id"]})
    check("同一交易重复调用不重复执行",duplicate["result"]==first["outcome"]["result"] and
        get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]==count_before)
    public_base=runtimes[provider]["public_service"]["directory_url"].removesuffix("/public/v1/agents")
    samples=get(public_base+"/public/v1/samples?service_id="+sid)
    check("前十次免费交付必须公开，后续不重排",samples["count"]==10 and samples["samples_total"]==10 and samples["completed"]==12)
    check("公开样品脱敏且不泄露原任务号", "alpha@example.com" not in json.dumps(samples) and first["task"] not in json.dumps(samples))
    refs=[]
    for call in calls[:3]:
        ref,record=feedback(call["buyer"],projections[call["buyer"]],call["task"],1)
        refs.append(ref)
        check("买方签名公开评价 "+call["buyer"],bool(record.get("proof")))
    provider_trade=next(t for t in api(provider,"/v1/trades")["trades"] if t["trade_uid"]==first["outcome"]["metadata"]["trade_uid"])
    seller=api(provider,"/v1/feedback/open",{"scope":sid,"task_id":provider_trade["task_id"],"dimensions":{"on_spec":5},"note":"受控业务验收：卖方对买方的意见"})
    api(provider,"/v1/feedback/publications",{"feedback_id":seller["feedback_id"],"public_note":"需求清楚，受控验收"})
    check("卖方也能反馈且方向由真实交易决定",seller["direction"]=="seller_to_buyer")
    subject={"kind":"service","provider_did":runtimes[provider]["node_did"],"service_id":sid}
    old_policy=api("desktop","/v1/policies")["policy"]
    try:
        values={**old_policy["values"],"mode":"ENFORCE_LOCAL","observe_groups":2,"observe_mass":1.5,"limit_groups":3,"limit_mass":2.5}
        api("desktop","/v1/policies/local-business/1",{"values":values},method="PUT",headers={"If-Match":'"'+str(old_policy["revision"])+'"'})
        query,view=query_reputation("desktop",subject)
        check("三来源意见合并并进入观察",query["known_sources_complete"] and view["dimensions"]["quality"]["counterparty_groups"]==3 and view["opportunity"]["state"]=="OBSERVE",
            groups=view["dimensions"]["quality"]["counterparty_groups"],known_sources_complete=query["known_sources_complete"],state=view["opportunity"]["state"])
        for call,wanted in zip(calls[3:5],("LIMITED","MANUAL_ONLY")):
            ref,_=feedback(call["buyer"],projections[call["buyer"]],call["task"],1)
            refs.append(ref)
            query,view=query_reputation("desktop",subject)
            check("有新交付意见才升级为 "+wanted,view["opportunity"]["effective_state"]==wanted)
        try:
            blocked,_=invoke("desktop",projections["desktop"],"blocked-"+RUN,"Agent helps people.")
        except RuntimeError as exc:
            if "LOCAL_SELECTION_POLICY" not in str(exc):
                raise
            blocked={"ok":False,"error":str(exc)}
        check("本机信誉策略实际阻止新调用",not blocked["ok"] and "LOCAL_SELECTION_POLICY" in str(blocked["error"]) and
            get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]==count_before)
        query,view=query_reputation("desktop",subject,partial=True)
        check("来源查询不完整仍保留已有充分限制",not query["known_sources_complete"] and view["opportunity"]["effective_state"]=="MANUAL_ONLY" and bool(view["opportunity"]["supported_decision"]))
        for buyer,fid in refs:
            api(buyer,"/v1/feedback/revise",{"feedback_id":fid,"dimensions":{"quality":5},"note":"受控验收：作者修订意见，保留历史链"})
            api(buyer,"/v1/feedback/publications",{"feedback_id":fid,"public_note":"已修订，受控验收"})
        query,view=query_reputation("desktop",subject)
        check("作者修订形成支持恢复，旧版不重复计权",query["known_sources_complete"] and view["opportunity"]["effective_state"]=="NORMAL" and view["dimensions"]["quality"]["counterparty_groups"]==3)
    finally:
        current=api("desktop","/v1/policies")["policy"]
        api("desktop","/v1/policies/local-business/1",{"values":old_policy["values"]},method="PUT",headers={"If-Match":'"'+str(current["revision"])+'"'})
    dispute=api("desktop","/v1/disputes/open",{"scope":projections["desktop"],"task_id":first["task"],"reason":"受控业务验收：协商一次补做"})
    proposal=agree(provider,"desktop",dispute,"REWORK")
    reworked=api("desktop","/v1/resolutions/rework",{"proposal_id":proposal["message_id"]})
    repeat=api("desktop","/v1/resolutions/rework",{"proposal_id":proposal["message_id"]})
    check("双方协商补做且重复启动不多执行",reworked["ok"] and repeat["result"]==reworked["result"] and
        get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]==count_before+1,
        state=reworked["state"],error=reworked.get("error"),successful_inspections_before=count_before,
        successful_inspections_after=get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"])
    agree(provider,"desktop",dispute,"CLOSE")
    closed=wait(lambda:api("desktop","/v1/runtime"),lambda x:any(d["id"]==dispute["id"] and d["state"]=="CLOSED_BILATERAL" for d in x["disputes"]))
    check("双方关闭异议，原交付与十个样品保留",any(d["id"]==dispute["id"] for d in closed["disputes"]) and get(public_base+"/public/v1/samples?service_id="+sid)["count"]==10)
    check("业务验收未配置现网钱包或产生付款",all(api(n,"/v1/payment-coordination")["wallet_address"] is None and not api(n,"/v1/payment-coordination")["orders"] for n in NODES))
    return {"service_id":sid,"agent_version":initial["version"],"agent_source":"examples/text_inspector/agent.py",
        "upstream_execution_delta":get(f"http://127.0.0.1:{AGENT_PORT}/counts")["counts"]["successful_inspections"]-initial["counts"]["successful_inspections"],
        "nodes":{n:r["node_did"] for n,r in runtimes.items()},"live_policy_restored":True}


def main():
    report={"at":datetime.now(timezone.utc).isoformat(),"passed":False,"checks":CHECKS,"run_id":RUN,"service_id":"text-inspector-"+RUN,
        "scope":"Real Windows bundle and three Docker nodes; controlled opinions from distinct node identities, not independent human users. No real-value payments."}
    try:
        report.update(run(),passed=True)
    except Exception as exc:
        report["failure"]=str(exc)
        raise
    finally:
        report["finished_at"]=datetime.now(timezone.utc).isoformat()
        (ROOT/"artifacts/local-business-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":True,"checks":len(CHECKS),"report":"artifacts/local-business-acceptance.json"}))


if __name__=="__main__":
    main()
