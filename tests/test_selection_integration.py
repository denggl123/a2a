"""Real signed calls and local assessment ports: no hidden invocation or settlement."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack
from types import SimpleNamespace

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.coordination import SearchSpec
from a2n_sdk.experience_service import ExperienceService
from a2n_sdk.policies import PolicyBook
from a2n_sdk.ports import CallRequest
from a2n_sdk.selection_adapter import SelectionFactAdapter
from a2n_sdk.storage import LocalStore


def wait_for(fn, seconds=10):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=fn()
        if value:
            return value
        time.sleep(.025)
    raise AssertionError("local projection did not finish")


def node(stack, home, roots=()):
    protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    value=Daemon(home,port=0,protector=protector,public_nodes=roots,coord_allow_networks=['127.0.0.0/8']).start()
    stack.callback(value.stop)
    value.management.discovery_public_base=value.runtime.local_base_url
    return value


def http(node, path, body=None, *, method=None, auth=True, headers=None):
    values={'Content-Type':'application/json',**(headers or {})}
    if auth:
        values['X-A2N-Local-Token']=node.runtime.management_token
    req=urllib.request.Request(node.runtime.local_base_url+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=values,method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=10) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code,json.load(exc)


def search(node):
    started=node.coordination.start(SearchSpec(skill='write',preferences={'min_candidates':1}),os.urandom(8).hex())
    wait_for(lambda: node.coordination.get(started.search_id).state!='RUNNING')
    return node.coordination.get(started.search_id)


def mount(provider, executed):
    provider.runtime.mount_callable({'name':'真实文本 Agent','version':'1',
        'skills':[{'id':'write','name':'write'}]},lambda p: executed.append(p) or {'text':p},service_id='svc')
    return provider.runtime.project_binding('svc')


def test_real_call_v2_feedback_revision_dispute_credit_and_local_only_ranking(tmp_path, monkeypatch):
    with ExitStack() as stack:
        provider=node(stack,tmp_path/'provider')
        buyer=node(stack,tmp_path/'buyer',[provider.runtime.local_base_url])
        executed=[]
        card=mount(provider,executed)
        buyer.runtime.import_agent(card,projection_id='use')
        result=buyer.calls.invoke('use',CallRequest(skill='write',task_id='trade',payload='private payload'))
        assert result.ok and result.metadata['trade_facts']['execution']=='DELIVERED'
        samples=provider.trials.samples('svc')
        assert len(samples)==1
        _,feedback=buyer.management.command('/v1/feedback/open',{'scope':'use','task_id':'trade',
            'dimensions':{'quality':1,'honoring':5},'note':'private note'})
        assert feedback['v']=='a2n-feedback/2'
        publication=buyer.experience.publish(feedback['feedback_id'])
        assert publication['v']=='a2n-public-feedback/2' and buyer.experience.validate(publication)
        service={'kind':'service','provider_did':provider.identity.did,'service_id':'svc'}
        wait_for(lambda: buyer.selection_facts._inputs(service)['local'])
        session=search(buyer)
        assert session.candidate_count==1
        # After discovering the card, reads must work with every remote path forbidden.
        def forbidden(*args,**kwargs):
            raise AssertionError('ranking performed remote I/O')
        monkeypatch.setattr(buyer.coord_network,'perform',forbidden)
        monkeypatch.setattr(buyer.experience_service.network,'fetch',forbidden)
        body={'search_id':session.search_id,'result_revision':session.result_revision,'task':{'workload_bucket':'small'}}
        code,first=http(buyer,'/v1/selection/rank',body)
        assert code==200,first
        row=first['items'][0]
        assert row['dimensions']['quality']['value']<.5
        assert row['dimensions']['credit']['value']>.5
        assert row['dimensions']['time']['value'] is not None
        assert row['dimensions']['reliability']['value']>.5
        assert 'private payload' not in json.dumps(first) and 'private note' not in json.dumps(first)
        assert executed==['private payload'] and provider.trials.samples('svc')==samples
        key=urllib.parse.quote(row['key'],safe='')
        assert http(buyer,f"/v1/selection/snapshots/{first['snapshot_id']}/explain/{key}")[0]==200
        assert http(buyer,'/v1/selection/profiles',auth=False)[0]==401
        _,revised=buyer.management.command('/v1/feedback/revise',{'feedback_id':feedback['feedback_id'],
            'dimensions':{'quality':5,'honoring':5}})
        wait_for(lambda: any(r['revision']==2 for r in buyer.selection_facts._inputs(service)['local']))
        second=buyer.selection.assess(body)
        assert second['items'][0]['dimensions']['quality']['value']>row['dimensions']['quality']['value']
        assert http(buyer,f"/v1/selection/snapshots/{first['snapshot_id']}")[1]['items'][0]==row
        with pytest.raises(ValueError,match='RECORDED_DISPUTE'):
            buyer.management.command('/v1/feedback/revise',{'feedback_id':feedback['feedback_id'],
                'dimensions':{'honoring':5,'dispute_handling':1}})
        _,dispute=buyer.management.command('/v1/disputes/open',{'scope':'use','task_id':'trade','reason':'结果不合预期'})
        # The fact that a complaint exists cannot change credit by itself.
        owner={'kind':'provider','provider_did':provider.identity.did}
        wait_for(lambda: buyer.selection_facts._inputs(owner).get('dispute_count')==1)
        assert buyer.selection_facts.subject_view(owner,time.time())['credit']['value']==pytest.approx(row['dimensions']['credit']['value'],abs=1e-5)
        buyer.management.command('/v1/feedback/revise',{'feedback_id':feedback['feedback_id'],
            'dimensions':{'quality':5,'honoring':5,'dispute_handling':1}})
        wait_for(lambda: any(r['revision']==3 for r in buyer.selection_facts._inputs(owner)['local']))
        bad=buyer.selection_facts.subject_view(owner,time.time())['credit']['value']
        assert bad<row['dimensions']['credit']['value']
        buyer.management.command('/v1/disputes/withdraw',{'dispute_id':dispute['id'],'note':'已沟通'})
        time.sleep(.1)
        assert buyer.selection_facts.subject_view(owner,time.time())['credit']['value']==pytest.approx(bad,abs=1e-5)
        assert buyer.policies.get()['values']['mode']=='SHADOW'
        assert executed==['private payload'] and provider.trials.samples('svc')==samples


def test_public_revision_withdrawal_buyer_credit_and_budgeted_refresh(tmp_path):
    with ExitStack() as stack:
        provider=node(stack,tmp_path/'provider')
        buyer=node(stack,tmp_path/'buyer')
        observer=node(stack,tmp_path/'observer',[buyer.runtime.local_base_url,provider.runtime.local_base_url])
        executed=[];card=mount(provider,executed)
        buyer.runtime.import_agent(card,projection_id='use')
        result=buyer.calls.invoke('use',CallRequest(skill='write',task_id='trade',payload='one'))
        _,feedback=buyer.management.command('/v1/feedback/open',{'scope':'use','task_id':'trade','dimensions':{'quality':1,'honoring':4}})
        publication=buyer.experience.publish(feedback['feedback_id'])
        observer.experience.ingest(publication,source=buyer.identity.did,source_complete=True)
        service={'kind':'service','provider_did':provider.identity.did,'service_id':'svc'}
        wait_for(lambda: observer.selection_facts._inputs(service)['external'])
        old=observer.selection_facts._inputs(service)['external'][0]['ref']
        buyer.management.command('/v1/feedback/revise',{'feedback_id':feedback['feedback_id'],'dimensions':{'quality':5,'honoring':4}})
        observer.experience.ingest(buyer.experience.publish(feedback['feedback_id']),source=buyer.identity.did,source_complete=True)
        wait_for(lambda: observer.selection_facts._inputs(service)['external'][0]['revision']==2)
        assert observer.selection_facts._inputs(service)['external'][0]['ref']!=old
        observer.experience.ingest(buyer.experience.publish(feedback['feedback_id'],visibility='PARTIES_ONLY'),
            source=buyer.identity.did,source_complete=True)
        wait_for(lambda: not observer.selection_facts._inputs(service)['external'])
        provider_fact=provider.trade_facts.get(result.metadata['trade_uid'])
        _,seller_feedback=provider.management.command('/v1/feedback/open',{'scope':'svc','task_id':provider_fact['task_id'],
            'dimensions':{'honoring':5,'cooperative':4}})
        subject={'kind':'buyer','buyer_did':buyer.identity.did}
        wait_for(lambda: provider.selection_facts._inputs(subject)['local'])
        assert provider.selection_facts.subject_view(subject,time.time())['credit']['value']>.5
        session=search(observer)
        key=provider.identity.did+'|svc'
        body={'search_id':session.search_id,'result_revision':session.result_revision,'keys':[key],
              'budget':{'remote_operations':8,'received_bytes':65536,'duration_ms':500}}
        headers={'Idempotency-Key':'refresh-once','If-Match':f'"{session.revision}"'}
        assert http(observer,'/v1/selection/refresh',body,headers={'Idempotency-Key':'no-rev'})[0]==428
        code,job=http(observer,'/v1/selection/refresh',body,headers=headers)
        assert code==202,job
        done=wait_for(lambda: (r if (r:=observer.selection_metadata.get(job['job_id']))['state']!='RUNNING' else None))
        assert done['used_operations']<=8 and done['used_bytes']<=65536
        reserved=observer.coordination.get(session.search_id).budget_used
        assert http(observer,'/v1/selection/refresh',body,headers=headers)[1]['job_id']==job['job_id']
        assert observer.coordination.get(session.search_id).budget_used==reserved
        assert http(observer,'/v1/selection/refresh',{**body,'keys':['unseen']},headers=headers)[0]==409
        assert len(executed)==1 and provider.trials.status('svc')['used']==1
        # Preference conflicts are detected without any remote operation.
        profile=observer.selection.profile()
        assert http(observer,'/v1/selection/profiles/balanced',{'values':profile['values']},method='PUT',headers={'If-Match':'"0"'})[0]==200
        assert http(observer,'/v1/selection/profiles/balanced',{'values':profile['values']},method='PUT',headers={'If-Match':'"0"'})[0]==409


def test_projection_failure_and_restart_recover_durable_jobs_without_feedback_dependency(tmp_path):
    path=tmp_path/'node.db'
    protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    store=LocalStore(path,protector=protector)
    reputation=SimpleNamespace(rebuild=lambda *a,**kw: None)
    def adapter(store):
        return SelectionFactAdapter(store,node_did='local',coordination=None,
            feedback=None,experience=SimpleNamespace(latest=lambda subject: []),reputation=reputation,policies=None,now=lambda:100)
    source=adapter(store)
    fact={'trade_uid':'tr','provider_did':'seller','buyer_did':'buyer','service_id':'svc',
          'scope':'use','task_id':'t','execution':'DELIVERED','admitted_at':90,'relation_verified':True}
    store.put('trade_facts','tr',fact)
    source._project=lambda *a: (_ for _ in ()).throw(RuntimeError('interrupted worker'))
    with pytest.raises(RuntimeError):
        source.drain()
    assert store.count('projection_jobs')==3 and store.change_head()>0
    store.close()
    restored=LocalStore(path,protector=protector);source=adapter(restored)
    result=source.drain()
    assert result['processed']==3 and result['pending']==0
    assert len(restored.items('selection_inputs'))==3
    restored.close()


def test_signed_malformed_capability_claims_become_unknown_instead_of_crashing(tmp_path):
    store=LocalStore()
    adapter=SelectionFactAdapter(store,node_did='local',coordination=None,feedback=None,
        experience=SimpleNamespace(latest=lambda subject: []),reputation=None,
        policies=SimpleNamespace(opportunity=lambda s,**kw:{'effective_state':'NORMAL'}))
    item={'key':{'provider_did':'seller','service_id':'svc'},'verification':'CARD_VERIFIED'}
    card={'skills':[{'id':'write','tags':None,'inputModes':5}],'defaultInputModes':None,
          'defaultOutputModes':[{}],'x-a2n':{'points':True,'payments':False,'accepts':None}}
    row=adapter._candidate(item,card,{'skill':'write'})
    assert row['verified'] and row['skills']==['write'] and row['input_modes']==[] and row['methods']==[]
    store.close()


def test_incomplete_refresh_retains_only_previous_qualified_negative_facts_and_withdrawal_removes_them():
    store=LocalStore();now=[1000]
    subject={'kind':'provider','provider_did':'seller'}
    opinions=[{'record':{'trade_uid':str(i),'author_did':str(i),'trade_at':900,'publication_revision':1,
        'service_version':'1','dimensions':{'honoring':5,'dispute_handling':1},'direction':'buyer_to_seller',
        'trade_anchor':{'execution':'DELIVERED'}},'eligible':True} for i in range(3)]
    source=SelectionFactAdapter(store,node_did='observer',coordination=None,feedback=None,
        experience=SimpleNamespace(latest=lambda subject:opinions),reputation=None,policies=None,now=lambda:now[0])
    store.put('experience_queries','complete',{'subject':subject,'created_at':950,'state':'COMPLETED','known_sources_complete':True})
    inputs=source._project(subject,now[0]);inputs['generation']=1
    store.put('selection_inputs',source._key(subject),inputs)
    before=source.subject_view(subject,now[0])['credit']
    assert before['support']['pressures']['handling']['external_applied']
    store.put('experience_queries','failed',{'subject':subject,'created_at':999,'state':'COMPLETED','known_sources_complete':False})
    inputs=source._project(subject,now[0]);inputs['generation']=2
    store.put('selection_inputs',source._key(subject),inputs)
    after=source.subject_view(subject,now[0])['credit']
    assert after['value']==before['value'] and after['status']=='LIMITED'
    assert after['reasons']==['LAST_COMPLETE_EVIDENCE_RETAINED_SOURCE_INCOMPLETE']
    # A verified withdrawal actually removes the contributor; failure alone does not.
    opinions.pop()
    inputs=source._project(subject,now[0]);inputs['generation']=3
    store.put('selection_inputs',source._key(subject),inputs)
    withdrawn=source.subject_view(subject,now[0])['credit']
    assert withdrawn['raw']['handling']==0 and not withdrawn['support']['pressures']['handling']['external_applied']
    now[0]+=31*86400
    inputs=source._project(subject,now[0]);inputs['generation']=4
    assert not inputs['qualified_negative_refs']
    store.close()


def test_omitted_known_sources_never_count_as_complete_and_read_only_policy_does_not_write():
    store=LocalStore();fetched=[]
    def fetch(source,subject,**kwargs):
        fetched.append(source['node_did'])
        return {'source_did':source['node_did'],'bytes':0,'snapshot':'empty','items':[],'next_cursor':''}
    book=SimpleNamespace(confirm_source_page_set=lambda records:None)
    service=ExperienceService(store,book,SimpleNamespace(fetch=fetch),sources=lambda subject:[
        {'node_did':str(i),'endpoint':'http://source-'+str(i)} for i in range(3)])
    try:
        query=service.start({'subject':{'kind':'provider','provider_did':'seller'},'budget':{'max_sources':1}},'only-one')
        done=wait_for(lambda:(r if (r:=service.get(query['query_id']))['state']!='RUNNING' else None))
        assert fetched==['0'] and done['known_source_count']==3 and not done['known_sources_complete']
        assert len(done['missing_sources'])==2
    finally:
        service.close()
    policies=PolicyBook(store,now=lambda:100)
    subject={'kind':'provider','provider_did':'seller'}
    policies.block(subject,blocked=True)
    before=store.items('local_opportunities')
    assert policies.opportunity(subject,persist=False)['effective_state']=='BLOCKED_LOCAL'
    assert store.items('local_opportunities')==before
    store.close()
