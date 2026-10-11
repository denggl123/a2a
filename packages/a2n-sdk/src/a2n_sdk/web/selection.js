/* Local assessment UI. Only the explicit refresh button requests remote metadata. */
function selectionEvidence(item){return window.a2nSelection?.summary(item)||'暂无本机评估资料'}
(() => {
  'use strict';
  const find=document.getElementById('find');if(!find)return;
  const labels={fit:'需求匹配',quality:'交付质量',credit:'信用',reliability:'完成可靠性',time:'交互时间',network:'网络',cost:'成本',personal:'个人偏好'};
  const states={SUPPORTED:'资料充分',LIMITED:'资料有限',UNKNOWN:'暂无记录',STALE:'资料过期',CONFLICT:'资料有冲突'};
  const reasons={LAST_COMPLETE_EVIDENCE_RETAINED_SOURCE_INCOMPLETE:'来源暂未查全，保留上次核验的负面记录',
    SERVICE_LEVEL_QUALITY_TASK_SCOPE_UNCONFIRMED:'历史质量只适用于商品版本，尚未区分这次任务类型',
    CHOOSE_WORKLOAD_FOR_COMPARABLE_TIMING:'不同输入规模的时间不可直接比较，请选择本次规模',
    RUBRIC_SPECIFIC_QUALITY_UNKNOWN:'暂无这套质量口径的评分记录，保留未知',
    TASK_SPECIFIC_PRIVATE_REVIEW:'使用本人对同类任务的实际评审记录',
    TASK_SPECIFIC_REVIEW_UNKNOWN:'暂无本人对该任务类型的评审记录',
    COST_OR_ACCEPTANCE_UNKNOWN:'缺少同一币种或发行方的预算与费用条件',
    DECLARED_CAPABILITY_MATCH:'按公开能力声明匹配',PERSONAL_BUDGET_UTILITY:'按自己的预算评估成本'};
  let profile=null, current=null, rows=new Map(), order=new Map(), version='', generation=0, refreshJob=null, demandTask={}, selected=null;
  const panel=document.createElement('section');panel.className='panel';panel.id='selectionPanel';
  const style=document.createElement('style');
  style.textContent='#selectionPanel .selection-inline{display:flex;align-items:center;gap:8px}#selectionPanel input[type="checkbox"]{width:auto;margin:0;flex:none}#selectionWeights{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}#selectionWeights label{display:flex;align-items:center;justify-content:space-between;margin:0;gap:8px}';
  document.head.append(style);
  panel.innerHTML=`<h2>按我的需求推荐</h2><p class="sub">默认重质量和诚信，兼顾速度与成本。评估只读取本机资料，不调用 Agent，不消耗免费样品。没有记录会显示未知。</p>
    <label>告诉我你要做什么 <textarea id="selectionDemand" maxlength="2000" placeholder="例如：提取中文文档中的数据，输出 JSON"></textarea></label><button class="ghost" id="selectionInterpret">整理需求条件</button><p id="selectionDemandHints" class="sub"></p>
    <details><summary>调整偏好与要求</summary><div class="facts" id="selectionWeights"></div>
      <label>任务类别 <input id="selectionTaskClass" list="selectionClasses" maxlength="96" placeholder="可选，例如 project-document-inventory-assistant"></label><datalist id="selectionClasses"></datalist>
      <label>输入规模 <select id="selectionWorkload"><option value="">尚不确定</option><option value="small">小（约 4 KiB 内）</option><option value="medium">中（约 64 KiB 内）</option><option value="large">大</option></select></label>
      <label>输出格式 <input id="selectionOutput" placeholder="可选，如 application/json"></label>
      <label>输出语言 <input id="selectionLanguage" placeholder="可选，如 zh-CN"></label>
      <label>最低质量（0–100） <input id="selectionMinQuality" type="number" min="0" max="100" placeholder="不设"></label>
      <label>最低信用（0–100） <input id="selectionMinCredit" type="number" min="0" max="100" placeholder="不设"></label>
      <label class="selection-inline"><input id="selectionSupported" type="checkbox">最低分要求资料充分；未知资料放入待确认</label>
      <p class="sub">预算使用币种的最小单位；积分填写 points:发行方完整 DID。不同发行方的积分不混算。声明费用仍需在调用前确认。</p>
      <label>预算币种 / 积分发行方 <input id="selectionCurrency" maxlength="256" placeholder="如 CNY 或 points:did:..."></label>
      <label>理想每次费用 <input id="selectionComfortable" inputmode="numeric" placeholder="整数最小单位"></label>
      <label>最高每次费用 <input id="selectionMaximum" inputmode="numeric" placeholder="整数最小单位"></label>
      <button id="selectionSave">保存偏好并重新推荐</button>
      <button class="ghost" id="selectionLearn">从本人评价学习偏好</button><button class="ghost" id="selectionLearnApply" hidden>采用这次偏好建议</button><p id="selectionLearnState" class="sub">学习只读取本人授权的实际使用评价；资料不足时保持当前偏好。</p>
    </details>
    <div class="actions"><button class="ghost" id="selectionRank">按本机资料重新推荐</button><button class="ghost" id="selectionRefresh">为首个候选补充公开体验</button><button class="ghost" id="selectionCancel" hidden>停止补资料</button><button class="ghost" id="selectionNetworkReset">网络环境变更，清除旧观测</button></div>
    <label class="selection-inline"><input type="checkbox" id="selectionShowExcluded">显示不满足要求的候选</label>
    <p id="selectionStatus" class="sub" role="status">发现候选后会生成本机推荐。</p><p id="selectionRefreshStatus" class="sub"></p><details><summary>我的选用效果</summary><button class="ghost" id="selectionOutcomeRefresh">更新实际使用统计</button><p id="selectionOutcomes" class="sub">明确选择后关联真实调用，才计入使用效果。</p></details>`;
  const tools=document.querySelector('.disc-tools');tools?.insertAdjacentElement('afterend',panel);
  const el=id=>document.getElementById(id);
  const sort=el('discSort');
  for(const [value,label] of [['match','适合我的需求'],['quality','交付质量'],['credit','信用'],['time','交互时间'],['network','网络']]){
    const option=document.createElement('option');option.value=value;option.textContent=label;sort.append(option);
  }
  function key(item){return item.key?item.key.provider_did+'|'+item.key.service_id:cardKey(item)}
  function value(m){return m?.value==null||['UNKNOWN','STALE','CONFLICT'].includes(m.status)?'未知':(m.value*100).toFixed(1)}
  function summary(item){const r=rows.get(key(item));if(!r)return '暂无本机评估资料';
    const stale=current&&Date.now()/1000>=current.valid_until;
    return `适合度 ${r.score.toFixed(1)} / 100 · ${r.state==='PASS'?'满足已知要求':r.state==='FAIL'?'不满足要求':'要求待确认'}${stale?' · 资料需更新':''}<br>`+
      ['quality','credit','time','network'].map(k=>`${labels[k]} ${value(r.dimensions[k])}（${states[r.dimensions[k].status]||'资料有限'}）`).join(' · ');
  }
  window.a2nSelection={summary,async confirmChoice(scope,task,card){
    const origin=card?.['x-a2n']?.projection;if(!selected||!origin||origin.node_did+'|'+origin.service_id!==selected.candidate_key)return;
    await request('/v1/selection/choices',{...selected,scope,task_id:task.task_id});
  }};
  document.addEventListener('click',e=>{const button=e.target.closest('[data-add-found]');if(!button||!current)return;
    const item=found[Number(button.dataset.addFound)];if(item)selected={snapshot_id:current.snapshot_id,candidate_key:key(item)};
  },true);
  const previousSorted=discSorted;
  discSorted=function(list){
    let items=previousSorted(list);if(!current)return items;
    if(!el('selectionShowExcluded').checked)items=items.filter(it=>rows.get(key(it))?.state!=='FAIL');
    const dimension=labels[disc.sort]?disc.sort:null;
    if(disc.sort==='default'||disc.sort==='match')items.sort((a,b)=>(order.get(key(a))??Infinity)-(order.get(key(b))??Infinity));
    else if(dimension)items.sort((a,b)=>{
      const left=rows.get(key(a)),right=rows.get(key(b)),state=r=>r?.state==='PASS'?0:r?.state==='UNKNOWN'?1:2;
      const score=r=>{const m=r?.dimensions[dimension];return m?.value==null||['STALE','CONFLICT'].includes(m.status)?-1:m.value};
      return state(left)-state(right)||score(right)-score(left)||String(key(a)).localeCompare(String(key(b)));
    });return items;
  };
  const previousFound=renderFound;
  renderFound=function(){previousFound();const box=el('results');
    const cards=box.querySelectorAll('article.agent'),table=box.querySelectorAll('tbody tr');
    if(cards.length)cards.forEach((card,i)=>{const text=document.createElement('p');text.className='sub';text.innerHTML=summary(found[i]);card.querySelector('.actions')?.before(text);
      const button=document.createElement('button');button.className='ghost';button.textContent='为什么推荐';button.dataset.selectionExplain=String(i);card.querySelector('.actions')?.append(button)});
    else table.forEach((tr,i)=>{const button=document.createElement('button');button.className='ghost';button.textContent='评分依据';button.dataset.selectionExplain=String(i);tr.lastElementChild?.append(button)});
  };
  async function loadProfile(){profile=await request('/v1/selection/profiles/balanced');
    el('selectionWeights').innerHTML=Object.keys(labels).map(k=>`<label>${labels[k]} % <input data-selection-weight="${k}" type="number" min="0" max="100" step="1" value="${Math.round(profile.values.weights[k]*100)}" style="width:65px"></label>`).join('');
    const required=profile.values.required;el('selectionMinQuality').value=required.min_quality==null?'':required.min_quality*100;
    el('selectionMinCredit').value=required.min_credit==null?'':required.min_credit*100;el('selectionSupported').checked=required.require_supported===true;
    const [currency,budget]=Object.entries(profile.values.budgets)[0]||['',{}];el('selectionCurrency').value=currency;
    el('selectionComfortable').value=budget.comfortable??'';el('selectionMaximum').value=budget.maximum??'';
  }
  async function rankLocal(){
    const session=coordSession;if(!session||session.state==='RUNNING')return;
    const ticket=++generation;
    try{
      const task={...demandTask};for(const [id,k] of [['selectionWorkload','workload_bucket'],['selectionOutput','output_mode'],['selectionLanguage','language'],['selectionRubric','rubric'],['selectionTaskClass','task_class']])if(el(id)?.value)task[k]=el(id).value;
      let result=await request('/v1/selection/rank',{search_id:session.search_id,result_revision:session.result_revision,profile_id:'balanced',task});
      const all=[...result.items];let cursor=result.next_cursor;
      while(cursor){const page=await request(`/v1/selection/snapshots/${result.snapshot_id}?limit=100&cursor=${encodeURIComponent(cursor)}`);all.push(...page.items);cursor=page.next_cursor}
      if(ticket!==generation||coordSession?.search_id!==session.search_id||coordSession?.result_revision!==session.result_revision)return;
      current={...result,items:all};rows=new Map(all.map(r=>[r.key,r]));order=new Map(all.map((r,i)=>[r.key,i]));
      const counts=result.counts||{};
      el('selectionStatus').textContent=`本次适合度推荐 · ${all.length} 个候选 · ${counts.PASS??all.filter(r=>r.state==='PASS').length} 个满足要求 · ${all.filter(r=>r.state==='UNKNOWN').length} 个待确认 · ${all.filter(r=>r.state==='FAIL').length} 个不满足要求。${result.decision==='SATISFIED'?'已达到本次满意条件。':'资料或选择不足，可继续发现。'} 评分不是成功概率，覆盖全网的程度未知。后台待更新 ${result.coverage.projection_pending||0} 项。`;
      applyDisc();
    }catch(e){if(ticket===generation)el('selectionStatus').textContent='暂未更新推荐：'+e.message}
  }
  document.addEventListener('a2n:candidates',e=>{
    const next=e.detail.search_id+'|'+e.detail.result_revision+'|'+e.detail.state;
    if(next===version)return;version=next;rows.clear();order.clear();current=null;
    if(e.detail.state!=='RUNNING')rankLocal();
  });
  el('selectionShowExcluded').onchange=applyDisc;
  el('selectionRank').onclick=rankLocal;
  el('selectionInterpret').onclick=async()=>{try{const hints=await request('/v1/selection/interpret',{text:el('selectionDemand').value,known_skills:browseSkills().slice(0,256)});
    demandTask=hints.task;for(const [id,k] of [['selectionOutput','output_mode'],['selectionLanguage','language']])if(hints.task[k])el(id).value=hints.task[k];
    el('selectionDemandHints').textContent=`建议能力：${hints.suggested_skills.join(' / ')||'尚未识别，请自行确认'}。${hints.notice}`;
    if(hints.suggested_skills.length===1)el('skill').value=hints.suggested_skills[0];await rankLocal();
  }catch(e){notice(e.message,true)}};
  el('selectionOutcomeRefresh').onclick=async()=>{try{const r=await request('/v1/selection/outcomes');
    const human=r.items.filter(x=>x.label_source==='HUMAN').length,assistant=r.items.filter(x=>x.label_source==='ASSISTANT').length;
    el('selectionOutcomes').textContent=`明确选用 ${r.total} 次 · 前三名被选 ${r.selected_top3} 次 · 已交付 ${r.delivered} 次 · 复用 ${r.reused_services} 个服务 · 已评价 ${r.rated} 次（本人 ${r.human_rated} / 助手 ${r.assistant_rated}）。本人平均有用程度 ${r.usefulness_mean==null?'暂无':r.usefulness_mean.toFixed(2)+' / 5'}；助手验收均值 ${r.assistant_usefulness_mean==null?'暂无':r.assistant_usefulness_mean.toFixed(2)+' / 5'}。${r.notice}`;
  }catch(e){notice(e.message,true)}};
  request('/v1/task-quality/plans?archived=true').then(r=>{const classes=[...new Set(r.items.map(x=>x.plan.context.task_class))];el('selectionClasses').innerHTML=classes.map(x=>`<option value="${esc(x)}"></option>`).join('')}).catch(()=>{});
  el('selectionNetworkReset').onclick=async()=>{try{await request('/v1/selection/network-context/reset',{});await rankLocal();notice('此前的网络观测已停用，等待本机获得新观测。')}catch(e){notice(e.message,true)}};
  el('selectionSave').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!profile)await loadProfile();
      const weights=Object.fromEntries([...panel.querySelectorAll('[data-selection-weight]')].map(input=>[input.dataset.selectionWeight,Number(input.value)/100]));
      if(Math.abs(Object.values(weights).reduce((a,b)=>a+b,0)-1)>1e-9)throw Error('八项权重合计需要是 100%');
      const required={...profile.values.required};for(const [id,k] of [['selectionMinQuality','min_quality'],['selectionMinCredit','min_credit']]){
        if(el(id).value==='')delete required[k];else required[k]=Number(el(id).value)/100;
      }required.require_supported=el('selectionSupported').checked;
      const currency=el('selectionCurrency').value.trim(),budgets=currency?{[currency]:{comfortable:el('selectionComfortable').value,maximum:el('selectionMaximum').value}}:{};
      profile=await request('/v1/selection/profiles/balanced',{values:{...profile.values,weights,required,budgets},expected_revision:profile.revision});
      await rankLocal();notice('本机偏好已保存。');
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  let learningCandidate=null;
  el('selectionLearn').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!profile)await loadProfile();
      learningCandidate=await request('/v1/selection/learning/fit',{profile_id:profile.profile_id});
      const support=learningCandidate.support;
      el('selectionLearnState').textContent=`本人授权的真人评价 ${support.human_samples} 条，${support.providers} 个供应方，${support.days} 天。`+
        (learningCandidate.state==='READY'?'按较晚记录检查有改善，可采用建议；每项最多调整五个百分点。':'当前没有可采用的改善建议，保留原偏好。至少需要 40 条、3 个供应方、3 天及较晚记录的改善验证。');
      el('selectionLearnApply').hidden=learningCandidate.state!=='READY';
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  el('selectionLearnApply').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      await request('/v1/selection/learning/apply',{candidate_id:learningCandidate.candidate_id,expected_revision:learningCandidate.base_revision});
      await loadProfile();await rankLocal();button.hidden=true;
      el('selectionLearnState').textContent='已采用本机偏好建议；预算、必须满足的条件与付款规则保持原配置。';
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  el('selectionRefresh').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!current)await rankLocal();const first=current?.items.find(r=>r.state!=='FAIL');if(!first)throw Error('先发现一个满足要求或待确认的候选');
      const session=await request('/v1/coord/searches/'+coordSession.search_id),remaining=session.budget_remaining;
      const budget={remote_operations:Math.min(12,remaining.remote_operations),received_bytes:Math.min(262144,remaining.received_bytes),duration_ms:Math.min(1500,remaining.duration_ms)};
      const result=await request('/v1/selection/refresh',{search_id:session.search_id,result_revision:current.result_revision,keys:[first.key],budget},
        {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${session.revision}"`});
      refreshJob=result.job_id;el('selectionCancel').hidden=false;
      let job=result;while(job.state==='RUNNING'){
        el('selectionRefreshStatus').textContent=`正在补充公开体验 · 元数据请求 ${job.used_operations} 次 · ${job.used_bytes} 字节`;
        await new Promise(resolve=>setTimeout(resolve,350));job=await request('/v1/selection/refresh/'+result.job_id);
      }
      el('selectionRefreshStatus').textContent=`${job.state==='CANCELLED'?'已停止':'本轮资料更新结束'} · 已知来源${job.known_sources_complete?'已查完':'未查全'} · 元数据请求 ${job.used_operations} 次。全网完整性未知。`;
      await rankLocal();
    }catch(err){notice(err.message,true)}finally{button.disabled=false;refreshJob=null;el('selectionCancel').hidden=true}
  };
  el('selectionCancel').onclick=async()=>{if(refreshJob)try{await request('/v1/selection/refresh/'+refreshJob+'/cancel',{})}catch(e){notice(e.message,true)}};
  document.addEventListener('click',async e=>{
    const button=e.target.closest('[data-selection-explain]');if(!button)return;
    const item=found[Number(button.dataset.selectionExplain)],r=rows.get(key(item));if(!r||!current){notice('请先按本机资料重新推荐');return}
    button.disabled=true;
    try{
      const explanation=await request(`/v1/selection/snapshots/${current.snapshot_id}/explain/${encodeURIComponent(r.key)}`);
      show('这次推荐的依据',`适合度 ${r.score.toFixed(1)} / 100\n资料时间 ${new Date(explanation.as_of*1000).toLocaleString()}\n${r.state==='PASS'?'满足已知要求':r.state==='FAIL'?'不满足要求':'要求待确认'}\n`+
        Object.entries(r.dimensions).map(([k,m])=>`${labels[k]}：${value(m)}，${states[m.status]||m.status}；权重 ${Math.round(explanation.weights[k]*100)}%，贡献 ${r.contributions[k].toFixed(2)}\n${m.reasons.map(s=>reasons[s]||s).join('；')}`).join('\n\n'));
      el('detailExtra').innerHTML=`<details><summary>原始单位、支持量和记录指纹</summary><pre>${esc(JSON.stringify({checks:r.checks,dimensions:r.dimensions},null,2))}</pre></details><p class="hint">分数仅用于这次需求。争议数量单独展示，不等于责任认定；入口可达不等于交付成功。</p>`;
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  });
  loadProfile().then(()=>rankLocal()).catch(e=>notice(e.message,true));
  // All automatic updates are local reads. Remote metadata always needs the button.
  setInterval(()=>{if(!document.hidden&&coordSession?.state!=='RUNNING')rankLocal()},15000);
})();
