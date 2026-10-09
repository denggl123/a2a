"""Exercise completion boundaries with actual node gateways and encrypted stores."""
import base64
import copy
import json
import os
import time
import urllib.error
import urllib.request

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.release_signing import ReleasePublisher
from a2n_node.feedback_identity import verifier_for
from a2n_sdk.experience import unsigned
from a2n_sdk.ports import CallRequest
from a2n_sdk.progress import capture, emit


@pytest.fixture(scope='module')
def nodes(tmp_path_factory):
    root=tmp_path_factory.mktemp('completion')
    protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    pair=[Daemon(root/str(i),port=0,protector=protector,beacon=False,
                 coord_allow_networks=['127.0.0.0/8']).start() for i in range(2)]
    try:yield pair
    finally:
        for node in reversed(pair):node.stop()


def test_streaming_measures_received_partial_output_and_replay_does_not_call_twice(nodes):
    provider,buyer=nodes;executions=[]
    def handler(payload):
        executions.append(payload);emit('first real output');time.sleep(.1)
        return {'value':payload['x']+1}
    provider.runtime.mount_callable({'name':'stream','version':'1','skills':[{'id':'run'}]},handler,service_id='stream-real')
    buyer.runtime.import_agent(provider.runtime.project_binding('stream-real'),projection_id='stream-use')
    request=CallRequest(task_id='stream-once',skill='run',payload={'x':3})
    observed=[]
    with capture(lambda delta:observed.append(delta)):
        result=buyer.calls.invoke('stream-use',request)
    assert result.ok and result.result=={'value':4} and observed==['first real output']
    timing=result.metadata['local_stream_observation']
    assert timing['events']==1 and timing['source']=='LOCAL_RECEIVED_PAYLOAD'
    assert buyer.calls.invoke('stream-use',request).ok and len(executions)==1
    assert provider.trials.status('stream-real')['completed']==1


def test_private_preview_requires_owner_and_rejects_executable_content(nodes):
    provider,_=nodes
    ref=provider.assets.upload(5,'text/plain',[b'hello'])
    base=provider.runtime.local_base_url+'/v1/assets/'+ref['asset_id']+'/preview'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as unauth:opener.open(base)
    assert unauth.value.code==401
    request=urllib.request.Request(base,headers={'X-A2N-Local-Token':provider.runtime.management_token,'Range':'bytes=1-3'})
    with opener.open(request) as response:
        assert response.status==206 and response.read()==b'ell'
        assert 'sandbox' in response.headers['Content-Security-Policy']
    html=provider.assets.upload(8,'text/html',[b'<script>'])
    request=urllib.request.Request(provider.runtime.local_base_url+'/v1/assets/'+html['asset_id']+'/preview',headers={'X-A2N-Local-Token':provider.runtime.management_token})
    with pytest.raises(urllib.error.HTTPError) as unsafe:opener.open(request)
    assert unsafe.value.code==400


def test_scheduled_backup_rotates_only_its_own_files_and_preserves_business(nodes):
    provider,_=nodes;service=provider.maintenance
    provider.store.put('samples','maintenance-test',{'immutable':'retain'})
    password='completion-recovery-password'
    manual=provider.backups.create(password)
    policy=service.status()['policy']
    service.configure({'enabled':True,'interval_hours':24,'keep':1,'password':password,'expected_revision':policy['revision']})
    first=service.tick(force=True);second=service.tick(force=True)
    assert first['state']==second['state']=='READY' and first['backup_id']!=second['backup_id']
    from pathlib import Path
    assert Path(manual['path']).exists()
    assert not Path(provider.store.get('backup_retention_history',first['backup_id'])['path']).exists()
    assert provider.store.get('samples','maintenance-test')=={'immutable':'retain'}
    latest=provider.store.get('scheduled_backups',second['backup_id'])
    assert provider.backups.validate(latest['path'],password)['same_identity']
    with pytest.raises(ValueError):provider.backups.validate(latest['path'],'wrong-password-at-least-12')
    policy=service.status()['policy']
    service.configure({'enabled':False,'interval_hours':24,'keep':1,'expected_revision':policy['revision']})


