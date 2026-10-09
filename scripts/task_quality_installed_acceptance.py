"""Real installed nodes; controlled task reviews never become production training."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid
import subprocess
import time
from urllib.parse import urlencode

from installed_selection_acceptance import api,get,wait,NODES

ROOT=Path(__file__).resolve().parents[1]

def main():
    run=uuid.uuid4().hex[:10];checks=[]
    original=json.loads((ROOT/'artifacts/selection-installed-2026-10-09.json').read_text(encoding='utf-8'))
    sid=original['service_id'];projection='task-quality-use-'+run
    report={'at':datetime.now(timezone.utc).isoformat(),'passed':False,'environment':'INSTALLED_WINDOWS_AND_DOCKER',
            'origin':'CONTROLLED','checks':checks,'service_id':sid,'projection_id':projection,'actual_calls':0}
    def check(name,ok,**facts):
        checks.append({'name':name,'passed':bool(ok),'facts':facts});print(('PASS ' if ok else 'FAIL ')+name,flush=True)
        if not ok: raise AssertionError(name)
    def failed(node,path,body,headers,code):
        try: api(node,path,body,headers=headers)
        except RuntimeError as exc: return 'HTTP '+str(code)+':' in str(exc)
        return False
    try:
        state={n:api(n,'/v1/runtime') for n in NODES}
        check('四个常驻节点启用任务评审与校准',all(r['task_quality']['enabled'] and r['calibration']['enabled'] for r in state.values()))
        check('实际运行完整新版 Windows 程序',state['desktop']['product_runtime']['mode']=='BUNDLED')
        points={n:api(n,'/v1/points') for n in NODES}
        policies={n:api(n,'/v1/policies') for n in NODES}
        feedback={n:state[n]['feedback'] for n in NODES}
        card=next(c for c in get('http://127.0.0.1:18883/public/v1/agents?skill=inspect')['cards']
                  if c['x-a2n']['projection']['service_id']==sid)
        search=api('desktop','/v1/coord/searches',{'skill':'inspect','required':{'version':card['version']},
                   'preferences':{'min_candidates':1},'round_budget':{'remote_operations':64,'received_bytes':4194304,
                   'duration_ms':15000,'introduction_depth':8,'candidate_limit':256,'probe_operations':1,'max_concurrency':2}},
                   headers={'Idempotency-Key':'quality-discovery-'+run})
        session=wait(lambda:api('desktop','/v1/coord/searches/'+search['search_id']),lambda r:r['state']!='RUNNING')
        report['search_id']=session['search_id']
        sample_url='http://127.0.0.1:18883/public/v1/samples?service_id='+sid
        samples_before=get(sample_url)['count'];count_before=get('http://127.0.0.1:18910/counts')['counts']['successful_inspections']
        plans={}
        for buyer in ('desktop','node-b'):
            api(buyer,'/v1/projections',{'card':card,'projection_id':projection})
            text='Agent produces useful work. Agent respects agreement.'
            task='task-review-'+run+'-'+buyer
            body={'scope':projection,'task_id':task,'skill':'inspect','rubric':'structured-output@1','origin':'CONTROLLED',
                  'checks':{'accuracy':[{'path':['characters'],'op':'equals','expected':len(text)},
                                        {'path':['text_sha256'],'op':'equals','expected':hashlib.sha256(text.encode()).hexdigest()}],
                            'completeness':[{'path':['keywords'],'op':'exists'},{'path':['paragraphs'],'op':'exists'}],
                            'format':[{'path':[],'op':'type','expected':'object'}]}}
            planned=api(buyer,'/v1/task-quality/plans',body)
            check(buyer+' 在调用前固定任务规则',planned['plan']['planned'] and planned['review'] is None)
            plan_id=planned['plan']['plan_id'];plans[buyer]=plan_id
            offer=api(buyer,'/v1/payment-coordination/quote',{'projection_id':projection,
                     'request':{'task_id':task,'skill':'inspect','payload':{'text':text}}})
            result=api(buyer,'/v1/payment-coordination/free-execute',{'offer_id':offer['offer_id']})
            report['actual_calls']+=1
            check(buyer+' 实际文本交付',result['ok'] and result['result']['characters']==len(text)
                  and result['settlement']['state']=='NOT_REQUIRED')
            reviewed=wait(lambda:api(buyer,'/v1/task-quality/plans/'+plan_id),lambda r:r['review'] is not None)
            check(buyer+' 后台自动评审且不保存原文',reviewed['review']['score']['objective_prediction']==1
                  and reviewed['review']['context']['workload_bucket']=='small' and text not in json.dumps(reviewed))
            human={'scores':{'accuracy':5,'completeness':5,'usefulness':4},'consent':True,'withdrawn':False}
            rated=api(buyer,'/v1/task-quality/plans/'+plan_id+'/review',human,headers={'If-Match':'"0"'})
            check(buyer+' 人工细项与客观检查分开',rated['review']['human']['usefulness']==4
                  and rated['review']['objective']==reviewed['review']['objective'])
        p=api('desktop','/v1/task-quality/plans/'+plans['desktop'])
        context=p['review']['context'];report['context']=context;report['plans']=plans
        status=api('desktop','/v1/calibration/status?'+urlencode(context))
        model=api('desktop','/v1/calibration/fit',{'context':context},headers={'Idempotency-Key':'fit-controlled-'+run})
        check('真实执行的受控数据不会冒充生产校准',status['controlled_reviews']>=1 and
              status['consented_production_reviews']==0 and model['state']=='INSUFFICIENT_DATA' and model['support']['samples']==0)
        check('不能应用未通过验证的模型',failed('desktop','/v1/calibration/activate',
              {'context':context,'model_id':model['model_id']},{'If-Match':'"0"'},400))
        check('改评要求当前版本',failed('desktop','/v1/task-quality/plans/'+plans['desktop']+'/review',
              {'scores':{},'consent':False,'withdrawn':False},{'If-Match':'"0"'},409))
        session=api('desktop','/v1/coord/searches/'+report['search_id'])
        task={'rubric':'structured-output@1','skill':'inspect','workload_bucket':'small'}
        def ranked():
            result=api('desktop','/v1/selection/rank',{'search_id':session['search_id'],'result_revision':session['result_revision'],'task':task})
            return next(r for r in result['items'] if r['service_id']==sid)
        row=ranked()
        check('任务级质量进入当前推荐并标记资料有限',row['dimensions']['quality']['value']>.5
              and row['dimensions']['quality']['status']=='LIMITED' and row['dimensions']['quality']['support']['samples']==1)
        old_credit=row['dimensions']['credit']['value']
        withdrawn=api('desktop','/v1/task-quality/plans/'+plans['desktop']+'/review',
                       {'scores':{},'consent':False,'withdrawn':True},headers={'If-Match':'"1"'})
        row=ranked()
        check('撤回移除任务贡献但不扣信用',row['dimensions']['quality']['value'] is None
              and abs(row['dimensions']['credit']['value']-old_credit)<.0001 and withdrawn['review']['revision']==2)
        api('desktop','/v1/task-quality/plans/'+plans['desktop']+'/review',
            {'scores':{'accuracy':5,'completeness':5,'usefulness':4},'consent':False,'withdrawn':False},headers={'If-Match':'"2"'})
        before=api('node-b','/v1/task-quality/plans/'+plans['node-b'])
        subprocess.run(['docker','restart','a2n-acceptance-node-b-1'],capture_output=True,check=True,timeout=60)
        for _ in range(60):
            try:
                after=api('node-b','/v1/task-quality/plans/'+plans['node-b']);break
            except RuntimeError:time.sleep(.5)
        check('Docker 重启恢复规则与评审版本',before==after)
        check('评审和排序不再调用 Agent 或占用样品',get(sample_url)['count']==samples_before and
              get('http://127.0.0.1:18910/counts')['counts']['successful_inspections']==count_before+2)
        check('原评价、信誉策略与积分账本不受任务评审影响',all(api(n,'/v1/points')==points[n]
              and api(n,'/v1/policies')==policies[n] and api(n,'/v1/runtime')['feedback']==feedback[n] for n in NODES))
        report['passed']=True
    finally:
        (ROOT/'artifacts/task-quality-installed-2026-10-09.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
