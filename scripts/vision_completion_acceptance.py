"""Actual installed-node workflow, artifact, assessment and local-chain acceptance.

All generated records are controlled engineering inputs, never human feedback or
three-day calibration evidence. No wallet secret is read or printed.
"""
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
from eth_abi import encode,decode
from eth_utils import keccak
from installed_selection_acceptance import api,get,wait,NODES,ROOT,OPENER

REPORT_PATH=ROOT/'artifacts/vision-completion-installed-2026-10-09.json'
if REPORT_PATH.exists():report=json.loads(REPORT_PATH.read_text(encoding='utf-8'))
else:report={'run_id':uuid.uuid4().hex[:10],'at':datetime.now(timezone.utc).isoformat(),'passed':False,'checks':[],'services':{},'calls':{}}
RUN=report['run_id']
def persist():REPORT_PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def check(name,ok,**facts):
    print(('PASS ' if ok else 'FAIL ')+name,flush=True)
    report['checks']=[r for r in report['checks'] if r['name']!=name]+[{'name':name,'passed':bool(ok),'facts':facts}];persist()
    if not ok:raise AssertionError(name)
def rpc(method,params):
    request=urllib.request.Request('http://127.0.0.1:18945',json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),{'Content-Type':'application/json'})
    with OPENER.open(request,timeout=10) as response:row=json.load(response)
    if 'error' in row:raise ValueError('LOCAL_CHAIN_RPC_FAILED')
    return row['result']
def post(url,body):
    request=urllib.request.Request(url,json.dumps(body).encode(),{'Content-Type':'application/json'})
    with OPENER.open(request,timeout=10) as response:return json.load(response)
def token_balance(token,address):
    raw=rpc('eth_call',[{'to':token,'data':'0x'+(keccak(text='balanceOf(address)')[:4]+encode(['address'],[address])).hex()},'latest'])
    return decode(['uint256'],bytes.fromhex(raw[2:]))[0]

def install(node,skill,name,price=None):
    image=subprocess.run(['docker','image','inspect','a2n-utilities:20261009','--format','{{.Id}}'],capture_output=True,text=True,check=True).stdout.strip()
    version='1.0.0';manifest={'package_id':skill+'-'+RUN,'version':version,'image':image,
        'command':['python','/app/utility.py',skill],'card':{'name':name,'version':version,
            'description':'受限容器中实际运行的实用服务。公开案例使用受控验收输入。','skills':[{'id':skill,'name':name}],
            'x-a2n':{'price_book':{skill:price or {'CNY':{'dimensions':[{'key':'call_count','amount':0,'per':1}]}}}}},
        'limits':{'timeout_ms':15000,'memory_mib':512,'gpu':False},
        'permissions':{'execute':'CONTAINER','network':'NONE','host_files':'NONE','gpu':False}}
    manifest=api(node,'/v1/workflows/sign',{'manifest':manifest})
    preview=api(node,'/v1/workflows/preview',{'manifest':manifest})
    installed=api(node,'/v1/workflows/install',{'manifest':manifest,'accepted_digest':preview['preview_digest'],
        'granted_permissions':manifest['permissions'],'command_id':RUN+'-'+skill})
    sid=installed['service_id'];card=api(node,'/v1/publish',{'service_id':sid})['card']
    report['services'][skill]={'node':node,'service_id':sid,'workflow_digest':preview['preview_digest'],'image':image};persist()
    return sid,card

def projection(buyer,skill,card):
    pid='vision-'+skill+'-'+RUN
    api(buyer,'/v1/projections',{'card':card,'projection_id':pid})
    return pid

def call(buyer,scope,skill,payload,task=None):
    task=task or 'vision-'+skill+'-'+RUN+'-'+buyer
    if task in report['calls']:
        detail=api(buyer,'/v1/calls/detail?scope='+scope+'&task_id='+task)
        return {'ok':report['calls'][task]['ok'],'result':{'assets':detail['assets']},
                'metadata':detail['metadata'],'settlement':detail['settlement']}
    offer=api(buyer,'/v1/payment-coordination/quote',{'projection_id':scope,'request':{'task_id':task,'skill':skill,'payload':payload}})
    result=api(buyer,'/v1/payment-coordination/free-execute',{'offer_id':offer['offer_id']})
    report['calls'][task]={'node':buyer,'scope':scope,'task_id':task,'trade_uid':result.get('metadata',{}).get('trade_uid'),'ok':result['ok']};persist()
    if not result['ok']:raise AssertionError(json.dumps(result,ensure_ascii=False))
    return result

