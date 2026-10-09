"""Delegated real document inventory through installed nodes, with explicit AI labels.

Inputs are public project documentation; deliveries and labels come from actual
delegated use at its recorded time. The user-authorized usage and later assistant judgments
use a separate task context. Existing controlled acceptance stays controlled.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
import uuid

from installed_selection_acceptance import api, get, wait

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'artifacts/project-agent-usage-2026-10-09.json'
DOCUMENTS=('VISION.md','POINTS.md','PAYMENT-COORDINATION.md','COORDINATION-RULES.md',
           'SDK-ARCHITECTURE.md','FEEDBACK-RULES.md','PERSONALIZED-AGENT-SELECTION.md')
NODES=('node-a','node-b','node-c')
CONTEXT={'rubric':'structured-output@1','task_class':'project-document-inventory-assistant',
         'skill':'inspect','workload_bucket':'small'}

def save(report):
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

def initial():
    if REPORT.exists():
        return json.loads(REPORT.read_text(encoding='utf-8'))
    run=uuid.uuid4().hex[:10];cases=[]
    for name in DOCUMENTS:
        text=(ROOT/'docs'/name).read_text(encoding='utf-8')
        sections=re.split(r'(?m)^## ',text)[1:7]
        if len(sections)!=6: raise ValueError('Document requires six meaningful sections: '+name)
        for section in sections:
            heading=section.splitlines()[0].strip();excerpt=('## '+section.strip())[:900]
            cases.append({'document':'docs/'+name,'heading':heading,'text':excerpt})
    inputs=ROOT/'.tmp'/('document-agent-inputs-'+run+'.json')
    inputs.write_text(json.dumps(cases,ensure_ascii=False),encoding='utf-8')
    report={'run_id':run,'started_at':datetime.now(timezone.utc).isoformat(),'origin':'PRODUCTION',
            'label_source':'ASSISTANT','context':CONTEXT,'purpose':'为项目文档盘点提供实际结构统计、主题检索提示和阅读预览',
            'scope_notice':'用户受托调用及助手评价；三个节点在同一电脑，使用同一个真实文本处理服务，不代表三个独立用户或算法。',
            'inputs_path':str(inputs.relative_to(ROOT)),'cases':[], 'suppliers':{},'passed':False}
    save(report);return report

def collect():
    report=initial();run=report['run_id'];sid='document-inspector-'+run
    cases=json.loads((ROOT/report['inputs_path']).read_text(encoding='utf-8'))
    if 'points_before' not in report:
        # Compare in memory in this run; persist only a digest, never account contents.
        points=api('desktop','/v1/points')
        report['points_before']=hashlib.sha256(json.dumps(points,sort_keys=True).encode()).hexdigest()
        report['agent_calls_before']=get('http://127.0.0.1:18910/counts')['counts']['successful_inspections']
        save(report)
    for node in NODES:
        if node in report['suppliers']:continue
        card={'name':'项目文档结构分析 Agent','version':'1.0.0-document-use.'+run,
              'description':'Python 文本处理：结构计数、词频及前 120 字预览。中文按单字计频，不提供语义分词或抽象摘要。',
              'skills':[{'id':'inspect','name':'盘点文档结构'}],
              'x-a2n':{'uid':str(uuid.uuid4()),'price_book':{'inspect':{'CNY':{'dimensions':[{'key':'call_count','amount':0,'per':1}]}}}}}
        api(node,'/v1/bindings/http',{'card':card,'service_id':sid,'endpoint':'http://a2n-business-text-agent:9000/invoke',
                                    'protocol':'json','listed':True})
        published=api(node,'/v1/publish',{'service_id':sid})['card']
        projection='document-use-'+run+'-'+node
        api('desktop','/v1/projections',{'card':published,'projection_id':projection})
        report['suppliers'][node]={'service_id':sid,'projection_id':projection,
                                 'provider_did':published['x-a2n']['projection']['node_did']}
        save(report)
    if 'search_id' not in report:
        search=api('desktop','/v1/coord/searches',{'skill':'inspect','required':{'version':'1.0.0-document-use.'+run},
                   'preferences':{'min_candidates':3},'round_budget':{'remote_operations':64,'received_bytes':4194304,
                   'duration_ms':15000,'introduction_depth':8,'candidate_limit':256,'probe_operations':1,'max_concurrency':2}},
                   headers={'Idempotency-Key':'document-discovery-'+run})
        wait(lambda:api('desktop','/v1/coord/searches/'+search['search_id']),lambda r:r['state']!='RUNNING')
        report['search_id']=search['search_id'];save(report)
    for i,case in enumerate(cases):
        if i<len(report['cases']):continue
        node=NODES[i%3];supplier=report['suppliers'][node];projection=supplier['projection_id']
        text=case['text'];task='document-'+run+'-'+str(i)
        planned=api('desktop','/v1/task-quality/plans',{'scope':projection,'task_id':task,**CONTEXT,'origin':'PRODUCTION',
            'checks':{'accuracy':[{'path':['characters'],'op':'equals','expected':len(text)},
                                  {'path':['text_sha256'],'op':'equals','expected':hashlib.sha256(text.encode()).hexdigest()}],
                      'completeness':[{'path':[k],'op':'exists'} for k in ('keywords','paragraphs','summary','tokens')],
                      'format':[{'path':[],'op':'type','expected':'object'},
                                {'path':['keywords'],'op':'type','expected':'array'},
                                {'path':['summary'],'op':'type','expected':'string'}]}})
        assert planned['plan']['planned'] or planned['review'], 'Rule must precede execution'
        offer=api('desktop','/v1/payment-coordination/quote',{'projection_id':projection,
                  'request':{'task_id':task,'skill':'inspect','payload':{'text':text}}})
        assert offer['free_reason'], 'Only explicitly free calls are authorized here'
        result=api('desktop','/v1/payment-coordination/free-execute',{'offer_id':offer['offer_id']})
        assert result['ok'] and result['settlement']['state']=='NOT_REQUIRED',result
        reviewed=wait(lambda:api('desktop','/v1/task-quality/plans/'+planned['plan']['plan_id']),lambda r:r['review'] is not None)
        assert reviewed['review']['score']['objective_prediction']==1 and reviewed['review']['context']==CONTEXT
        report['cases'].append({'index':i,'document':case['document'],'heading':case['heading'],'node':node,
                                'task_id':task,'plan_id':planned['plan']['plan_id'],
                                'trade_uid':reviewed['review']['trade_uid'],'delivered_at':reviewed['review']['at'],
                                'result':result['result'],'objective':reviewed['review']['score']['objective_prediction'],
                                'free_reason':offer['free_reason']})
        save(report)
        print('DELIVERED '+str(i+1)+'/42 '+node+' '+case['document']+' '+case['heading'],flush=True)
    assert len(report['cases'])==42
    report['collected_at']=datetime.now(timezone.utc).isoformat();save(report)
    print('Collected real deliveries; independent assistant judgments still required.',flush=True)

def review(labels_path):
    report=initial();labels=json.loads(Path(labels_path).read_text(encoding='utf-8'))
    assert len(report['cases'])==42 and len(labels)==42
    for case,label in zip(report['cases'],labels):
        assert label['index']==case['index'] and type(label['usefulness']) is int and 1<=label['usefulness']<=5 and label['reason']
        item=api('desktop','/v1/task-quality/plans/'+case['plan_id']);r=item['review']
        if r.get('label_source')!='ASSISTANT' or r['human'].get('usefulness')!=label['usefulness'] or not r['consent']:
            item=api('desktop','/v1/task-quality/plans/'+case['plan_id']+'/review',
                     {'scores':{'accuracy':5,'completeness':5,'usefulness':label['usefulness']},'consent':True,
                      'withdrawn':False,'label_source':'ASSISTANT'},headers={'If-Match':'"'+str(r['revision'])+'"'})
        assert item['review']['label_source']=='ASSISTANT'
        case['assistant_judgment']={'usefulness':label['usefulness'],'reason':label['reason']};save(report)
    status=api('desktop','/v1/calibration/status?'+urlencode(CONTEXT))
    model=api('desktop','/v1/calibration/fit',{'context':CONTEXT},headers={'Idempotency-Key':'document-fit-'+report['run_id']})
    assert status['consented_production_reviews']>=42 and status['qualified_providers']==3
    assert status['label_sources'].get('ASSISTANT',0)>=42 and not status['controlled_reviews']
    assert model['support']['samples']>=42 and model['support']['providers']==3
    assert model['support']['days']>=1
    points=api('desktop','/v1/points')
    assert hashlib.sha256(json.dumps(points,sort_keys=True).encode()).hexdigest()==report['points_before']
    report['status']=status;report['calibration_trial']={k:v for k,v in model.items() if k not in ('knots',)}
    report['agent_calls_after']=get('http://127.0.0.1:18910/counts')['counts']['successful_inspections']
    report['reviewed_at']=datetime.now(timezone.utc).isoformat();report['passed']=True;save(report)
    render(report)
    print(json.dumps({'real_qualified_records':status['consented_production_reviews'],'providers':status['qualified_providers'],
                      'days':status['qualified_days'],'label_sources':status['label_sources'],
                      'model_state':model['state'],'active':status['active']['state']},ensure_ascii=False),flush=True)

def render(report):
    lines=['# 项目文档片段的实际 Agent 使用记录','','日期：2026-10-09。用户委托助手使用与评审，共 42 次实际交付。',
           '','目的：盘点公开项目文档的结构、检索提示和阅读预览；全部经节点投影和交易调用流程完成。',
           '','三个供给节点位于同一台电脑，采用同一个文本处理服务。评价来源为助手，不代表独立自然人反馈。',
           '','结构检查与细项评价独立保存。本批评价只在 `project-document-inventory-assistant` 口径使用，原本人评价与信用保持原值。',
           '','以下统计对应各章节开头最多 900 字的实际调用片段，预览取片段前 120 字。',
           '','| 文档章节片段 | 供应节点 | 字符 | 段落 | Token | 有用程度 / 5 | 使用判断 |','| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for case in report['cases']:
        r=case['result'];label=case['assistant_judgment']
        cells=[case['document']+'：'+case['heading'],case['node'],r['characters'],r['paragraphs'],r['tokens'],label['usefulness'],label['reason']]
        lines.append('| '+' | '.join(str(v).replace('|','／').replace('\n',' ') for v in cells)+' |')
    status=report['status'];model=report['calibration_trial']
    lines+=['','## 校准准备状态','',f"合格实际记录 {status['consented_production_reviews']} 笔，供应节点 {status['qualified_providers']} 个，真实记录日期 {status['qualified_days']} 天；全部为助手受托评价。",
            '',f"本次试算：`{model['state']}`；当前映射：`{status['active']['state']}`。实际时间及评价来源没有改写。",
            '','校准应用仍要求 3 天实际记录，以及后续记录验证确有改善。数量达标不会自动开启模型。']
    (ROOT/'artifacts/project-document-inventory-2026-10-09.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('collect','review'))
    parser.add_argument('--labels');args=parser.parse_args()
    if args.command=='collect':collect()
    elif not args.labels:parser.error('review requires --labels with independent judgments')
    else:review(args.labels)
