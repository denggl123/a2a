import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from a2n_sdk.storage import LocalStore
from a2n_sdk.task_quality.contracts import digest
from a2n_sdk.task_quality.rubrics import BUILTINS, combine, evaluate_checks, validate_checks, validate_rubric
from a2n_sdk.task_quality.calibration import CalibrationService, fit, predict
from a2n_sdk.task_quality.service import TaskQualityService


def test_quality_core_has_no_runtime_or_ledger_imports():
    root=Path(__file__).parents[1]/'packages/a2n-sdk/src/a2n_sdk/task_quality'
    allowed={'__future__','hashlib','json','math','re','typing','time','secrets'}
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,ast.Import): assert all(n.name.split('.')[0] in allowed for n in node.names),path
            if isinstance(node,ast.ImportFrom) and not node.level: assert node.module.split('.')[0] in allowed,path


def test_versioned_weights_missing_dimensions_and_independent_sources():
    rubric=validate_rubric(BUILTINS[0]);original=copy.deepcopy(rubric)
    only_format=combine(rubric,{'format':{'value':1}}, {})
    assert only_format['value']==pytest.approx(.6) and only_format['coverage']==.2
    assert only_format['dimensions']['accuracy']['value'] is None
    full=combine(rubric,{k:{'value':1} for k in ('accuracy','completeness','format')},{'accuracy':5,'completeness':5,'usefulness':5})
    assert full['value']==pytest.approx(1) and full['coverage']==pytest.approx(1)
    assert full['objective_prediction']==1 and full['objective_coverage']==1
    mapped=combine(rubric,{'format':{'value':1}}, {'usefulness':4},objective_map=lambda x:.25)
    assert mapped['value']==pytest.approx(.5) and mapped['coverage']==pytest.approx(.4)
    assert mapped['dimensions']['accuracy']['value'] is None
    assert mapped['dimensions']['usefulness']['value']==pytest.approx(.75)
    assert mapped['dimensions']['format']['sources']['objective']==1
    assert mapped['calibrated_objective']==.25
    mixed=combine(rubric,{k:{'value':v} for k,v in [('accuracy',0),('completeness',.5),('format',1)]},
                  {'accuracy':4,'completeness':2,'usefulness':5},objective_map=lambda x:.25)
    assert mixed['value']==pytest.approx(.435) and mixed['coverage']==pytest.approx(1)
    assert rubric==original
    bad=copy.deepcopy(rubric);bad['dimensions']['accuracy']['weight']=.4
    with pytest.raises(ValueError): validate_rubric(bad)
    bad=copy.deepcopy(rubric);bad['calibration_target']='accuracy'
    with pytest.raises(ValueError): validate_rubric(bad)


def test_bounded_checks_no_bool_number_confusion_or_payload_leak():
    rubric=BUILTINS[0]
    checks=validate_checks({'accuracy':[{'path':['ok'],'op':'equals','expected':1}],
        'format':[{'path':[],'op':'type','expected':'object'}],
        'completeness':[{'path':['private'],'op':'exists'}]},rubric)
    result={'ok':True,'private':'sensitive-result'}
    actual,reasons=evaluate_checks(result,checks)
    assert actual['accuracy']['value']==0 and actual['format']['value']==1
    assert 'sensitive-result' not in str(actual) and not reasons
    with pytest.raises(ValueError): validate_checks({'usefulness':[{'path':[],'op':'exists'}]},rubric)
    with pytest.raises(ValueError): validate_checks({'accuracy':[{'path':[-1],'op':'exists'}]},rubric)
    with pytest.raises(ValueError): validate_checks({'accuracy':[{'path':[],'op':'exists'}]*65},rubric)
    assert evaluate_checks('x'*1048577,checks)[1]==['LOCAL_REVIEW_ARTIFACT_TOO_LARGE']


def fixture_rows(count=60):
    # Pure algorithm fixtures; these are never inserted into a node as real user history.
    now=1000000
    return [dict(trade_uid='t'+str(i),provider_did='provider'+str(i%3),origin='PRODUCTION',consent=True,
                 planned=True,known_self=False,withdrawn=False,objective_coverage=1,workload_bucket='small',
                 prediction=(i%5)/4,label=((i%5)/4)*.5,at=now-5*86400+i*7200,
                 label_at=now-5*86400+i*7200+1,revision=1,record_digest='d'+str(i)) for i in range(count)]


def test_temporal_validation_is_monotonic_and_uses_exact_validated_training_model():
    rows=fixture_rows();out=fit(rows,now=1000000)
    assert out['state']=='VALIDATED'
    assert out['validation']['training_samples']==48 and out['validation']['holdout_samples']==12
    assert out['validation']['calibrated']['mse']<out['validation']['baseline']['mse']
    mapped=[predict(out['knots'],i/100) for i in range(101)]
    assert mapped==sorted(mapped) and all(0<=v<=1 for v in mapped)
    assert not out['validation']['refit_on_holdout']
    assert out==fit(list(reversed(rows)),now=1000000)


@pytest.mark.parametrize('field,value',[('origin','CONTROLLED'),('consent',False),('planned',False),('known_self',True),('withdrawn',True),('objective_coverage',.2),('prediction',None)])
def test_unqualified_history_cannot_train_a_production_calibration(field,value):
    rows=[dict(r,**{field:value}) for r in fixture_rows()]
    out=fit(rows,now=1000000)
    assert out['state']=='INSUFFICIENT_DATA' and not out['knots']