def paid(buyer,scope,skill,payload,currency):
    task='vision-paid-'+currency+'-'+RUN
    saved=report.setdefault('paid_plans',{}).get(currency)
    if not saved and any(r['name']==currency+' 实际付款并交付' and r['passed'] for r in report['checks']):
        detail=api(buyer,'/v1/calls/detail?scope='+scope+'&task_id='+task)
        saved=detail['metadata']['admission_contract']['payment_plan_id']
    if saved:pid=saved
    else:
        offer=api(buyer,'/v1/payment-coordination/quote',{'projection_id':scope,'request':{'task_id':task,'skill':skill,'payload':payload}})
        index=next(i for i,o in enumerate(offer['options']) if o['currency']==currency)
        row=api(buyer,'/v1/payment-coordination/prepare',{'offer_id':offer['offer_id'],'option_index':index,'fee_cap_minor':'1000000000000000' if currency=='TETH' else '0','command_id':task})
        pid=row['record']['plan_id']
    report['paid_plans'][currency]=pid;persist()
    if currency=='TETH':
        api(buyer,'/v1/payment-coordination/plans/'+pid+'/pay',{})
        wait(lambda:api(buyer,'/v1/payment-coordination/plans/'+pid+'/reconcile',{}),lambda r:r['state']=='CONFIRMED')
    result=api(buyer,'/v1/payment-coordination/plans/'+pid+'/execute',{})
    check(currency+' 实际付款并交付',result['ok'] and result['result']['rows']==2)
    replay=api(buyer,'/v1/payment-coordination/plans/'+pid+'/execute',{})
    check(currency+' 同笔重试保留原交易',replay['metadata']['trade_uid']==result['metadata']['trade_uid'])
    return task,result

def refund(provider,buyer,scope,task,currency):
    dispute=api(buyer,'/v1/disputes/open',{'scope':scope,'task_id':task,'side':'requester','reason':'工程验收：验证自愿退款协商，不表示服务质量投诉'})
    did=dispute['id']
    proposal=api(buyer,'/v1/disputes/'+did+'/messages',{'kind':'PROPOSAL','body':{'action':'REFUND','text':'受控验收：双方同意退回测试币','amount_minor':1234,'currency':currency},'command_id':'proposal-'+task})
    mid=proposal['message_id']
    api(buyer,'/v1/disputes/'+did+'/agreements',{'proposal_id':mid,'command_id':'buyer-'+task})
    def accepted():
        try:return api(provider,'/v1/disputes/'+did+'/agreements',{'proposal_id':mid,'command_id':'seller-'+task})
        except RuntimeError:return None
    wait(accepted,lambda r:r is not None)
    wait(lambda:api(provider,'/v1/runtime')['resolution_agreements'],lambda rows:any(r['proposal']['message_id']==mid and r['state']=='BOTH_ACCEPTED' for r in rows))
    body={'proposal_id':mid,'fee_cap_minor':'1000000000000000' if currency=='TETH' else '0'}
    row=api(provider,'/v1/payment-coordination/refund',body)
    if row['state']!='CONFIRMED':row=wait(lambda:api(provider,'/v1/payment-coordination/refund-reconcile',body),lambda r:r['state']=='CONFIRMED')
    check(currency+' 原通道实际反向退款',row['state']=='CONFIRMED')

