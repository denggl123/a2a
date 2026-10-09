import pytest
from a2n_sdk.storage import LocalStore
from a2n_sdk.privacy import redact,sample_projection
from a2n_sdk.selection.demand import interpret
from a2n_sdk.selection.scoring import rank
from a2n_sdk.archives import archive,get_record,page_records
from a2n_sdk.assets import AssetBook,valid_ref
from a2n_sdk.task_quality.rubrics import BUILTINS,validate_checks,evaluate_checks

def test_word_counts_are_public_but_real_credentials_are_hidden():
    value={'tokens':20,'token_rule':'中文单字，英文单词','keywords':[{'token':'文档','count':3}],
           'access_token':'private-secret','nested':{'token':'private-secret'},'prompt_tokens':12}
    for project in (lambda x,r:redact(x,r),lambda x,r:sample_projection(x,r)[0]):
        removed=[];result=project(value,removed)
        assert result['tokens']==20 and result['keywords'][0]['token']=='文档'
        assert result['access_token']=='[已隐去]' and result['nested']['token']=='[已隐去]'
        assert 'tokens' not in removed and 'token_rule' not in removed
    assert redact({'token':'a'*32,'count':1},[])['token']=='[已隐去]'

def test_demand_hints_do_not_perform_discovery_and_matching_uses_description():
    hints=interpret({'text':'提取中文文档，输出 JSON','known_skills':['extract','inspect']})
    assert hints['suggested_skills']==['extract'] and hints['task']['language']=='zh-CN'
    base={'provider_did':'p','service_id':'s','version':'1','skills':['extract'],'key':'p|s'}
    candidates=[{**base,'description':'JSON 文档提取'},{**base,'key':'p|other','service_id':'other','description':'图片处理'}]
    result=rank(candidates,task={'keywords':['JSON','文档']})
    assert result['items'][0]['service_id']=='s'
    with pytest.raises(ValueError):rank(candidates,task={'keywords':['x']*25})

def test_cold_records_remain_readable_and_waiting_work_is_retained():
    store=LocalStore()
    for i in range(5):store.put('task_review_plans',str(i),{'created_at':i,'state':'WAITING' if i==0 else 'REVIEWED'})
    result=archive(store,'task_review_plans',keep=2,eligible=lambda r:r['state']!='WAITING')
    assert result['moved']==3 and store.get('task_review_plans','0')
    assert get_record(store,'task_review_plans','1')['created_at']==1
    assert page_records(store,'task_review_plans',{'archived':'true','limit':'2'})['total']==5
    assert page_records(store,'task_review_plans',{'archived':'false'})['total']==2

def test_asset_capacity_is_configurable_with_owner_revision():
    store=LocalStore();book=AssetBook(store,node_did='owner')
    limits=book.configure({'file_bytes':64*1024**2,'total_bytes':1024**3,'count':512,'expected_revision':0})
    assert limits['revision']==1
    with pytest.raises(ValueError,match='REV_CONFLICT'):book.configure({'file_bytes':64*1024**2,'total_bytes':1024**3,'count':512,'expected_revision':0})

def test_independent_reviewer_is_optional_and_unknown_is_not_failure():
    spec={'kind':'python-function','reviewer_digest':'a'*64,'function':'add','cases':[{'args':[1,2],'expected':3}]}
    checks={'requirements':[{'path':['code'],'op':'reviewer','expected':spec}]}
    validate_checks(checks,BUILTINS[2]);objective,reasons=evaluate_checks({'code':'def add(a,b): return a+b'},checks)
    assert objective['requirements']['value'] is None and reasons
    class Reviewer:
        def evaluate(self,value,spec):return {'score':.5,'state':'REVIEWED','source':'INDEPENDENT'}
    objective,reasons=evaluate_checks({'code':'def add(a,b): return a+b'},checks,Reviewer())
    assert objective['requirements']['value']==.5 and not reasons
