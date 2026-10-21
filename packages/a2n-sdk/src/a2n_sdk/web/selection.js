/* Local assessment UI. Only the explicit refresh button requests remote metadata. */
function selectionEvidence(item){return window.a2nSelection?.summary(item)||I18N.t('sel.no_evidence')}
(() => {
  'use strict';
  const find=document.getElementById('find');if(!find)return;
  // 本文件不留展示文字：维度/状态/原因只留合法键名清单，文案走 I18N（sel.*），查不到回显原值。
  const DIMS=['fit','quality','credit','reliability','time','network','cost','personal'];
  const dimLabel=k=>I18N.text('sel.dim.'+k)||k, stateLabel=k=>I18N.text('sel.state.'+k)||k;
  const reasonLabel=k=>I18N.text('sel.reason.'+k)||k;
  let profile=null, current=null, rows=new Map(), order=new Map(), version='', generation=0, refreshJob=null, demandTask={}, selected=null;
  const panel=document.createElement('section');panel.className='panel';panel.id='selectionPanel';
  const style=document.createElement('style');
  style.textContent='#selectionPanel .selection-inline{display:flex;align-items:center;gap:8px}#selectionPanel input[type="checkbox"]{width:auto;margin:0;flex:none}#selectionWeights{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}#selectionWeights label{display:flex;align-items:center;justify-content:space-between;margin:0;gap:8px}';
  document.head.append(style);
  panel.innerHTML=`<h2>${I18N.t('sel.heading')}</h2><p class="sub">${I18N.t('sel.intro')}</p>
    <label>${I18N.t('sel.demand_label')} <textarea id="selectionDemand" maxlength="2000" placeholder="${I18N.t('sel.demand_placeholder')}"></textarea></label><button class="ghost" id="selectionInterpret">${I18N.t('sel.interpret_btn')}</button><p id="selectionDemandHints" class="sub"></p>
    <details><summary>${I18N.t('sel.preferences_summary')}</summary><div class="facts" id="selectionWeights"></div>
      <label>${I18N.t('sel.task_class')} <input id="selectionTaskClass" list="selectionClasses" maxlength="96" placeholder="${I18N.t('sel.task_class_placeholder')}"></label><datalist id="selectionClasses"></datalist>
      <label>${I18N.t('sel.workload')} <select id="selectionWorkload"><option value="">${I18N.t('sel.workload.unknown')}</option><option value="small">${I18N.t('sel.workload.small')}</option><option value="medium">${I18N.t('sel.workload.medium')}</option><option value="large">${I18N.t('sel.workload.large')}</option></select></label>
      <label>${I18N.t('sel.output')} <input id="selectionOutput" placeholder="${I18N.t('sel.output_placeholder')}"></label>
      <label>${I18N.t('sel.language')} <input id="selectionLanguage" placeholder="${I18N.t('sel.language_placeholder')}"></label>
      <label>${I18N.t('sel.min_quality')} <input id="selectionMinQuality" type="number" min="0" max="100" placeholder="${I18N.t('sel.min_placeholder')}"></label>
      <label>${I18N.t('sel.min_credit')} <input id="selectionMinCredit" type="number" min="0" max="100" placeholder="${I18N.t('sel.min_placeholder')}"></label>
      <label class="selection-inline"><input id="selectionSupported" type="checkbox">${I18N.t('sel.require_supported')}</label>
      <p class="sub">${I18N.t('sel.budget_note')}</p>
      <label>${I18N.t('sel.currency')} <input id="selectionCurrency" maxlength="256" placeholder="${I18N.t('sel.currency_placeholder')}"></label>
      <label>${I18N.t('sel.comfortable')} <input id="selectionComfortable" inputmode="numeric" placeholder="${I18N.t('sel.amount_placeholder')}"></label>
      <label>${I18N.t('sel.maximum')} <input id="selectionMaximum" inputmode="numeric" placeholder="${I18N.t('sel.amount_placeholder')}"></label>
      <button id="selectionSave">${I18N.t('sel.save_btn')}</button>
      <button class="ghost" id="selectionLearn">${I18N.t('sel.learn_btn')}</button><button class="ghost" id="selectionLearnApply" hidden>${I18N.t('sel.learn_apply_btn')}</button><p id="selectionLearnState" class="sub">${I18N.t('sel.learn_note')}</p>
    </details>
    <div class="actions"><button class="ghost" id="selectionRank">${I18N.t('sel.rank_btn')}</button><button class="ghost" id="selectionRefresh">${I18N.t('sel.refresh_btn')}</button><button class="ghost" id="selectionCancel" hidden>${I18N.t('sel.cancel_btn')}</button><button class="ghost" id="selectionNetworkReset">${I18N.t('sel.network_reset_btn')}</button></div>
    <label class="selection-inline"><input type="checkbox" id="selectionShowExcluded">${I18N.t('sel.show_excluded')}</label>
    <p id="selectionStatus" class="sub" role="status">${I18N.t('sel.status_initial')}</p><p id="selectionRefreshStatus" class="sub"></p><details><summary>${I18N.t('sel.outcome_summary')}</summary><button class="ghost" id="selectionOutcomeRefresh">${I18N.t('sel.outcome_refresh_btn')}</button><p id="selectionOutcomes" class="sub">${I18N.t('sel.outcome_note')}</p></details>`;
  const tools=document.querySelector('.disc-tools');tools?.insertAdjacentElement('afterend',panel);
  const el=id=>document.getElementById(id);
  const sort=el('discSort');
  for(const [value,label] of [['match','sel.sort.match'],['quality','sel.sort.quality'],['credit','sel.sort.credit'],['time','sel.sort.time'],['network','sel.sort.network']]){
    const option=document.createElement('option');option.value=value;option.textContent=I18N.t(label);sort.append(option);
  }
  function key(item){return item.key?item.key.provider_did+'|'+item.key.service_id:cardKey(item)}
  function value(m){return m?.value==null||['UNKNOWN','STALE','CONFLICT'].includes(m.status)?I18N.t('sel.unknown'):(m.value*100).toFixed(1)}
  function summary(item){const r=rows.get(key(item));if(!r)return I18N.t('sel.no_evidence');
    const stale=current&&Date.now()/1000>=current.valid_until;
    return I18N.t('sel.summary_head',{score:r.score.toFixed(1),state:I18N.t(r.state==='PASS'?'sel.match.pass':r.state==='FAIL'?'sel.match.fail':'sel.match.pending'),stale:stale?I18N.t('sel.stale_suffix'):''})+'<br>'+
      ['quality','credit','time','network'].map(k=>I18N.t('sel.dim_line',{dimension:dimLabel(k),value:value(r.dimensions[k]),state:stateLabel(r.dimensions[k].status)||I18N.t('sel.state_fallback')})).join(' · ');
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
    const dimension=DIMS.includes(disc.sort)?disc.sort:null;
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
      const button=document.createElement('button');button.className='ghost';button.textContent=I18N.t('sel.explain_btn');button.dataset.selectionExplain=String(i);card.querySelector('.actions')?.append(button)});
    else table.forEach((tr,i)=>{const button=document.createElement('button');button.className='ghost';button.textContent=I18N.t('sel.explain_btn_table');button.dataset.selectionExplain=String(i);tr.lastElementChild?.append(button)});
  };
  async function loadProfile(){profile=await request('/v1/selection/profiles/balanced');
    el('selectionWeights').innerHTML=DIMS.map(k=>`<label>${dimLabel(k)} % <input data-selection-weight="${k}" type="number" min="0" max="100" step="1" value="${Math.round(profile.values.weights[k]*100)}" style="width:65px"></label>`).join('');
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
      el('selectionStatus').textContent=I18N.t('sel.status',{total:all.length,pass:counts.PASS??all.filter(r=>r.state==='PASS').length,unknown:all.filter(r=>r.state==='UNKNOWN').length,fail:all.filter(r=>r.state==='FAIL').length,decision:I18N.t(result.decision==='SATISFIED'?'sel.decision.satisfied':'sel.decision.more'),pending:result.coverage.projection_pending||0});
      applyDisc();
    }catch(e){if(ticket===generation)el('selectionStatus').textContent=I18N.t('sel.status_failed',{error:e.message})}
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
    el('selectionDemandHints').textContent=I18N.t('sel.hints',{skills:hints.suggested_skills.join(' / ')||I18N.t('sel.skills_unknown'),notice:hints.notice});
    if(hints.suggested_skills.length===1)el('skill').value=hints.suggested_skills[0];await rankLocal();
  }catch(e){notice(e.message,true)}};
  el('selectionOutcomeRefresh').onclick=async()=>{try{const r=await request('/v1/selection/outcomes');
    const none=I18N.t('sel.none');
    el('selectionOutcomes').textContent=I18N.t('sel.outcomes',{total:r.total,top3:r.selected_top3,delivered:r.delivered,reused:r.reused_services,rated:r.rated,human:r.human_rated,assistant:r.assistant_rated,usefulness:r.usefulness_mean==null?none:r.usefulness_mean.toFixed(2)+' / 5',assistant_usefulness:r.assistant_usefulness_mean==null?none:r.assistant_usefulness_mean.toFixed(2)+' / 5',notice:r.notice});
  }catch(e){notice(e.message,true)}};
  request('/v1/task-quality/plans?archived=true').then(r=>{const classes=[...new Set(r.items.map(x=>x.plan.context.task_class))];el('selectionClasses').innerHTML=classes.map(x=>`<option value="${esc(x)}"></option>`).join('')}).catch(()=>{});
  el('selectionNetworkReset').onclick=async()=>{try{await request('/v1/selection/network-context/reset',{});await rankLocal();notice(I18N.t('sel.network_reset_done'))}catch(e){notice(e.message,true)}};
  el('selectionSave').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!profile)await loadProfile();
      const weights=Object.fromEntries([...panel.querySelectorAll('[data-selection-weight]')].map(input=>[input.dataset.selectionWeight,Number(input.value)/100]));
      if(Math.abs(Object.values(weights).reduce((a,b)=>a+b,0)-1)>1e-9)throw Error(I18N.t('sel.weights_total'));
      const required={...profile.values.required};for(const [id,k] of [['selectionMinQuality','min_quality'],['selectionMinCredit','min_credit']]){
        if(el(id).value==='')delete required[k];else required[k]=Number(el(id).value)/100;
      }required.require_supported=el('selectionSupported').checked;
      const currency=el('selectionCurrency').value.trim(),budgets=currency?{[currency]:{comfortable:el('selectionComfortable').value,maximum:el('selectionMaximum').value}}:{};
      profile=await request('/v1/selection/profiles/balanced',{values:{...profile.values,weights,required,budgets},expected_revision:profile.revision});
      await rankLocal();notice(I18N.t('sel.profile_saved'));
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  let learningCandidate=null;
  el('selectionLearn').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!profile)await loadProfile();
      learningCandidate=await request('/v1/selection/learning/fit',{profile_id:profile.profile_id});
      const support=learningCandidate.support;
      el('selectionLearnState').textContent=I18N.t('sel.learn_support',{human:support.human_samples,providers:support.providers,days:support.days})+
        (learningCandidate.state==='READY'?I18N.t('sel.learn_ready'):I18N.t('sel.learn_unavailable'));
      el('selectionLearnApply').hidden=learningCandidate.state!=='READY';
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  el('selectionLearnApply').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      await request('/v1/selection/learning/apply',{candidate_id:learningCandidate.candidate_id,expected_revision:learningCandidate.base_revision});
      await loadProfile();await rankLocal();button.hidden=true;
      el('selectionLearnState').textContent=I18N.t('sel.learn_applied');
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  };
  el('selectionRefresh').onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try{
      if(!current)await rankLocal();const first=current?.items.find(r=>r.state!=='FAIL');if(!first)throw Error(I18N.t('sel.refresh_needs_candidate'));
      const session=await request('/v1/coord/searches/'+coordSession.search_id),remaining=session.budget_remaining;
      const budget={remote_operations:Math.min(12,remaining.remote_operations),received_bytes:Math.min(262144,remaining.received_bytes),duration_ms:Math.min(1500,remaining.duration_ms)};
      const result=await request('/v1/selection/refresh',{search_id:session.search_id,result_revision:current.result_revision,keys:[first.key],budget},
        {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${session.revision}"`});
      refreshJob=result.job_id;el('selectionCancel').hidden=false;
      let job=result;while(job.state==='RUNNING'){
        el('selectionRefreshStatus').textContent=I18N.t('sel.refresh_progress',{ops:job.used_operations,bytes:job.used_bytes});
        await new Promise(resolve=>setTimeout(resolve,350));job=await request('/v1/selection/refresh/'+result.job_id);
      }
      el('selectionRefreshStatus').textContent=I18N.t('sel.refresh_done',{state:job.state==='CANCELLED'?I18N.t('sel.refresh.cancelled'):I18N.t('sel.refresh.finished'),coverage:job.known_sources_complete?I18N.t('sel.refresh.complete'):I18N.t('sel.refresh.incomplete'),ops:job.used_operations});
      await rankLocal();
    }catch(err){notice(err.message,true)}finally{button.disabled=false;refreshJob=null;el('selectionCancel').hidden=true}
  };
  el('selectionCancel').onclick=async()=>{if(refreshJob)try{await request('/v1/selection/refresh/'+refreshJob+'/cancel',{})}catch(e){notice(e.message,true)}};
  document.addEventListener('click',async e=>{
    const button=e.target.closest('[data-selection-explain]');if(!button)return;
    const item=found[Number(button.dataset.selectionExplain)],r=rows.get(key(item));if(!r||!current){notice(I18N.t('sel.explain_requires_rank'));return}
    button.disabled=true;
    try{
      const explanation=await request(`/v1/selection/snapshots/${current.snapshot_id}/explain/${encodeURIComponent(r.key)}`);
      show(I18N.t('sel.explain_title'),I18N.t('sel.explain_head',{score:r.score.toFixed(1),time:new Date(explanation.as_of*1000).toLocaleString(),state:I18N.t(r.state==='PASS'?'sel.match.pass':r.state==='FAIL'?'sel.match.fail':'sel.match.pending')})+
        Object.entries(r.dimensions).map(([k,m])=>I18N.t('sel.explain_dim',{dimension:dimLabel(k),value:value(m),state:stateLabel(m.status)||m.status,weight:Math.round(explanation.weights[k]*100),contribution:r.contributions[k].toFixed(2),reasons:m.reasons.map(s=>reasonLabel(s)).join(I18N.t('sel.reason_join'))})).join('\n\n'));
      el('detailExtra').innerHTML=`<details><summary>${I18N.t('sel.raw_summary')}</summary><pre>${esc(JSON.stringify({checks:r.checks,dimensions:r.dimensions},null,2))}</pre></details><p class="hint">${I18N.t('sel.explain_hint')}</p>`;
    }catch(err){notice(err.message,true)}finally{button.disabled=false}
  });
  loadProfile().then(()=>rankLocal()).catch(e=>notice(e.message,true));
  // All automatic updates are local reads. Remote metadata always needs the button.
  setInterval(()=>{if(!document.hidden&&coordSession?.state!=='RUNNING')rankLocal()},15000);
})();
