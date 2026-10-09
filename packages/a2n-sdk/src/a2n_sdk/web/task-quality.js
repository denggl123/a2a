/* Composition UI: optional pre-call plans, private task reviews, explicit calibration. */
(() => {
 'use strict';
 const el=id=>document.getElementById(id),host=el('selectionPanel');if(!host)return;
 const names={accuracy:'结果正确',completeness:'内容完整',format:'格式符合要求',usefulness:'实际有用程度',expression:'表达自然',requirements:'需求覆盖',maintainability:'容易维护'};
 const stateNames={WAITING:'等待实际交付',REVIEWED:'已完成检查',CONTEXT_MISMATCH:'交付与预设任务不一致',CANCELLED:'已取消',DEFAULT:'使用默认参数',ACTIVE:'使用本机校准',INVALIDATED:'原资料变更或过期，已回到默认',VALIDATED:'后续记录验证通过',INSUFFICIENT_DATA:'真实使用记录不足',INSUFFICIENT_VARIATION:'需要更多不同体验',INSUFFICIENT_DIVERSITY:'需要更丰富的验证来源',NO_VALIDATED_IMPROVEMENT:'尚未证明改善，保留默认'};
 const box=document.createElement('details');box.className='panel';box.id='taskQualityPanel';
 box.innerHTML=`<summary>任务评审与实际体验校准</summary><p class="sub">先定检查规则，再调用；交付后补充本人体验。评审留在本机，不代替双方评价或争议处理。</p>
 <label class="selection-inline"><input type="checkbox" id="qaEnable">下一次在付款与调用页询价时，先登记任务评审</label>
 <label>本次调用用途 <select id="qaOrigin"><option value="PRODUCTION">实际使用</option><option value="CONTROLLED">受控测试（不参与真实数据校准）</option></select></label>
 <label>任务类型 <select id="qaRubric"></select></label>
 <details><summary>添加可检查的具体要求</summary><p class="sub">规则只检查你选定的字段或文本。格式正确不代表事实正确；代码功能仍须独立测试或人工确认。</p>
 <label>检查哪方面 <select id="qaDimension"></select></label><label>结果中的字段（留空代表整体，嵌套字段用 a.b）<input id="qaPath" maxlength="256"></label>
 <label>要求 <select id="qaOperation"><option value="exists">字段存在</option><option value="equals">等于指定值</option><option value="contains">包含指定文本</option><option value="type">值的类型</option><option value="range">数字在范围内</option><option value="length">长度在范围内</option></select></label>
 <label>指定值类型 <select id="qaValueKind"><option value="string">文本</option><option value="number">数字</option><option value="boolean">布尔值 true / false</option></select></label>
 <label>指定值 <input id="qaExpected" maxlength="4096"></label><label>检查值的类型 <select id="qaExpectedType"><option value="object">对象</option><option value="array">列表</option><option value="string">文本</option><option value="number">数字</option><option value="integer">整数</option><option value="boolean">布尔值</option><option value="null">空值</option></select></label>
 <label>最小值 <input id="qaMinimum" type="number" min="0" value="0"></label><label>最大值 <input id="qaMaximum" type="number" min="0" value="100"></label>
 <button class="ghost" id="qaAddCheck">加入要求</button> <button class="ghost" id="qaClearChecks">清空本次要求</button><p class="sub" id="qaChecks"></p></details>
 <p id="qaPrepared" class="sub"></p><hr><h3>已有任务评审</h3><label>选择任务 <select id="qaPlan"></select></label><p class="sub" id="qaReviewSummary"></p><div id="qaScores"></div>
 <label class="selection-inline"><input id="qaConsent" type="checkbox">允许用这份实际使用体验做本机校准</label>
 <div class="actions"><button id="qaSaveReview">保存本人细项体验</button><button class="ghost" id="qaWithdraw">停止使用本份评审</button><button class="ghost" id="qaCancelPlan">取消未执行的评审计划</button><button class="ghost" id="qaReload">读取本机记录</button></div>
 <hr><h3>实际使用数据校准</h3><p class="sub">测试记录不计入。至少需要 40 笔合格记录、3 个供应节点和 3 天记录；还要用后续记录验证改善。节点数不代表已证明的独立人数。</p>
 <p id="qaCalibrationStatus" class="sub"></p><div class="actions"><button class="ghost" id="qaFit">试算校准效果</button><button id="qaActivate" disabled>使用已验证的校准</button><button class="ghost" id="qaRollback">恢复该任务的默认参数</button></div><p id="qaMessage" class="sub" role="status"></p>`;
 host.insertAdjacentElement('afterend',box);
 const choice=document.createElement('label');choice.textContent='本次质量口径 ';
 const selector=document.createElement('select');selector.id='selectionRubric';choice.append(selector);host.querySelector('h2').insertAdjacentElement('afterend',choice);
 const style=document.createElement('style');style.textContent='#taskQualityPanel input[type="checkbox"]{width:auto;margin:0 8px 0 0}#qaScores{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}';document.head.append(style);
 let rubrics=[],checks={},plans=[],selected=null,model=null,loadGeneration=0,draftDirty=false;
 const message=text=>{el('qaMessage').textContent=text};
 async function run(button,fn){button.disabled=true;try{await fn()}catch(error){message(error.message)}finally{button.disabled=false}}
 function currentRubric(){return rubrics.find(r=>r.reference===el('qaRubric').value)?.definition}
 function dimensions(){const rubric=currentRubric();checks={};el('qaChecks').textContent='尚未添加客观要求；未检查的细项保持未知。';
  el('qaDimension').innerHTML='';for(const [k,v] of Object.entries(rubric?.dimensions||{}))if(v.sources.objective>0){const o=new Option(names[k]||k,k);el('qaDimension').add(o)}}
 function requirements(){let n=0;for(const v of Object.values(checks))n+=v.length;el('qaChecks').textContent=Object.entries(checks).map(([k,v])=>(names[k]||k)+'：'+v.length+' 条').join('；')||'尚未添加客观要求。';return n}
 el('qaAddCheck').onclick=e=>run(e.currentTarget,async()=>{
  const op=el('qaOperation').value,rule={path:el('qaPath').value.trim()?el('qaPath').value.trim().split('.'):[],op};
  if(op==='type')rule.expected=el('qaExpectedType').value;
  if(['equals','contains'].includes(op)){rule.expected=el('qaExpected').value;
   if(op==='equals'&&el('qaValueKind').value==='number'){if(!rule.expected.trim())throw Error('请填写指定数字');rule.expected=Number(rule.expected);if(!Number.isFinite(rule.expected))throw Error('请输入有效数字')}
   if(op==='equals'&&el('qaValueKind').value==='boolean'){if(!['true','false'].includes(rule.expected))throw Error('布尔值填写 true 或 false');rule.expected=rule.expected==='true'}}
  if(['range','length'].includes(op)){rule.minimum=Number(el('qaMinimum').value);rule.maximum=Number(el('qaMaximum').value)}
  if(requirements()>=64)throw Error('一份任务最多 64 条检查要求');const d=el('qaDimension').value;if(!d)throw Error('本任务没有客观检查项');(checks[d]??=[]).push(rule);requirements();message('要求已加入；已登记的旧任务不会改变。')});
 el('qaClearChecks').onclick=()=>{checks={};requirements()};el('qaRubric').onchange=dimensions;
 const independent=document.createElement('details');independent.innerHTML=`<summary>实际运行与内容评审</summary>
 <p class="sub">先在账户页配置评审器。检查结果与真人体验分别保存；服务不可用时保持未知。</p>
 <label>评审方式<select id="qaIndependentKind"><option value="python-function">运行 Python 函数用例</option><option value="semantic">检查内容是否符合文字要求</option><option value="media-constraints">读取实际成果规格</option></select></label>
 <label>结果字段（代码填 code，媒体填 assets[0]，整体留空）<input id="qaIndependentPath" value="code"></label>
 <label>函数名<input id="qaIndependentFunction" value="normalize_text"></label>
 <label>独立用例（参数与预期结果）<textarea id="qaIndependentCases" maxlength="60000">[{"args":["Ａ  B"],"expected":"A B"}]</textarea></label>
 <label>内容要求<textarea id="qaIndependentRequirements" maxlength="4096" placeholder="你希望成果满足的具体内容要求"></textarea></label>
 <label>成果规格（字段、要求、范围）<textarea id="qaIndependentMedia" maxlength="60000">[{"path":["width"],"op":"range","minimum":300,"maximum":1000}]</textarea></label>
 <button class="ghost" id="qaAddIndependent">加入本次选定方面的要求</button></details>`;
 el('qaPrepared').insertAdjacentElement('beforebegin',independent);
 el('qaAddIndependent').onclick=e=>run(e.currentTarget,async()=>{
  const cfg=(await request('/v1/runtime')).reviewers,kind=el('qaIndependentKind').value;
  if(!cfg?.reviewer_digest||kind==='python-function'&&!cfg.python_image||kind==='semantic'&&!cfg.semantic_url)throw Error('请先在账户页配置并授权对应评审器');
  const spec={kind,reviewer_digest:cfg.reviewer_digest};
  if(kind==='python-function'){spec.function=el('qaIndependentFunction').value.trim();spec.cases=JSON.parse(el('qaIndependentCases').value)}
  if(kind==='semantic')spec.requirements=el('qaIndependentRequirements').value.trim();
  if(kind==='media-constraints')spec.constraints=JSON.parse(el('qaIndependentMedia').value);
  const path=el('qaIndependentPath').value.trim().replace(/\[(\d+)\]/g,'.$1').split('.').filter(Boolean).map(p=>/^\d+$/.test(p)?Number(p):p);
  if(requirements()>=64)throw Error('一份任务最多 64 条检查要求');const dimension=el('qaDimension').value;if(!dimension)throw Error('请先选择客观检查方面');
  (checks[dimension]??=[]).push({path,op:'reviewer',expected:spec});requirements();message('独立检查已加入；预期结果由本节点比较。')
 });
 window.a2nTaskReview={prepare:async(scope,requestBody)=>{
  if(!el('qaEnable').checked)return;
  const result=await request('/v1/task-quality/plans',{scope,task_id:requestBody.task_id,skill:requestBody.skill,rubric:el('qaRubric').value,checks,origin:el('qaOrigin').value});
  el('qaPrepared').textContent='已固定本次评审规则：'+result.plan.task_id+'。实际执行仍在调用页操作。';await loadPlans(result.plan.plan_id)
 }};
 function activeContext(){return selected?.review?.context||selected?.plan?.context}
 async function calibrationStatus(){const c=activeContext();if(!c)return;const data=await request('/v1/calibration/status?'+new URLSearchParams(c));
  el('qaCalibrationStatus').textContent=`本口径有 ${data.available_reviews} 份评审，已同意的实际记录 ${data.consented_production_reviews} 份，测试记录 ${data.controlled_reviews} 份。覆盖 ${data.qualified_providers||0} 个供应节点、${data.qualified_days||0} 天；本人评价 ${data.label_sources?.HUMAN||0} 份，助手代评 ${data.label_sources?.ASSISTANT||0} 份。${stateNames[data.active.state]||data.active.state}。`}
 function draw(){draftDirty=false;selected=plans.find(r=>r.plan.plan_id===el('qaPlan').value)||null;el('qaScores').innerHTML='';
  if(model&&(!activeContext()||Object.keys(model.context).some(k=>model.context[k]!==activeContext()[k])))model=null;el('qaActivate').disabled=model?.state!=='VALIDATED';
  if(!selected){el('qaReviewSummary').textContent='尚无任务评审。可先勾选登记评审，再到调用页选择服务。';return}
  const r=selected.review,def=rubrics.find(x=>x.reference===selected.plan.context.rubric)?.definition;
  el('qaReviewSummary').textContent=(stateNames[selected.plan.state]||selected.plan.state)+(r?` · 当前 ${r.score.value==null?'未知':(r.score.value*100).toFixed(1)} / 100 · 已测权重 ${(r.score.coverage*100).toFixed(0)}% · 第 ${r.revision} 版`:'')+(r?.label_source==='ASSISTANT'?' · 助手受托评审':'')+(selected.plan.planned?' · 规则在调用前登记':' · 回看评审，不能用于校准');
  for(const [k,v] of Object.entries(def?.dimensions||{}))if(v.sources.human>0){const label=document.createElement('label');label.textContent=names[k]||k;const input=document.createElement('select');input.dataset.qaScore=k;input.add(new Option('尚未评价',''));for(let i=1;i<=5;i++)input.add(new Option(String(i)+' / 5',String(i)));input.value=r?.human[k]??'';label.append(input);el('qaScores').append(label)}
  el('qaConsent').checked=!!r?.consent;calibrationStatus().catch(e=>message(e.message))}
 async function loadPlans(prefer){if(draftDirty&&!prefer)return;const generation=++loadGeneration,data=await request('/v1/task-quality/plans?archived=true');if(generation!==loadGeneration||draftDirty&&!prefer)return;
  const choice=prefer||el('qaPlan').value;plans=data.items;el('qaPlan').innerHTML='';for(const p of [...plans].reverse())el('qaPlan').add(new Option(p.plan.task_id+' · '+(stateNames[p.plan.state]||p.plan.state),p.plan.plan_id));if(plans.some(p=>p.plan.plan_id===choice))el('qaPlan').value=choice;draw()}
 el('qaScores').onchange=()=>{draftDirty=true};el('qaConsent').onchange=()=>{draftDirty=true};
 el('qaPlan').onchange=draw;el('qaReload').onclick=e=>run(e.currentTarget,()=>{draftDirty=false;return loadPlans()});
 async function save(withdrawn){if(!selected?.review)throw Error('这份任务尚未实际交付并检查');const scores={};for(const x of box.querySelectorAll('[data-qa-score]'))if(x.value)scores[x.dataset.qaScore]=Number(x.value);
  await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/review',{scores:withdrawn?selected.review.human:scores,consent:withdrawn?false:el('qaConsent').checked,withdrawn,label_source:withdrawn?(selected.review.label_source||'HUMAN'):'HUMAN'},{'If-Match':'"'+selected.review.revision+'"'});draftDirty=false;await loadPlans();el('selectionRank').click();message(withdrawn?'本评审不再参与推荐与校准，历史版本保留。':'本人细项体验已保存。它不自动修改对外评价或信用。')}
 el('qaSaveReview').onclick=e=>run(e.currentTarget,()=>save(false));el('qaWithdraw').onclick=e=>run(e.currentTarget,()=>save(true));
 const recheck=document.createElement('button');recheck.id='qaRecheck';recheck.className='ghost';recheck.textContent='重新检查原成果';el('qaReload').insertAdjacentElement('afterend',recheck);
 recheck.onclick=e=>run(e.currentTarget,async()=>{if(!selected?.review)throw Error('先选择已交付的任务');await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/recheck',{},{'If-Match':'"'+selected.review.revision+'"'});draftDirty=false;await loadPlans();message('已按原要求重新检查；没有再次调用或收费，本人体验保留。')});
 el('qaCancelPlan').onclick=e=>run(e.currentTarget,async()=>{if(!selected||selected.plan.state!=='WAITING')throw Error('只能取消尚未执行的评审计划');await request('/v1/task-quality/plans/'+selected.plan.plan_id+'/cancel',{});await loadPlans()});
 el('qaFit').onclick=e=>run(e.currentTarget,async()=>{const c=activeContext();if(!c)throw Error('先选择一份任务评审');model=await request('/v1/calibration/fit',{context:c},{'Idempotency-Key':'fit_'+crypto.randomUUID()});
  el('qaActivate').disabled=model.state!=='VALIDATED';message((stateNames[model.state]||model.state)+`；合格记录 ${model.support.samples} 笔，${model.support.providers} 个供应节点，${model.support.days} 天。`+(model.validation?` 后续验证误差：默认 ${model.validation.baseline.mae.toFixed(3)}，校准 ${model.validation.calibrated.mae.toFixed(3)}。`:''))});
 async function activate(id){const c=activeContext();if(!c)throw Error('先选择任务类型');const s=await request('/v1/calibration/status?'+new URLSearchParams(c));await request('/v1/calibration/activate',{context:c,model_id:id},{'If-Match':'"'+s.active.revision+'"'});await calibrationStatus();el('selectionRank').click();message(id?'已应用本机验证的任务映射；原始检查值和个人权重保留。':'该任务已恢复默认映射。')}
 el('qaActivate').onclick=e=>run(e.currentTarget,async()=>{if(!model||model.state!=='VALIDATED')throw Error('没有通过验证的校准');await activate(model.model_id)});
 el('qaRollback').onclick=e=>run(e.currentTarget,()=>activate(null));
 selector.onchange=()=>el('selectionRank').click();
 async function load(){const data=await request('/v1/task-quality/rubrics');rubrics=data.items;selector.add(new Option('总体交付质量',''));for(const r of rubrics){selector.add(new Option(r.definition.name+' · '+r.definition.version,r.reference));el('qaRubric').add(new Option(r.definition.name+' · '+r.definition.version,r.reference))}dimensions();await loadPlans()}
 load().catch(e=>message('任务评审尚未就绪：'+e.message));
 setInterval(()=>{if(box.open&&!document.hidden)loadPlans().catch(e=>message(e.message))},5000);
})();
