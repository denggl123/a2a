"""One Windows node + three isolated Docker nodes + a usable text Agent.

Uses a prebuilt product image. Existing containers/homes are not modified.
Only public test inputs and explicit feedback are used; samples skip settlement.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import uuid

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.coordination import SearchSpec
from a2n_sdk.ports import CallRequest

ROOT=Path(__file__).resolve().parents[1]
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def command(args, *, body=None, timeout=45):
    result=subprocess.run(args,input=json.dumps(body,ensure_ascii=False) if body is not None else None,
        capture_output=True,text=True,encoding='utf-8',cwd=ROOT,timeout=timeout)
    if result.returncode:
        raise RuntimeError(str(args[:2])+': '+result.stderr[-1200:])
    return result.stdout.strip()


def docker_api(container,path,body=None,headers=None):
    args=['docker','exec','-i',container,'python','-m','a2n_node.product_cli','request',
          '--path',path,'--method','POST' if body is not None else 'GET']
    if body is not None:
        args+=['--body','-']
    for key,value in (headers or {}).items():
        args+=['--header',key+':'+str(value)]
    return json.loads(command(args,body=body))


def get(url):
    with OPENER.open(url,timeout=3) as response:
        return json.load(response)


def wait(fn, timeout=30):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        try:
            value=fn()
            if value:
                return value
        except (OSError,ValueError,RuntimeError) as exc:
            last=str(exc)
        time.sleep(.2)
    raise TimeoutError(last or 'state did not finish')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',default='a2n-selection-validation:20261008')
    parser.add_argument('--host',default='192.168.31.21',help='Local IPv4 address reachable from Docker')
    parser.add_argument('--browser',action='store_true')
    args=parser.parse_args()
    host=str(ipaddress.IPv4Address(args.host))
    run=uuid.uuid4().hex[:8];network='a2n-selection-'+run
    names={n:network+'-'+n for n in ('a','b','c','agent')};created=[];windows=None;network_created=False
    checks=[];report={'at':datetime.now(timezone.utc).isoformat(),'run_id':run,'passed':False,'checks':checks}
    def check(name, ok, **facts):
        checks.append({'name':name,'passed':bool(ok),'facts':facts})
        print(('PASS ' if ok else 'FAIL ')+name,flush=True)
        if not ok:
            raise AssertionError(name)
    try:
        # Reserve known-free host ports before creating only this run's resources.
        ports={}
        for n in ('a','b','c'):
            with socket.socket() as sock:
                sock.bind(('',0));ports[n]=sock.getsockname()[1]
        command(['docker','network','create','--label','a2n.purpose=selection-validation',network])
        network_created=True
        subnet=json.loads(command(['docker','network','inspect',network]))[0]['IPAM']['Config'][0]['Subnet']
        for n in ('c','b','a'):
            public=f'http://{host}:{ports[n]}'
            root=f'http://{host}:{ports["c" if n=="b" else "b"]}' if n!='c' else ''
            key=base64.b64encode(os.urandom(32)).decode()
            command(['docker','run','-d','--name',names[n],'--label','a2n.purpose=selection-validation',
                '--network',network,'-p',f'{ports[n]}:8788',
                '-e','A2N_PUBLIC_BASE='+public,'-e','A2N_PUBLIC_NODES='+root,
                '-e','A2N_COORD_ALLOW_NETWORKS='+subnet+','+host+'/32',
                '-e','A2N_STORAGE_KEY='+key,args.image])
            created.append(names[n])
            wait(lambda:get(f'http://127.0.0.1:{ports[n]}/public/v1/agents?skill=selection-readiness'))
        command(['docker','run','-d','--name',names['agent'],'--label','a2n.purpose=selection-validation',
            '--network',network,'--mount',f'type=bind,source={ROOT / "examples/text_inspector/agent.py"},target=/agent.py,readonly',
            '--entrypoint','python',args.image,'/agent.py','--host','0.0.0.0'])
        created.append(names['agent'])
        sid='text-inspector-'+run
        card={'name':'文本结构分析 Agent','version':'1.0.0',
            'description':'真实 Python 服务：字数、段落、词频与短摘要。',
            'skills':[{'id':'inspect','name':'分析文本结构'}],
            'x-a2n':{'price_book':{'inspect':{'CNY':{'dimensions':[{'key':'call_count','amount':0,'per':1}]}}}}}
        docker_api(names['c'],'/v1/bindings/http',{'card':card,'service_id':sid,
            'endpoint':f'http://{names["agent"]}:9000/invoke','protocol':'json','listed':True})
        published=docker_api(names['c'],'/v1/publish',{'service_id':sid})['card']
        # Three independent Linux node processes publish the same public discovery duty.
        runtimes={n:docker_api(names[n],'/v1/runtime') for n in ('a','b','c')}
        protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
        windows=Daemon(ROOT/'.tmp'/network/'windows',port=0,protector=protector,
            public_nodes=[f'http://127.0.0.1:{ports["a"]}'],
            coord_allow_networks=['127.0.0.0/8',host+'/32',subnet]).start()
        check('Windows 与三个 Docker 节点完整装配',len({windows.identity.did,*[r['node_did'] for r in runtimes.values()]})==4 and
              all(r['public_service']['services']['discovery'] and r['public_service']['services']['samples'] for r in runtimes.values()))
        # Allow public introductions to synchronize before asking only node A.
        time.sleep(2)
        search=windows.coordination.start(SearchSpec(skill='inspect',preferences={'min_candidates':1}),run)
        wait(lambda:windows.coordination.get(search.search_id).state!='RUNNING')
        session=windows.coordination.get(search.search_id)
        candidates=windows.coordination.candidates(search.search_id)['items']
        check('沿 A → B → C 发现真实 Agent',any(i['key']['service_id']==sid for i in candidates),state=session.state)
        before=get(f'http://127.0.0.1:{ports["c"]}/public/v1/samples?service_id='+sid)['count']
        ranked=windows.selection.assess({'search_id':search.search_id,'result_revision':session.result_revision})
        check('推荐不执行 Agent 或消耗样品',before==0 and get(f'http://127.0.0.1:{ports["c"]}/public/v1/samples?service_id='+sid)['count']==0)
        windows.runtime.import_agent(published,projection_id='use-selection')
        request=CallRequest(skill='inspect',task_id='windows-'+run,payload={'text':'Agent fair trade. Agent honest service.'})
        result=windows.calls.invoke('use-selection',request)
        check('Windows 到 Docker 实际调用且样品跳过结算',result.ok and result.result['characters']==39 and result.settlement['state']=='NOT_REQUIRED',
              characters=result.result.get('characters') if isinstance(result.result,dict) else None)
        _,feedback=windows.management.command('/v1/feedback/open',{'scope':'use-selection','task_id':request.task_id,
            'dimensions':{'quality':2,'honoring':5},'note':'受控业务验收意见，不是用户满意度数据'})
        windows.experience.publish(feedback['feedback_id'],public_note='受控业务验收')
        subject={'kind':'service','provider_did':runtimes['c']['node_did'],'service_id':sid}
        wait(lambda:windows.selection_facts._inputs(subject)['local'])
        ranked=windows.selection.assess({'search_id':search.search_id,'result_revision':session.result_revision,'task':{'workload_bucket':'small'}})
        row=next(r for r in ranked['items'] if r['service_id']==sid)
        check('真实评价和交互耗时自动进入本机评估',row['dimensions']['quality']['value']<.5 and row['dimensions']['credit']['value']>.5
              and row['dimensions']['time']['value'] is not None and row['dimensions']['reliability']['value']>.5)
        docker_api(names['b'],'/v1/projections',{'card':published,'projection_id':'use-selection'})
        offer=docker_api(names['b'],'/v1/payment-coordination/quote',{'projection_id':'use-selection',
            'request':{'skill':'inspect','task_id':'linux-'+run,'payload':{'text':'Useful agents exchange useful work.'}}})
        delivered=docker_api(names['b'],'/v1/payment-coordination/free-execute',{'offer_id':offer['offer_id']})
        check('Docker 节点之间实际调用',delivered['ok'] and delivered['result']['characters']==35)
        fb=docker_api(names['b'],'/v1/feedback/open',{'scope':'use-selection','task_id':'linux-'+run,'dimensions':{'quality':4,'honoring':4}})
        docker_api(names['b'],'/v1/feedback/publications',{'feedback_id':fb['feedback_id'],'public_note':'受控业务验收'})
        q=windows.experience_service.start({'subject':subject},run+'-opinions')
        wait(lambda:windows.experience_service.get(q['query_id'])['state']!='RUNNING')
        wait(lambda:windows.selection_facts._inputs(subject)['external'])
        check('跨 Docker 公开评价验签后自动更新本机资料',len(windows.selection_facts._inputs(subject)['external'])==1)
        profile=docker_api(names['b'],'/v1/selection/profiles/balanced')
        docker_api(names['b'],'/v1/selection/profiles/balanced',{'values':profile['values'],'expected_revision':profile['revision']})
        command(['docker','restart',names['b']],timeout=60)
        wait(lambda:get(f'http://127.0.0.1:{ports["b"]}/public/v1/agents?skill=selection-readiness'))
        check('Docker 重启保留评价与偏好版本',docker_api(names['b'],'/v1/selection/profiles/balanced')['revision']==1 and
              any(r['feedback_id']==fb['feedback_id'] for r in docker_api(names['b'],'/v1/runtime')['feedback']))
        check('评估全过程没有新增结算或替换样品',get(f'http://127.0.0.1:{ports["c"]}/public/v1/samples?service_id='+sid)['count']==2 and
              not docker_api(names['c'],'/v1/points')['orders'])
        report.update(windows_url=windows.runtime.local_base_url,candidate_count=len(candidates),node_count=4,
            actual_calls=2,algorithm=ranked['algorithm'],docker_image=args.image)
        if args.browser:
            browser_env={**os.environ,'NODE_PATH':str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')}
            run_browser=subprocess.run(['node',str(ROOT/'scripts/selection_console_check.js'),windows.runtime.local_base_url,search.search_id],
                capture_output=True,text=True,encoding='utf-8',cwd=ROOT,env=browser_env,timeout=60)
            print(run_browser.stdout,flush=True)
            if run_browser.returncode:
                raise RuntimeError(run_browser.stderr[-2000:])
            check('真实 Chrome 控制台操作通过',True)
        report['passed']=True
    except Exception as exc:
        report['failure']=str(exc)
        raise
    finally:
        if windows:
            windows.stop()
        for container in reversed(created):
            subprocess.run(['docker','stop','-t','15',container],capture_output=True,timeout=30)
            subprocess.run(['docker','rm',container],capture_output=True,timeout=10)
        if network_created:
            subprocess.run(['docker','network','rm',network],capture_output=True,timeout=10)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (ROOT/'artifacts/selection-acceptance-2026-10-08.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