def main():
    runtimes={n:api(n,'/v1/runtime') for n in NODES}
    check('四个常驻节点均运行新增模块',all(r.get('maintenance') and r.get('reviewers') for r in runtimes.values()))
    token=get('http://127.0.0.1:18946/status')['token'];wallets={}
    for n in NODES:
        url='http://127.0.0.1:18945' if n=='desktop' else 'http://a2n-payment-testchain:8545'
        status=api(n,'/v1/payment-coordination/test-wallet',{'rpc_url':url})
        wallet=status['wallet_address'];wallets[n]=wallet
        check(n+' 创建独立加密测试钱包且重复操作不换钱包',api(n,'/v1/payment-coordination/test-wallet',{'rpc_url':url})['wallet_address']==wallet)
        cfg={'native':[{'currency':'TETH','network':'eip155:31337','rpc_url':url,'confirmations':1,'allow_http':True}],
             'x402':{'allow_http':True,'assets':[{'currency':'TST','network':'eip155:31337','asset':token,'name':'A2N Test Token','version':'1','rpc_url':url,'confirmations':1}]},
             'facilitator_url':'http://127.0.0.1:18946' if n=='desktop' else 'http://a2n-payment-testfacilitator:8546','allow_http':True}
        api(n,'/v1/payment-coordination/configure',{'config':cfg})
        if token_balance(token,wallet)<100000:post('http://127.0.0.1:18946/mint',{'address':wallet})
        for currency,limit in [('TETH',10**16),('TST',100000)]:
            policy=api(n,'/v1/runtime')['risk']
            api(n,'/v1/payment-coordination/budget',{'currency':currency,'per_trade':str(limit),'total_exposure':str(limit*10),
                'daily_spend':str(limit*10),'per_counterparty':str(limit*10),'expected_revision':policy['revision'],'max_pending':8})
        image=subprocess.run(['docker','image','inspect','a2n-utilities:20261009','--format','{{.Id}}'],capture_output=True,text=True,check=True).stdout.strip()
        api(n,'/v1/assets/media/configure',{'image':image})
        api(n,'/v1/reviewers/configure',{'python_image':image,'grant_execution':True})
    check('四个测试钱包使用不同地址',len(set(wallets.values()))==4)
    report['payment_environment']={'kind':'LOCAL_ANVIL','network':'eip155:31337','wallet_addresses':wallets,'test_token':token};persist()
    payload={'csv':'name,amount\na,2\nb,3'}
    prices={c:{'dimensions':[{'key':'call_count','amount':12345,'per':1}]} for c in ('TETH','TST')}
    sid,card=install('node-a','csv-profile','CSV 结构与金额汇总',prices)
    scopes={n:projection(n,'csv-profile',card) for n in ('desktop','node-b','node-c')}
    for i in range(10):
        buyer=('desktop','node-b','node-c')[i%3]
        result=call(buyer,scopes[buyer],'csv-profile',payload,'vision-csv-free-'+RUN+'-'+str(i))
        check('前十次样品第 '+str(i+1)+' 次完全跳过结算',result['settlement']['state']=='NOT_REQUIRED' and result['metadata']['admission_contract']['amount_minor']==0)
    samples=get('http://127.0.0.1:18881/public/v1/samples?service_id='+sid)
    check('样品固定公开十份，实际流式时间已有记录',samples['count']==10 and result['metadata']['local_stream_observation']['events']>0)
    task,result=paid('desktop',scopes['desktop'],'csv-profile',payload,'TETH')
    refund('node-a','desktop',scopes['desktop'],task,'TETH')
    before=report.get('x402_provider_balance_before')
    if before is None:
        before=token_balance(token,wallets['node-a']);report['x402_provider_balance_before']=before;persist()
    task,result=paid('node-b',scopes['node-b'],'csv-profile',payload,'TST')
    transfers=rpc('eth_getLogs',[{'address':token,'fromBlock':'0x0','toBlock':'latest','topics':[
        '0x'+keccak(text='Transfer(address,address,uint256)').hex(),
        '0x'+wallets['node-b'][2:].lower().rjust(64,'0'),'0x'+wallets['node-a'][2:].lower().rjust(64,'0')]}])
    check('x402 实际代币到账',any(int(event['data'],16)==12345 for event in transfers))
    refund('node-a','node-b',scopes['node-b'],task,'TST')
    cases=[('json-validate','node-b','desktop',{'json':'{"a":3}','required':['a']},'结构化数据校验'),
           ('python-transform','node-c','desktop',{},'Python 文本归一化函数'),
           ('image-card','desktop','node-b',{'title':'Agent exchange'},'PNG 信息卡'),
           ('audio-tone','node-b','desktop',{'frequency':440,'seconds':.4},'WAV 音频提示音'),
           ('document-render','node-c','desktop',{'title':'公开案例','paragraphs':['实际生成 UTF-8 文档。','结构与换行可以验证。']},'结构化文本文件'),
           ('video-title','desktop','node-c',{'title':'Actual generated clip'},'MP4 配色短片')]
    for skill,provider,buyer,payload,name in cases:
        sid,card=install(provider,skill,name);scope=projection(buyer,skill,card);task='vision-'+skill+'-'+RUN+'-'+buyer
        plan=None
        if skill=='python-transform':
            rd=api(buyer,'/v1/runtime')['reviewers']['reviewer_digest']
            checks={'requirements':[{'path':['code'],'op':'reviewer','expected':{'kind':'python-function','reviewer_digest':rd,'function':'normalize_text','cases':[{'args':['Ａ  B\nC'],'expected':'A B C'},{'args':[' hello\tworld '],'expected':'hello world'}]}}]}
            plan=api(buyer,'/v1/task-quality/plans',{'scope':scope,'task_id':task,'skill':skill,'rubric':'code-output@1','origin':'CONTROLLED','checks':checks})['plan']['plan_id']
        result=call(buyer,scope,skill,payload,task)
        check(name+' 跨节点实际交付',result['ok'])
        if plan:
            reviewed=wait(lambda:api(buyer,'/v1/task-quality/plans/'+plan),lambda r:r['review'] is not None,timeout=30)
            check('实际代码通过买方独立用例',reviewed['review']['objective']['requirements']['value']==1)
        if result['result'].get('assets'):
            ref=result['result']['assets'][0]
            owned=api(buyer,'/v1/assets/fetch',{'trade_uid':result['metadata']['trade_uid'],'asset_id':ref['asset_id']})
            check(name+' 私有成果签名传输并校验',owned['sha256']==ref['sha256'])
            base={'desktop':'http://127.0.0.1:18885','node-a':'http://127.0.0.1:18881','node-b':'http://127.0.0.1:18882','node-c':'http://127.0.0.1:18883'}[provider]
            public=get(base+'/public/v1/samples?service_id='+sid)
            check(name+' 公开样品仅保留摘要与预览',public['count']==1 and ref['asset_id'] not in json.dumps(public))
    report['passed']=True;persist()

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:main()
    finally:persist()
