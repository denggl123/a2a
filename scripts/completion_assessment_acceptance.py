"""Controlled real media reviews and local discovery-to-choice outcomes."""
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from installed_selection_acceptance import api,wait,get,ROOT

base=json.loads((ROOT/'artifacts/vision-completion-installed-2026-10-09.json').read_text(encoding='utf-8'))
run=base['run_id'];checks=[]
report={'passed':False,'origin':'CONTROLLED','label_source':'ASSISTANT','checks':checks}
out=ROOT/'artifacts/vision-completion-assessment-2026-10-09.json'
def check(name,ok,**facts):
    checks.append({'name':name,'passed':bool(ok),'facts':facts});print(('PASS ' if ok else 'FAIL ')+name,flush=True)
    if not ok:raise AssertionError(name)
def invoke(buyer,scope,skill,payload,task):
    # Replays query the immutable local delivery, never change an expired quote.
    try:
        detail=api(buyer,'/v1/calls/detail?'+urlencode({'scope':scope,'task_id':task}))
        if detail['state'] in {'COMPLETED','ACCEPTED','SETTLED'}:return detail
    except RuntimeError:pass
    offer=api(buyer,'/v1/payment-coordination/quote',{'projection_id':scope,'request':{'task_id':task,'skill':skill,'payload':payload}})
    return api(buyer,'/v1/payment-coordination/free-execute',{'offer_id':offer['offer_id']})
def main():
    for skill,buyer,rubric,payload,rules in [
        ('audio-tone','node-c','audio-output@1',{'seconds':.4,'frequency':440},[{'path':['duration'],'op':'range','minimum':.399,'maximum':.401},{'path':['sample_rate'],'op':'equals','expected':16000}]),
        ('video-title','node-c','video-output@1',{'title':'Actual clip'},[{'path':['width'],'op':'equals','expected':320},{'path':['height'],'op':'equals','expected':180}])]:
        service=base['services'][skill];card=api(service['node'],'/v1/publish',{'service_id':service['service_id']})['card']
        scope='media-quality-'+skill+'-'+run;api(buyer,'/v1/projections',{'projection_id':scope,'card':card})
        task='media-quality-'+skill+'-'+run;rd=api(buyer,'/v1/runtime')['reviewers']['reviewer_digest']
        plan=api(buyer,'/v1/task-quality/plans',{'scope':scope,'task_id':task,'skill':skill,'rubric':rubric,'origin':'CONTROLLED',
            'checks':{'format':[{'path':['assets',0],'op':'reviewer','expected':{'kind':'media-constraints','reviewer_digest':rd,'constraints':rules}}]}})
        pid=plan['plan']['plan_id'];result=invoke(buyer,scope,skill,payload,task)
        reviewed=wait(lambda:api(buyer,'/v1/task-quality/plans/'+pid),lambda r:r['review'] is not None,timeout=40)
        if reviewed['review']['objective']['format']['value'] is None:
            reviewed=api(buyer,'/v1/task-quality/plans/'+pid+'/recheck',{},headers={'If-Match':'"'+str(reviewed['review']['revision'])+'"'})
        check(skill+' 实际文件规格经独立检查',reviewed['review']['objective']['format']['value']==1)
        if skill=='video-title':
            public=wait(lambda:get('http://127.0.0.1:18885/public/v1/samples?service_id='+service['service_id']),
                lambda p:any(item.get('media_preview') for item in p['samples']),timeout=30)
            check('视频后台预览可见且样品没有变成原始文件',public['count']>=2 and 'video/mp4' not in json.dumps([item.get('media_preview') for item in public['samples']]))
    service=base['services']['json-validate'];scope='vision-json-validate-'+run
    search=api('desktop','/v1/coord/searches',{'skill':'json-validate','preferences':{'min_candidates':1}},headers={'Idempotency-Key':'completion-choice-'+run})
    session=wait(lambda:api('desktop','/v1/coord/searches/'+search['search_id']),lambda r:r['state']!='RUNNING')
    ranked=api('desktop','/v1/selection/rank',{'search_id':search['search_id'],'result_revision':session['result_revision'],
         'task':{'skill':'json-validate','task_class':'own-json-validation','keywords':['JSON','数据']}})
    item=next(row for row in ranked['items'] if row['service_id']==service['service_id'])
    task='selection-outcome-'+run
    choice=api('desktop','/v1/selection/choices',{'snapshot_id':ranked['snapshot_id'],'candidate_key':item['key'],'scope':scope,'task_id':task})
    check('选择绑定已发现供给和实际调用任务',choice['discovery_to_choice_seconds'] is not None and choice['rank']>=1)
    plan=api('desktop','/v1/task-quality/plans',{'scope':scope,'task_id':task,'skill':'json-validate','rubric':'structured-output@1','task_class':'own-json-validation','origin':'CONTROLLED',
        'checks':{'accuracy':[{'path':['valid_json'],'op':'equals','expected':True}]}})
    result=invoke('desktop',scope,'json-validate',{'json':'{"schema":1}','required':['schema']},task)
    pid=plan['plan']['plan_id'];review=wait(lambda:api('desktop','/v1/task-quality/plans/'+pid),lambda r:r['review'] is not None)
    api('desktop','/v1/task-quality/plans/'+pid+'/review',{'scores':{'usefulness':5},'consent':False,'withdrawn':False,'label_source':'ASSISTANT'},headers={'If-Match':'"'+str(review['review']['revision'])+'"'})
    outcomes=api('desktop','/v1/selection/outcomes');row=next(r for r in outcomes['items'] if r['task_id']==task)
    check('选用效果从真实交付与评审回填',row['execution']=='DELIVERED' and row['label_source']=='ASSISTANT' and row['usefulness']==5)
    check('助手验收不会冒充本人满意度',outcomes['assistant_rated']>=1 and outcomes['human_rated']==0 and outcomes['usefulness_mean'] is None)
    report.update(passed=True,search_id=search['search_id'],choice=choice,outcomes=outcomes)
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:main()
    finally:out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