def test_release_publisher_is_stable_separate_and_tamper_is_detected(tmp_path,nodes):
    protector=EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    publisher=ReleasePublisher(tmp_path/'publisher',protector=protector)
    assert publisher.identity.did!=nodes[0].identity.did
    artifact=tmp_path/'A2N.exe';artifact.write_bytes(b'reviewable binary fixture')
    manifest=publisher.sign(artifact,sequence=1)
    assert verifier_for()(manifest['proof'],unsigned(manifest))
    assert ReleasePublisher(tmp_path/'publisher',protector=protector).identity.did==publisher.identity.did
    corrupt=copy.deepcopy(manifest);corrupt['sha256']='0'*64
    assert not verifier_for()(corrupt['proof'],unsigned(corrupt))
    assert len((tmp_path/'publisher'/'publisher.sealed').read_bytes())>32
    large=publisher.sign(artifact,sequence=10**18)
    assert type(large['release_sequence']) is str
    parsed=json.loads(json.dumps(large))
    service=nodes[0].upgrades;service.trust(publisher.identity.did,True)
    assert service.preview(parsed,artifact)['verified']


def test_workflow_history_cannot_replace_a_previously_signed_version(nodes,monkeypatch):
    service=nodes[0].workflows
    monkeypatch.setattr('a2n_node.workflows.image_id',lambda image:image)
    manifest={'package_id':'history-test','version':'1','image':'sha256:'+'a'*64,
              'command':['python','-c','print(1)'],'limits':{'timeout_ms':1000,'memory_mib':64,'gpu':False},
              'permissions':{'execute':'CONTAINER','network':'NONE','host_files':'NONE','gpu':False},
              'card':{'name':'history','version':'1','skills':[{'id':'run'}]}}
    def install(definition,cid):
        signed=service.sign({'manifest':definition});preview=service.preview(signed)
        return service.install({'manifest':signed,'accepted_digest':preview['preview_digest'],
                                'granted_permissions':signed['permissions'],'command_id':cid})
    first=install(manifest,'first-history')
    next_version=copy.deepcopy(manifest);next_version['version']='2';next_version['card']['version']='2'
    install(next_version,'second-history')
    replacement=copy.deepcopy(manifest);replacement['command']=['python','-c','print(2)']
    with pytest.raises(ValueError,match='WORKFLOW_VERSION_IMMUTABLE'):install(replacement,'replacement-history')
    restored=service.rollback({'service_id':first['service_id'],'version_digest':first['preview_digest']})
    assert restored['manifest']['version']=='1'


def test_media_quality_uses_actual_content_instead_of_claimed_metadata(nodes):
    provider,_=nodes
    ref=provider.assets.upload(11,'text/plain',[b'actual text'])
    metadata=provider.assets.media.inspect(ref)
    assert metadata['characters']==11 and metadata['text']=='actual text'
    bad=copy.deepcopy(ref);bad['sha256']='0'*64
    with pytest.raises(ValueError):provider.assets.media.inspect(bad)


def test_slow_preview_cannot_hold_the_payment_and_file_ledger(nodes,monkeypatch):
    import threading
    provider,buyer=nodes;entered=threading.Event();release=threading.Event()
    def inspect(ref):
        entered.set();release.wait(8);return {'text':'safe public document'}
    monkeypatch.setattr(provider.assets.media,'inspect',inspect)
    ref=provider.assets.upload(4,'text/plain',[b'test'])
    provider.runtime.mount_callable({'name':'slow preview','version':'1','skills':[{'id':'file'}]},
                                    lambda _: {'assets':[ref]},service_id='slow-preview')
    buyer.runtime.import_agent(provider.runtime.project_binding('slow-preview'),projection_id='slow-preview-use')
    try:
        result=buyer.calls.invoke('slow-preview-use',CallRequest(task_id='slow-preview-test',payload={}))
        assert result.ok and entered.wait(3)
        began=time.monotonic()
        owned=buyer.assets.fetch(result.metadata['trade_uid'],ref['asset_id'])
        assert owned['sha256']==ref['sha256'] and time.monotonic()-began<2
    finally:release.set()