def test_provider_and_day_diversity_variation_and_holdout_gates():
    assert fit([dict(r,provider_did='one') for r in fixture_rows()],now=1000000)['state']=='INSUFFICIENT_DATA'
    assert fit([dict(r,at=999000,label_at=999100+i) for i,r in enumerate(fixture_rows())],now=1000000)['state']=='INSUFFICIENT_DATA'
    assert fit([dict(r,label=.75) for r in fixture_rows()],now=1000000)['state']=='INSUFFICIENT_VARIATION'
    assert fit([dict(r,label=r['prediction']) for r in fixture_rows()],now=1000000)['state']=='NO_VALIDATED_IMPROVEMENT'
    rows=fixture_rows();rows += [dict(rows[0],revision=2,label=.125,record_digest='revised')]
    out=fit(rows,now=1000000)
    assert out['support']['samples']==60 and out['references']['t0']=='revised'


def test_delegated_assistant_use_is_labelled_and_never_mixed_with_personal_labels():
    rows=[dict(r,label_source='ASSISTANT') for r in fixture_rows()]
    model=fit(rows,now=1000000)
    assert model['state']=='VALIDATED' and model['support']['label_sources']=={'ASSISTANT':60}
    rows[0]['label_source']='HUMAN'
    model=fit(rows,now=1000000)
    assert model['state']=='INSUFFICIENT_DIVERSITY' and not model['knots']
    assert model['reasons']==['EVALUATION_SOURCES_REQUIRE_SEPARATE_CONTEXTS']


def test_calibration_activation_revision_reversal_and_source_withdrawal():
    store=LocalStore();data={'rows':fixture_rows(),'rubric_digest':'rubric'}
    source=SimpleNamespace(observations=lambda ctx:copy.deepcopy(data))
    c={'rubric':'structured-output@1','task_class':'structured-output','skill':'inspect','workload_bucket':'small'}
    service=CalibrationService(store,source,now=lambda:1000000)
    body={'context':c,'command_id':'fit-one'}
    model=service.create(body)
    assert model['state']=='VALIDATED' and 'references' not in model
    assert service.create(body)==model and service.active(c,1000000)['state']=='DEFAULT'
    active=service.activate({'context':c,'model_id':model['model_id'],'expected_revision':0})
    assert active['state']=='ACTIVE'
    assert service.active(c,model['valid_until'])['state']=='INVALIDATED'
    data['rows'][0]['record_digest']='changed-opinion'
    assert service.active(c,1000000)['state']=='INVALIDATED'
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'): service.create(body)
    data['rows'][0]['record_digest']='d0'
    assert service.active(c,1000000)['state']=='ACTIVE'
    with pytest.raises(ValueError,match='REV_CONFLICT'): service.activate({'context':c,'model_id':None,'expected_revision':0})
    data['rows'][0]['consent']=False
    assert service.active(c,1000000)['state']=='INVALIDATED'
    assert service.activate({'context':c,'model_id':None,'expected_revision':1})['state']=='DEFAULT'
    assert len(store.items('calibration_active_versions'))==2


def test_plan_immutable_private_opinion_revision_and_retrospective_exclusion():
    store=LocalStore();ready=[];now=[100]
    artifacts=SimpleNamespace(describe=lambda s,t:{'subject':{'provider_did':'provider','service_id':'svc'},'already_admitted':False},
                              delivered=lambda s,t:ready[0] if ready else None)
    service=TaskQualityService(store,artifacts,now=lambda:now[0])
    body={'scope':'use','task_id':'task','rubric':'structured-output@1','skill':'inspect',
          'checks':{'format':[{'path':[],'op':'type','expected':'object'}]}}
    p=service.plan(body);pid=p['plan']['plan_id']
    assert service.plan(body)==p
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'): service.plan(dict(body,origin='CONTROLLED'))
    assert service.drain()['completed']==0
    ready.append({'subject':{'provider_did':'provider','service_id':'svc'},'skill':'inspect','workload_bucket':'small',
                  'result':{'private':'secret'},'result_digest':'digest','version':'1','trade_uid':'trade','at':101,'known_self':False})
    now[0]=102
    assert service.drain()['completed']==1 and service.drain()['completed']==0
    before=service.get(pid)['review']
    assert 'secret' not in str(before)
    after=service.review(pid,{'scores':{'usefulness':5},'consent':True,'withdrawn':False,'expected_revision':0})['review']
    assert after['revision']==1 and after['objective']==before['objective']
    with pytest.raises(ValueError,match='REV_CONFLICT'): service.review(pid,{'scores':{},'consent':False,'withdrawn':False,'expected_revision':0})
    task={'rubric':'structured-output@1','skill':'inspect','workload_bucket':'small'}
    assert service.metric('provider','svc','1',task,103)['value'] is not None
    assert service.metric('provider','svc','2',task,103)['value'] is None
    service.review(pid,{'scores':{},'consent':False,'withdrawn':True,'expected_revision':1})
    assert service.metric('provider','svc','1',task,103)['value'] is None
    assert len(store.items('task_review_versions'))==3
