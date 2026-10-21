/* Composition UI: optional pre-call plans, private task reviews, explicit calibration. */
(() => {
 'use strict';
 const el=id=>document.getElementById(id),host=el('selectionPanel');if(!host)return;
 // 本文件不留展示文字：维度与状态只按合法键名取表（qa.dim.* / qa.state.*），查不到如实回显原值。
 const dim=k=>I18N.text('qa.dim.'+k)||k,stateName=k=>I18N.text('qa.state.'+k)||k;
 const box=document.createElement('details');box.className='panel';box.id='taskQualityPanel';
 box.innerHTML=`<summary>${I18N.t('qa.heading')}</summary><p class="sub">${I18N.t('qa.intro')}</p>
 <label class="selection-inline"><input type="checkbox" id="qaEnable">${I18N.t('qa.enable')}</label>
 <label>${I18N.t('qa.origin_label')} <select id="qaOrigin"><option value="PRODUCTION">${I18N.t('qa.origin.PRODUCTION')}</option><option value="CONTROLLED">${I18N.t('qa.origin.CONTROLLED')}</option></select></label>
 <label>${I18N.t('qa.rubric_label')} <select id="qaRubric"></select></label>
 <details><summary>${I18N.t('qa.add_check_summary')}</summary><p class="sub">${I18N.t('qa.add_check_intro')}</p>
 <label>${I18N.t('qa.dimension_label')} <select id="qaDimension"></select></label><label>${I18N.t('qa.path_label')}<input id="qaPath" maxlength="256"></label>
 <label>${I18N.t('qa.operation_label')} <select id="qaOperation"><option value="exists">${I18N.t('qa.op.exists')}</option><option value="equals">${I18N.t('qa.op.equals')}</option><option value="contains">${I18N.t('qa.op.contains')}</option><option value="type">${I18N.t('qa.op.type')}</option><option value="range">${I18N.t('qa.op.range')}</option><option value="length">${I18N.t('qa.op.length')}</option></select></label>
 <label>${I18N.t('qa.value_kind_label')} <select id="qaValueKind"><option value="string">${I18N.t('qa.value_kind.string')}</option><option value="number">${I18N.t('qa.value_kind.number')}</option><option value="boolean">${I18N.t('qa.value_kind.boolean')}</option></select></label>
 <label>${I18N.t('qa.expected_label')} <input id="qaExpected" maxlength="4096"></label><label>${I18N.t('qa.expected_type_label')} <select id="qaExpectedType"><option value="object">${I18N.t('qa.type.object')}</option><option value="array">${I18N.t('qa.type.array')}</option><option value="string">${I18N.t('qa.type.string')}</option><option value="number">${I18N.t('qa.type.number')}</option><option value="integer">${I18N.t('qa.type.integer')}</option><option value="boolean">${I18N.t('qa.type.boolean')}</option><option value="null">${I18N.t('qa.type.null')}</option></select></label>
 <label>${I18N.t('qa.minimum_label')} <input id="qaMinimum" type="number" min="0" value="0"></label><label>${I18N.t('qa.maximum_label')} <input id="qaMaximum" type="number" min="0" value="100"></label>
 <button class="ghost" id="qaAddCheck">${I18N.t('qa.add_check_btn')}</button> <button class="ghost" id="qaClearChecks">${I18N.t('qa.clear_checks_btn')}</button><p class="sub" id="qaChecks"></p></details>
 <p id="qaPrepared" class="sub"></p><hr><h3>${I18N.t('qa.existing_heading')}</h3><label>${I18N.t('qa.plan_label')} <select id="qaPlan"></select></label><p class="sub" id="qaReviewSummary"></p><div id="qaScores"></div>
 <label class="selection-inline"><input id="qaConsent" type="checkbox">${I18N.t('qa.consent')}</label>
 <div class="actions"><button id="qaSaveReview">${I18N.t('qa.save_review_btn')}</button><button class="ghost" id="qaWithdraw">${I18N.t('qa.withdraw_btn')}</button><button class="ghost" id="qaCancelPlan">${I18N.t('qa.cancel_plan_btn')}</button><button class="ghost" id="qaReload">${I18N.t('qa.reload_btn')}</button></div>
 <hr><h3>${I18N.t('qa.calibration_heading')}</h3><p class="sub">${I18N.t('qa.calibration_intro')}</p>
 <p id="qaCalibrationStatus" class="sub"></p><div class="actions"><button class="ghost" id="qaFit">${I18N.t('qa.fit_btn')}</button><button id="qaActivate" disabled>${I18N.t('qa.activate_btn')}</button><button class="ghost" id="qaRollback">${I18N.t('qa.rollback_btn')}</button></div><p id="qaMessage" class="sub" role="status"></p>`;
 host.insertAdjacentElement('afterend',box);
 const choice=document.createElement('label');choice.textContent=I18N.t('qa.rubric_inline');
 const selector=document.createElement('select');selector.id='selectionRubric';choice.append(selector);host.querySelector('h2').insertAdjacentElement('afterend',choice);
 const style=document.createElement('style');style.textContent='#taskQualityPanel input[type="checkbox"]{width:auto;margin:0 8px 0 0}#qaScores{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}';document.head.append(style);
 let rubrics=[],checks={},plans=[],selected=null,model=null,loadGeneration=0,draftDirty=false;
 const message=text=>{el('qaMessage').textContent=text};
 async function run(button,fn){button.disabled=true;try{await fn()}catch(error){message(error.message)}finally{button.disabled=false}}
 function currentRubric(){return rubrics.find(r=>r.reference===el('qaRubric').value)?.definition}
 function dimensions(){const rubric=currentRubric();checks={};el('qaChecks').textContent=I18N.t('qa.checks_empty');
  el('qaDimension').innerHTML='';for(const [k,v] of Object.entries(rubric?.dimensions||{}))if(v.sources.objective>0){const o=new Option(dim(k),k);el('qaDimension').add(o)}}
 function requirements(){let n=0;for(const v of Object.values(checks))n+=v.length;el('qaChecks').textContent=Object.entries(checks).map(([k,v])=>I18N.t('qa.checks_item',{dimension:dim(k),count:v.length})).join(I18N.t('qa.checks_join'))||I18N.t('qa.checks_none');return n}
 el('qaAddCheck').onclick=e=>run(e.currentTarget,async()=>{
  const op=el('qaOperation').value,rule={path:el('qaPath').value.trim()?el('qaPath').value.trim().split('.'):[],op};
  if(op==='type')rule.expected=el('qaExpectedType').value;
  if(['equals','contains'].includes(op)){rule.expected=el('qaExpected').value;
   if(op==='equals'&&el('qaValueKind').value==='number'){if(!rule.expected.trim())throw Error(I18N.t('qa.number_required'));rule.expected=Number(rule.expected);if(!Number.isFinite(rule.expected))throw Error(I18N.t('qa.number_invalid'))}
   if(op==='equals'&&el('qaValueKind').value==='boolean'){if(!['true','false'].includes(rule.expected))throw Error(I18N.t('qa.boolean_required'));rule.expected=rule.expected==='true'}}
  if(['range','length'].includes(op)){rule.minimum=Number(el('qaMinimum').value);rule.maximum=Number(el('qaMaximum').value)}
  if(requirements()>=64)throw Error(I18N.t('qa.check_limit'));const d=el('qaDimension').value;if(!d)throw Error(I18N.t('qa.no_objective_dimension'));(checks[d]??=[]).push(rule);requirements();message(I18N.t('qa.check_added'))});
 el('qaClearChecks').onclick=()=>{checks={};requirements()};el('qaRubric').onchange=dimensions;
 const independent=document.createElement('details');independent.innerHTML=`<summary>${I18N.t('qa.independent_summary')}</summary>
 <p class="sub">${I18N.t('qa.independent_intro')}</p>
 <label>${I18N.t('qa.independent_kind_label')}<select id="qaIndependentKind"><option value="python-function">${I18N.t('qa.kind.python-function')}</option><option value="semantic">${I18N.t('qa.kind.semantic')}</option><option value="media-constraints">${I18N.t('qa.kind.media-constraints')}</option></select></label>
 <label>${I18N.t('qa.independent_path_label')}<input id="qaIndependentPath" value="code"></label>
 <label>${I18N.t('qa.independent_function_label')}<input id="qaIndependentFunction" value="normalize_text"></label>
 <label>${I18N.t('qa.independent_cases_label')}<textarea id="qaIndependentCases" maxlength="60000">[{"args":["Ａ  B"],"expected":"A B"}]</textarea></label>
 <label>${I18N.t('qa.independent_requirements_label')}<textarea id="qaIndependentRequirements" maxlength="4096" placeholder="${I18N.t('qa.independent_requirements_placeholder')}"></textarea></label>
 <label>${I18N.t('qa.independent_media_label')}<textarea id="qaIndependentMedia" maxlength="60000">[{"path":["width"],"op":"range","minimum":300,"maximum":1000}]</textarea></label>
 <button class="ghost" id="qaAddIndependent">${I18N.t('qa.independent_add_btn')}</button></details>`;
 el('qaPrepared').insertAdjacentElement('beforebegin',independent);
 el('qaAddIndependent').onclick=e=>run(e.currentTarget,async()=>{
  const cfg=(await request('/v1/runtime')).reviewers,kind=el('qaIndependentKind').value;
  if(!cfg?.reviewer_digest||kind==='python-function'&&!cfg.python_image||kind==='semantic'&&!cfg.semantic_url)throw Error(I18N.t('qa.reviewer_not_granted'));
  const spec={kind,reviewer_digest:cfg.reviewer_digest};
  if(kind==='python-function'){spec.function=el('qaIndependentFunction').value.trim();spec.cases=JSON.parse(el('qaIndependentCases').value)}
  if(kind==='semantic')spec.requirements=el('qaIndependentRequirements').value.trim();
  if(kind==='media-constraints')spec.constraints=JSON.parse(el('qaIndependentMedia').value);
  const path=el('qaIndependentPath').value.trim().replace(/\[(\d+)\]/g,'.$1').split('.').filter(Boolean).map(p=>/^\d+$/.test(p)?Number(p):p);
  if(requirements()>=64)throw Error(I18N.t('qa.check_limit'));const dimension=el('qaDimension').value;if(!dimension)throw Error(I18N.t('qa.dimension_required'));
  (checks[dimension]??=[]).push({path,op:'reviewer',expected:spec});requirements();message(I18N.t('qa.independent_added'))
 });
 window.a2nTaskReview={prepare:async(scope,requestBody)=>{
  if(!el('qaEnable').checked)return;
  const result=await request('/v1/task-quality/plans',{scope,task_id:requestBody.task_id,skill:requestBody.skill,rubric:el('qaRubric').value,checks,origin:el('qaOrigin').value});
  el('qaPrepared').textContent=I18N.t('qa.prepared',{task:result.plan.task_id});await loadPlans(result.plan.plan_id)
 }};
 function activeContext(){return selected?.review?.context||selected?.plan?.context}
 async function calibrationStatus(){const c=activeContext();if(!c)return;const data=await request('/v1/calibration/status?'+new URLSearchParams(c));
  el('qaCalibrationStatus').textContent=I18N.t('qa.calibration_status',{reviews:data.available_reviews,consented:data.consented_production_reviews,controlled:data.controlled_reviews,providers:data.qualified_providers||0,days:data.qualified_days||0,human:data.label_sources?.HUMAN||0,assistant:data.label_sources?.ASSISTANT||0,state:stateName(data.active.state)})}
 function draw(){draftDirty=false;selected=plans.find(r=>r.plan.plan_id===el('qaPlan').value)||null;el('qaScores').innerHTML='';
  if(model&&(!activeContext()||Object.keys(model.context).some(k=>model.context[k]!==activeContext()[k])))model=null;el('qaActivate').disabled=model?.state!=='VALIDATED';
  if(!selected){el('qaReviewSummary').textContent=I18N.t('qa.no_review');return}
  const r=selected.review,def=rubrics.find(x=>x.reference===selected.plan.context.rubric)?.definition;
  el('qaReviewSummary').textContent=stateName(selected.plan.state)+(r?I18N.t('qa.summary_score',{score:r.score.value==null?I18N.t('qa.score_unknown'):(r.score.value*100).toFixed(1),coverage:(r.score.coverage*100).toFixed(0),revision:r.revision}):'')+(r?.label_source==='ASSISTANT'?I18N.t('qa.summary_assistant'):'')+(selected.plan.planned?I18N.t('qa.summary_planned'):I18N.t('qa.summary_retro'));
  for(const [k,v] of Object.entries(def?.dimensions||{}))if(v.sources.human>0){const label=document.createElement('label');label.textContent=dim(k);const input=document.createElement('select');input.dataset.qaScore=k;input.add(new Option(I18N.t('qa.unrated'),''));for(let i=1;i<=5;i++)input.add(new Option(I18N.t('qa.score_option',{value:String(i)}),String(i)));input.value=r?.human[k]??'';label.append(input);el('qaScores').append(label)}
  el('qaConsent').checked=!!r?.consent;calibrationStatus().catch(e=>message(e.message))}
 async function loadPlans(prefer){if(draftDirty&&!prefer)return;const generation=++loadGeneration,data=await request('/v1/task-quality/plans?archived=true');if(generation!==loadGeneration||draftDirty&&!prefer)return;
  const choice=prefer||el('qaPlan').value;plans=data.items;el('qaPlan').innerHTML='';for(const p of [...plans].reverse())el('qaPlan').add(new Option(p.plan.task_id+' · '+stateName(p.plan.state),p.plan.plan_id));if(plans.some(p=>p.plan.plan_id===choice))el('qaPlan').value=choice;draw()}
 el('qaScores').onchange=()=>{draftDirty=true};el('qaConsent').onchange=()=>{draftDirty=true};
 el('qaPlan').onchange=draw;el('qaReload').onclick=e=>run(e.currentTarget,()=>{draftDirty=false;return loadPlans()});
 async function save(withdrawn){if(!selected?.review)throw Error(I18N.t('qa.not_delivered'));const scores={};for(const x of box.querySelectorAll('[data-qa-score]'))if(x.value)scores[x.dataset.qaScore]=Number(x.value);
  await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/review',{scores:withdrawn?selected.review.human:scores,consent:withdrawn?false:el('qaConsent').checked,withdrawn,label_source:withdrawn?(selected.review.label_source||'HUMAN'):'HUMAN'},{'If-Match':'"'+selected.review.revision+'"'});draftDirty=false;await loadPlans();el('selectionRank').click();message(withdrawn?I18N.t('qa.withdrawn_saved'):I18N.t('qa.review_saved'))}
 el('qaSaveReview').onclick=e=>run(e.currentTarget,()=>save(false));el('qaWithdraw').onclick=e=>run(e.currentTarget,()=>save(true));
 const recheck=document.createElement('button');recheck.id='qaRecheck';recheck.className='ghost';recheck.textContent=I18N.t('qa.recheck_btn');el('qaReload').insertAdjacentElement('afterend',recheck);
 recheck.onclick=e=>run(e.currentTarget,async()=>{if(!selected?.review)throw Error(I18N.t('qa.recheck_requires_task'));await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/recheck',{},{'If-Match':'"'+selected.review.revision+'"'});draftDirty=false;await loadPlans();message(I18N.t('qa.rechecked'))});
 el('qaCancelPlan').onclick=e=>run(e.currentTarget,async()=>{if(!selected||selected.plan.state!=='WAITING')throw Error(I18N.t('qa.cancel_requires_waiting'));await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/cancel',{});await loadPlans()});
 el('qaFit').onclick=e=>run(e.currentTarget,async()=>{const c=activeContext();if(!c)throw Error(I18N.t('qa.plan_required'));model=await request('/v1/calibration/fit',{context:c},{'Idempotency-Key':'fit_'+crypto.randomUUID()});
  el('qaActivate').disabled=model.state!=='VALIDATED';message(I18N.t('qa.fit_result',{state:stateName(model.state),samples:model.support.samples,providers:model.support.providers,days:model.support.days})+(model.validation?I18N.t('qa.fit_validation',{baseline:model.validation.baseline.mae.toFixed(3),calibrated:model.validation.calibrated.mae.toFixed(3)}):''))});
 async function activate(id){const c=activeContext();if(!c)throw Error(I18N.t('qa.rubric_required'));const s=await request('/v1/calibration/status?'+new URLSearchParams(c));await request('/v1/calibration/activate',{context:c,model_id:id},{'If-Match':'"'+s.active.revision+'"'});await calibrationStatus();el('selectionRank').click();message(id?I18N.t('qa.activated'):I18N.t('qa.rollback_done'))}
 el('qaActivate').onclick=e=>run(e.currentTarget,async()=>{if(!model||model.state!=='VALIDATED')throw Error(I18N.t('qa.no_validated_model'));await activate(model.model_id)});
 el('qaRollback').onclick=e=>run(e.currentTarget,()=>activate(null));
 selector.onchange=()=>el('selectionRank').click();
 async function load(){const data=await request('/v1/task-quality/rubrics');rubrics=data.items;selector.add(new Option(I18N.t('qa.general_quality'),''));for(const r of rubrics){selector.add(new Option(r.definition.name+' · '+r.definition.version,r.reference));el('qaRubric').add(new Option(r.definition.name+' · '+r.definition.version,r.reference))}dimensions();await loadPlans()}
 load().catch(e=>message(I18N.t('qa.not_ready',{error:e.message})));
 setInterval(()=>{if(box.open&&!document.hidden)loadPlans().catch(e=>message(e.message))},5000);
})();