def test_review_retry_uses_original_artifact_keeps_experience_and_never_recalls_agent(nodes,monkeypatch):
    provider,buyer=nodes;executions=[]
    provider.runtime.mount_callable({'name':'retry review','version':'1','skills':[{'id':'code'}]},
                                    lambda p:executions.append(p) or {'code':'def add(a,b): return a+b'},service_id='retry-review')
    buyer.runtime.import_agent(provider.runtime.project_binding('retry-review'),projection_id='retry-review-use')
    spec={'kind':'python-function','reviewer_digest':'a'*64,'function':'add','cases':[{'args':[1,2],'expected':3}]}
    monkeypatch.setattr(buyer.reviewers,'evaluate',lambda value,spec: {'score':None,'state':'UNAVAILABLE'})
    plan=buyer.task_quality.plan({'scope':'retry-review-use','task_id':'retry-review-task','skill':'code','rubric':'code-output@1','origin':'CONTROLLED',
        'checks':{'requirements':[{'path':['code'],'op':'reviewer','expected':spec}]}})
    pid=plan['plan']['plan_id']
    assert buyer.calls.invoke('retry-review-use',CallRequest(task_id='retry-review-task',skill='code',payload={})).ok
    buyer.task_quality.drain()
    old=buyer.task_quality.review(pid,{'scores':{'usefulness':4},'consent':False,'withdrawn':False,'label_source':'ASSISTANT','expected_revision':0})
    monkeypatch.setattr(buyer.reviewers,'evaluate',lambda value,spec: {'score':1,'state':'REVIEWED'})
    updated=buyer.task_quality.recheck(pid,{'expected_revision':1})
    assert updated['review']['objective']['requirements']['value']==1 and len(executions)==1
    assert updated['review']['human']==old['review']['human'] and updated['review']['label_source']=='ASSISTANT'
    assert updated['review']['label_at']==old['review']['label_at'] and updated['review']['origin']=='CONTROLLED'
    with pytest.raises(ValueError,match='REV_CONFLICT'):buyer.task_quality.recheck(pid,{'expected_revision':1})


def test_a_real_payment_channel_cannot_keep_the_local_test_wallet_label(nodes):
    node=nodes[1];service=node.payment_coordination
    from eth_account import Account
    account=Account.create()
    native={'currency':'TETH','network':'eip155:31337','rpc_url':'http://127.0.0.1:18945','confirmations':1,'allow_http':True}
    service.configure({'config':{'native':[native]},'keystore':Account.encrypt(account.key,'controlled-password',kdf='pbkdf2',iterations=1000),'password':'controlled-password'})
    node.store.put('payment_settings','test_environment',{'kind':'LOCAL_ANVIL','rpc_url':native['rpc_url']})
    mixed={'native':[native],'x402':{'assets':[{'currency':'USD','network':'eip155:1','asset':'0x'+'1'*40,
        'name':'USD','version':'1','rpc_url':'https://example.org/rpc','confirmations':1}]}}
    service.configure({'config':mixed})
    assert service.status()['test_environment'] is None and service.signer.address==account.address
    with pytest.raises(ValueError,match='INVALID_TEST_CHAIN_URL'):service.test_wallet({'rpc_url':123})


def test_preview_failure_is_retried_once_after_a_parser_or_dependency_change(nodes,monkeypatch):
    provider,_=nodes
    ref=provider.assets.upload(5,'text/plain',[b'hello'])
    store=provider.store;key='retry-preview-fixture'
    store.put('asset_preview_jobs',key,{'state':'READY','refs':[ref],'created_at':time.time(),'error_types':['ValueError'],'parser_digest':'old'})
    monkeypatch.setattr(provider.assets.media,'inspect',lambda ref: {'text':'hello'})
    provider.assets.drain_previews(limit=100)
    assert store.get('asset_preview_jobs',key)['error_types']==[]
    assert store.get('asset_previews',key)['document_previews'][0]['text']=='hello'
