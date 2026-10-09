import json
from contextlib import ExitStack

from a2n_sdk.ports import CallRequest
from tests.test_selection_integration import node, http, search, wait_for


def test_actual_delivery_review_private_calibration_gate_and_local_ranking(tmp_path,monkeypatch):
    with ExitStack() as stack:
        provider=node(stack,tmp_path/'provider');buyer=node(stack,tmp_path/'buyer',[provider.runtime.local_base_url])
        calls=[]
        provider.runtime.mount_callable({'name':'真实提取服务','version':'1','skills':[{'id':'write','name':'write'}]},
            lambda p:calls.append(p) or {'field':'correct','private':'secret-output'},service_id='svc')
        buyer.runtime.import_agent(provider.runtime.project_binding('svc'),projection_id='use')
        body={'scope':'use','task_id':'task','rubric':'structured-output@1','skill':'write','origin':'CONTROLLED',
              'checks':{'accuracy':[{'path':['field'],'op':'equals','expected':'correct'}],
                        'completeness':[{'path':['field'],'op':'exists'}],'format':[{'path':[],'op':'type','expected':'object'}]}}
        assert http(buyer,'/v1/task-quality/rubrics',auth=False)[0]==401
        code,p=http(buyer,'/v1/task-quality/plans',body)
        assert code==201,p
        pid=p['plan']['plan_id']
        result=buyer.calls.invoke('use',CallRequest(skill='write',task_id='task',payload='private-input'))
        assert result.ok and result.settlement['state']=='NOT_REQUIRED'
        reviewed=wait_for(lambda:(r if (r:=buyer.task_quality.get(pid))['review'] else None))
        assert reviewed['plan']['planned'] and reviewed['review']['score']['objective_prediction']==1
        assert reviewed['review']['artifact_digest'] and 'secret-output' not in json.dumps(reviewed)
        revpath='/v1/task-quality/plans/'+pid+'/review'
        feedback={'scores':{'accuracy':5,'completeness':5,'usefulness':4},'consent':True,'withdrawn':False}
        assert http(buyer,revpath,feedback)[0]==428
        code,value=http(buyer,revpath,feedback,headers={'If-Match':'"0"'})
        assert code==200,value
        assert http(buyer,revpath,feedback,headers={'If-Match':'"0"'})[0]==409
        s=search(buyer)
        def forbidden(*args,**kwargs): raise AssertionError('review ranking made a remote request')
        monkeypatch.setattr(buyer.coord_network,'perform',forbidden)
        request={'search_id':s.search_id,'result_revision':s.result_revision,
                 'task':{'rubric':'structured-output@1','workload_bucket':'small'}}
        code,ranked=http(buyer,'/v1/selection/rank',request)
        assert code==200,ranked
        metric=ranked['items'][0]['dimensions']['quality']
        assert metric['value']>.5 and metric['raw']['rubric']=='structured-output@1'
        assert metric['support']['samples']==1 and metric['status']=='LIMITED'
        assert 'private-input' not in json.dumps(ranked) and 'secret-output' not in json.dumps(ranked)
        assert len(calls)==1 and len(provider.trials.samples('svc'))==1
        context=reviewed['review']['context']
        code,model=http(buyer,'/v1/calibration/fit',{'context':context},headers={'Idempotency-Key':'real-data-only'})
        assert code==201,model
        assert model['state']=='INSUFFICIENT_DATA' and model['support']['samples']==0
        assert buyer.calibration.status(context)['controlled_reviews']==1
        assert http(buyer,'/v1/calibration/activate',{'context':context,'model_id':model['model_id']},headers={'If-Match':'"0"'})[0]==400
        # The user can delegate real use to an assistant. Provenance remains explicit
        # and this context is separate from their personal ratings and controlled tests.
        body={**body,'task_id':'delegated-task','origin':'PRODUCTION','task_class':'document-audit-assistant'}
        code,p=http(buyer,'/v1/task-quality/plans',body)
        assert code==201,p
        result=buyer.calls.invoke('use',CallRequest(skill='write',task_id='delegated-task',payload='delegated-use'))
        assert result.ok
        pid=p['plan']['plan_id']
        value=wait_for(lambda:(r if (r:=buyer.task_quality.get(pid))['review'] else None))
        feedback={**feedback,'label_source':'ASSISTANT','scores':{'accuracy':5,'completeness':5,'usefulness':3}}
        code,value=http(buyer,'/v1/task-quality/plans/'+pid+'/review',feedback,headers={'If-Match':'"0"'})
        assert code==200,value
        context=value['review']['context'];status=buyer.calibration.status(context)
        assert status['consented_production_reviews']==1 and status['qualified_days']==1
        assert status['label_sources']=={'ASSISTANT':1} and not status['controlled_reviews']
        code,model=http(buyer,'/v1/calibration/fit',{'context':context},headers={'Idempotency-Key':'assistant-use'})
        assert code==201 and model['support']['samples']==1 and model['state']=='INSUFFICIENT_DATA'
        assert model['support']['label_sources']=={'ASSISTANT':1}
        code,_=http(buyer,'/v1/task-quality/plans/'+pid+'/review',
                    {**feedback,'label_source':'UNKNOWN'},headers={'If-Match':'"1"'})
        assert code==400
        assert not buyer.store.items('feedback') and not buyer.store.items('points_transactions')
        assert buyer.policies.get()['values']['mode']=='SHADOW'
