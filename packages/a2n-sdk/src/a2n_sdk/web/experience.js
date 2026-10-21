/* Public opinions and local shadow reputation. No button here invokes an Agent. */
(() => {
  'use strict';
  const find = document.getElementById('find'), account = document.getElementById('account');
  if (!find || !account) return;
  const queryPanel = document.createElement('section');
  queryPanel.className = 'panel';
  queryPanel.innerHTML = '<h2>'+I18N.t('exp.query_heading')+'</h2><p class="sub">'+I18N.t('exp.query_intro')+'</p>\n    <label for="experienceRole">'+I18N.t('exp.query_role')+'</label><select id="experienceRole"><option value="service">'+I18N.t('exp.role.service')+'</option><option value="provider">'+I18N.t('exp.role.provider')+'</option><option value="buyer">'+I18N.t('exp.role.buyer')+'</option></select>\n    <label for="experienceTarget">'+I18N.t('exp.query_target')+'</label><select id="experienceTarget"></select>\n    <div class="actions"><button id="experienceRead">'+I18N.t('exp.read_btn')+'</button> <button class="ghost" id="experienceRebuild">'+I18N.t('exp.rebuild_btn')+'</button> <button class="ghost" id="experienceRefresh">'+I18N.t('exp.refresh_btn')+'</button></div>\n    <p class="sub" id="experienceCoverage"></p><div id="experienceResult"></div>';
  find.append(queryPanel);
  const publishPanel = document.createElement('section');
  publishPanel.className = 'panel';
  publishPanel.innerHTML = '<h2>'+I18N.t('exp.publish_heading')+'</h2><p class="sub">'+I18N.t('exp.publish_intro')+'</p>\n    <label for="experienceFeedback">'+I18N.t('exp.my_feedback')+'</label><select id="experienceFeedback"></select>\n    <label for="experienceNote">'+I18N.t('exp.note_label')+'</label><textarea id="experienceNote" maxlength="500" placeholder="'+I18N.t('exp.note_placeholder')+'"></textarea>\n    <button id="experiencePublish">'+I18N.t('exp.publish_btn')+'</button> <button class="ghost" id="experienceWithdraw">'+I18N.t('exp.withdraw_btn')+'</button>\n    <p class="sub" id="experiencePublicState"></p>\n    <h3>'+I18N.t('exp.policy_heading')+'</h3><p class="sub">'+I18N.t('exp.policy_intro')+'</p>\n    <label for="experienceMode">'+I18N.t('exp.mode_label')+'</label><select id="experienceMode"><option value="DISABLED">'+I18N.t('exp.mode.DISABLED')+'</option><option value="SHADOW">'+I18N.t('exp.mode.SHADOW')+'</option><option value="SUGGEST">'+I18N.t('exp.mode.SUGGEST')+'</option><option value="ENFORCE_LOCAL">'+I18N.t('exp.mode.ENFORCE_LOCAL')+'</option></select>\n    <button class="ghost" id="experienceSavePolicy">'+I18N.t('exp.save_policy_btn')+'</button><p class="sub" id="experiencePolicyState"></p>';
  account.append(publishPanel);
  const el = id => document.getElementById(id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  let targets = [], snapshot = null, querying = false;
  function target() {
    const item = targets[Number(el('experienceTarget').value)];
    if (!item) throw Error(I18N.t('exp.target_required'));
    return item;
  }
  function subject(item) {
    return item.kind==='buyer'?{kind:'buyer',buyer_did:item.buyer_did}:item.kind==='provider'?
      {kind:'provider',provider_did:item.provider_did}:{kind:'service',provider_did:item.provider_did,service_id:item.service_id};
  }
  async function refreshExperience() {
    snapshot = await request('/v1/runtime');
    const rows = [...(snapshot.projections || []).map(p => ({name:p.name, provider_did:p.provider_did, service_id:p.service_id})),
      ...(snapshot.published || []).map(p => ({name:p.card?.name, provider_did:p.provider_did, service_id:p.service_id}))];
    if (typeof pool !== 'undefined') for (const item of pool) {
      const p = item.card?.['x-a2n']?.projection;
      if (p) rows.push({name:item.card.name, provider_did:p.node_did, service_id:p.service_id, version:item.card.version});
    }
    const trades=await request('/v1/trades');
    const role=el('experienceRole').value;
    let choices=rows;
    if(role==='provider')choices=rows.map(r=>({...r,kind:'provider',name:r.provider_did,service_id:''}));
    if(role==='buyer')choices=(trades.trades||[]).filter(r=>r.relation_verified&&r.buyer_did).map(r=>({kind:'buyer',name:r.buyer_did,buyer_did:r.buyer_did}));
    const previous = targets[Number(el('experienceTarget').value)];
    targets = [...new Map(choices.filter(p => p.buyer_did||p.provider_did&&(role!=='service'||p.service_id)).map(p => [JSON.stringify(subject(p)),p])).values()];
    el('experienceTarget').innerHTML = targets.map((p,i) => `<option value="${i}">${safe(p.name || p.service_id)} · ${safe((p.provider_did||p.buyer_did).slice(-8))}</option>`).join('') || '<option>'+I18N.t('exp.no_target')+'</option>';
    const kept = targets.findIndex(p => JSON.stringify(subject(p))===JSON.stringify(previous?subject(previous):{}));
    if (kept >= 0) el('experienceTarget').value = String(kept);
    const fid = el('experienceFeedback').value;
    el('experienceFeedback').innerHTML = (snapshot.feedback || []).filter(f => f.source === 'self').map(f => `<option value="${safe(f.feedback_id)}">${safe(f.service_id)} · ${safe(f.task_id)} · ${I18N.t('exp.revision_of',{revision:safe(f.revision)})}</option>`).join('') || '<option value="">'+I18N.t('exp.no_feedback')+'</option>';
    if ([...el('experienceFeedback').options].some(o => o.value === fid)) el('experienceFeedback').value = fid;
    const pubs = snapshot.experience?.publications || [];
    el('experiencePublicState').textContent = I18N.t('exp.public_state',{public:pubs.filter(p => p.visibility === 'PUBLIC').length,withdrawn:pubs.filter(p => p.visibility === 'WITHDRAWN').length});
    const policy = snapshot.experience?.policy;
    if (policy) {
      el('experienceMode').value = policy.values.mode;
      el('experiencePolicyState').textContent = I18N.t('exp.policy_state',{revision:policy.revision,fraction:Math.round(policy.values.exploration_fraction * 100)});
    }
  }
  function renderReputation(row) {
    const dimensions = Object.entries(row.dimensions || {});
    el('experienceResult').innerHTML = dimensions.map(([key,v]) => `<div class="agent"><h3>${safe(I18N.text('exp.dim.'+key) || key)}</h3><p>${v.support === 'UNKNOWN' ? I18N.t('exp.support_unknown') : I18N.t('exp.theta',{value:Number(v.theta).toFixed(3)})} · ${I18N.t('exp.effective_mass',{value:Number(v.effective_mass).toFixed(3)})} · ${I18N.t('exp.counterparty',{value:safe(v.counterparty_groups)})} · ${I18N.t('exp.effective_support',{value:Number(v.n_eff).toFixed(2)})}</p>
      <details><summary>${I18N.t('exp.contributions')}</summary><pre>${safe(JSON.stringify(v.contributions,null,2))}</pre></details></div>`).join('');
    const op = row.opportunity;
    if (op) {
      const kept = op.supported_decision ? '<br>' + I18N.t('exp.decision_retained', {until: safe(new Date(op.supported_decision.expires_at*1000).toLocaleString())}) : '';
      const review = op.review_required ? '<br>' + I18N.t('exp.review_required') : '';
      el('experienceResult').insertAdjacentHTML('beforeend', '<p class="hint">'
        + I18N.t('exp.op_mode', {mode: safe(op.mode)}) + ' · '
        + I18N.t('exp.op_state', {state: safe(op.state)}) + ' · '
        + I18N.t('exp.op_effective', {state: safe(op.effective_state)})
        + '<br>' + safe(op.reasons.join('、')) + kept + review + '</p>');
    }
  }
  async function rebuild(item) {
    const s=subject(item);
    const row = await request('/v1/reputation/rebuild', {subject:s, current_version:item.version || ''}, {'Idempotency-Key':crypto.randomUUID()});
    renderReputation(row);
    const view=await request('/v1/selection/subjects?'+new URLSearchParams(s));
    const c=view.credit;
    el('experienceResult').insertAdjacentHTML('beforeend','<p class="hint">'+I18N.t('exp.credit_line',{value:c.value==null?I18N.t('exp.credit_unknown'):(c.value*100).toFixed(1),disputes:c.raw.known_disputes,trades:c.raw.observed_trades})+'</p>');
  }
  async function run(button, action) {
    button.disabled = true;
    try {await action();} catch(e) {notice(e.message,true);} finally {button.disabled = false;}
  }
  el('experienceRead').onclick = e => run(e.currentTarget, async () => {
    if (querying) return;
    const item = target(); querying = true;
    try {
      const q = await request('/v1/experience/queries', {subject:subject(item)}, {'Idempotency-Key':crypto.randomUUID()});
      let row = q; const until = Date.now() + 15000;
      while (row.state === 'RUNNING' && Date.now() < until) {
        el('experienceCoverage').textContent = I18N.t('exp.coverage_running',{done:row.completed_sources.length,total:row.sources.length,received:row.received_count});
        await new Promise(resolve => setTimeout(resolve,400));
        row = await request('/v1/experience/queries/' + encodeURIComponent(q.query_id));
      }
      el('experienceCoverage').textContent = I18N.t('exp.coverage_done',{done:row.completed_sources.length,total:row.sources.length,known:row.known_sources_complete ? I18N.t('exp.known_complete') : I18N.t('exp.coverage_incomplete')}) + (row.missing_sources || []).map(s => I18N.t('exp.missing',{reason:s.reason})).join('');
      await rebuild(item);
    } finally {querying = false;}
  });
  el('experienceRebuild').onclick = e => run(e.currentTarget, () => rebuild(target()));
  el('experienceRefresh').onclick = e => run(e.currentTarget, refreshExperience);
  el('experienceRole').onchange=()=>refreshExperience().catch(e=>notice(e.message,true));
  for (const [id,visibility] of [['experiencePublish','PUBLIC'],['experienceWithdraw','PARTIES_ONLY']]) el(id).onclick = e => run(e.currentTarget, async () => {
    if (!el('experienceFeedback').value) throw Error(I18N.t('exp.feedback_required'));
    await request('/v1/feedback/publications', {feedback_id:el('experienceFeedback').value, visibility, public_note:visibility === 'PUBLIC' ? el('experienceNote').value : ''});
    notice(visibility === 'PUBLIC' ? I18N.t('exp.published') : I18N.t('exp.withdrawn'));
    await refreshExperience();
  });
  el('experienceSavePolicy').onclick = e => run(e.currentTarget, async () => {
    const current = await request('/v1/policies');
    const response = await fetch('/v1/policies/local-business/1', {method:'PUT', headers:{'Content-Type':'application/json','If-Match':`"${current.policy.revision}"`}, body:JSON.stringify({values:{...current.policy.values,mode:el('experienceMode').value}})});
    const data = await response.json(); if (!response.ok) throw Error(data.error || I18N.t('exp.save_failed'));
    notice(I18N.t('exp.policy_saved')); await refreshExperience();
  });
  refreshExperience().catch(e => notice(e.message,true));
})();
