"""Read-only verification of delegated use on the installed Windows/Docker nodes."""
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from installed_selection_acceptance import api,get

ROOT=Path(__file__).resolve().parents[1]

def main():
    report=json.loads((ROOT/'artifacts/project-agent-usage-2026-10-09.json').read_text(encoding='utf-8'))
    assert report['passed']
    checks=[]
    def check(name,condition,**facts):
        checks.append({'name':name,'passed':bool(condition),'facts':facts})
        assert condition,name
        print('PASS '+name,flush=True)
    check('42 笔实际交付都有独立交易标识',len(report['cases'])==len({c['trade_uid'] for c in report['cases']})==42)
    check('实际服务计数正好增加 42 次',report['agent_calls_after']-report['agent_calls_before']==42)
    for node,port in (('node-a',18881),('node-b',18882),('node-c',18883)):
        supplier=report['suppliers'][node]
        samples=get('http://127.0.0.1:'+str(port)+'/public/v1/samples?'+urlencode({'service_id':supplier['service_id']}))
        check(node+' 前十次固定公开、后四次不增加样品',samples['count']==samples['samples_total']==10 and samples['completed']==14)
    for case in report['cases']:
        r=api('desktop','/v1/task-quality/plans/'+case['plan_id'])['review']
        assert r['planned'] and r['origin']=='PRODUCTION' and r['label_source']=='ASSISTANT' and r['consent']
        assert r['human']['usefulness']==case['assistant_judgment']['usefulness']
        assert r['context']==report['context']
    check('42 份受托评价来源与当前规则一致',True)
    session=api('desktop','/v1/coord/searches/'+report['search_id'])
    ranked=api('desktop','/v1/selection/rank',{'search_id':report['search_id'],
               'result_revision':session['result_revision'],'task':report['context']})
    rows=[r for r in ranked['items'] if r['service_id'] in {s['service_id'] for s in report['suppliers'].values()}]
    check('三个供给的任务推荐读取实际评价、标明助手来源',len(rows)==3 and all(
          r['dimensions']['quality']['support']['label_sources']=={'ASSISTANT':14}
          and r['dimensions']['quality']['support']['samples']==14
          and r['dimensions']['quality']['status']=='LIMITED' for r in rows))
    old=json.loads((ROOT/'artifacts/task-quality-installed-2026-10-09.json').read_text(encoding='utf-8'))
    status=api('desktop','/v1/calibration/status?'+urlencode(old['context']))
    check('旧验收记录仍为受控数据、没有改写成实际使用',status['consented_production_reviews']==0 and status['controlled_reviews']>=1)
    status=api('desktop','/v1/calibration/status?'+urlencode(report['context']))
    check('校准看到 42 笔、3 供应节点、1 个实际日期',status['consented_production_reviews']==42
          and status['qualified_providers']==3 and status['qualified_days']==1 and status['label_sources']=={'ASSISTANT':42})
    check('时间不足时保持默认映射',status['active']['state']=='DEFAULT' and report['calibration_trial']['state']=='INSUFFICIENT_DATA')
    check('全部评审完成、无遗留等待计划',api('desktop','/v1/runtime')['task_quality']['pending']==0)
    result={'passed':True,'checks':checks,'environment':'INSTALLED_WINDOWS_AND_THREE_DOCKER_NODES',
            'source':'ASSISTANT_DELEGATED_USE','context':report['context'],'calibration_status':status}
    (ROOT/'artifacts/project-agent-usage-verification-2026-10-09.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
