/* Public opinions and local shadow reputation. No button here invokes an Agent. */
(() => {
  'use strict';
  const find = document.getElementById('find'), account = document.getElementById('account');
  if (!find || !account) return;
  const queryPanel = document.createElement('section');
  queryPanel.className = 'panel';
  queryPanel.innerHTML = `<h2>交易体验与信誉</h2><p class="sub">向已知来源读取作者签名的公开意见。信誉按维度展示；未知和未评价不代表差评。</p>
    <label for="experienceTarget">选择已发现或导入的 Agent</label><select id="experienceTarget"></select>
    <div class="actions"><button id="experienceRead">查询多方体验</button> <button class="ghost" id="experienceRebuild">按本机记录复算</button> <button class="ghost" id="experienceRefresh">刷新商品清单</button></div>
    <p class="sub" id="experienceCoverage"></p><div id="experienceResult"></div>`;
  find.append(queryPanel);
  const publishPanel = document.createElement('section');
  publishPanel.className = 'panel';
  publishPanel.innerHTML = `<h2>公开我的反馈</h2><p class="sub">私人反馈与公开短评分别保存。公开内容包含交易双方节点身份、商品和评分，原始输入输出不会随评价发布。撤回会发布撤回声明；离线缓存需更新后才能看到。</p>
    <label for="experienceFeedback">我的反馈</label><select id="experienceFeedback"></select>
    <label for="experienceNote">愿意公开的短评（可留空）</label><textarea id="experienceNote" maxlength="500" placeholder="只填写你愿意向其他节点公开的内容"></textarea>
    <button id="experiencePublish">发布公开意见</button> <button class="ghost" id="experienceWithdraw">撤回公开意见</button>
    <p class="sub" id="experiencePublicState"></p>
    <h3>本机机会策略</h3><p class="sub">影子模式只记录建议；本机执行模式须满足支持量与来源覆盖条件。策略不授予调用或资金权限。</p>
    <label for="experienceMode">模式</label><select id="experienceMode"><option value="DISABLED">停用</option><option value="SHADOW">影子观察</option><option value="SUGGEST">建议</option><option value="ENFORCE_LOCAL">本机执行限制</option></select>
    <button class="ghost" id="experienceSavePolicy">保存模式</button><p class="sub" id="experiencePolicyState"></p>`;
  account.append(publishPanel);
  const el = id => document.getElementById(id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  let targets = [], snapshot = null, querying = false;
  function target() {
    const item = targets[Number(el('experienceTarget').value)];
    if (!item) throw Error('先发现或导入一份有节点身份的 Agent');
    return item;
  }
  async function refreshExperience() {
    snapshot = await request('/v1/runtime');
    const rows = [...(snapshot.projections || []).map(p => ({name:p.name, provider_did:p.provider_did, service_id:p.service_id})),
      ...(snapshot.published || []).map(p => ({name:p.card?.name, provider_did:p.provider_did, service_id:p.service_id}))];
    if (typeof pool !== 'undefined') for (const item of pool) {
      const p = item.card?.['x-a2n']?.projection;
      if (p) rows.push({name:item.card.name, provider_did:p.node_did, service_id:p.service_id, version:item.card.version});
    }
    const previous = targets[Number(el('experienceTarget').value)];
    targets = [...new Map(rows.filter(p => p.provider_did && p.service_id).map(p => [JSON.stringify([p.provider_did,p.service_id]),p])).values()];
    el('experienceTarget').innerHTML = targets.map((p,i) => `<option value="${i}">${safe(p.name || p.service_id)} · ${safe(p.provider_did.slice(-8))}</option>`).join('') || '<option>尚无可查询的商品</option>';
    const kept = targets.findIndex(p => p.provider_did === previous?.provider_did && p.service_id === previous?.service_id);
    if (kept >= 0) el('experienceTarget').value = String(kept);
    const fid = el('experienceFeedback').value;
    el('experienceFeedback').innerHTML = (snapshot.feedback || []).filter(f => f.source === 'self').map(f => `<option value="${safe(f.feedback_id)}">${safe(f.service_id)} · ${safe(f.task_id)} · 第 ${safe(f.revision)} 版</option>`).join('') || '<option value="">尚未写反馈</option>';
    if ([...el('experienceFeedback').options].some(o => o.value === fid)) el('experienceFeedback').value = fid;
    const pubs = snapshot.experience?.publications || [];
    el('experiencePublicState').textContent = `当前公开 ${pubs.filter(p => p.visibility === 'PUBLIC').length} 条，已撤回 ${pubs.filter(p => p.visibility === 'WITHDRAWN').length} 条。`;
    const policy = snapshot.experience?.policy;
    if (policy) {
      el('experienceMode').value = policy.values.mode;
      el('experiencePolicyState').textContent = `政策版本 ${policy.revision}，新人展示比例 ${Math.round(policy.values.exploration_fraction * 100)}%。`;
    }
  }
  function renderReputation(row) {
    const dimensions = Object.entries(row.dimensions || {});
    const names = {quality:'交付质量', punctual:'及时性', communication:'沟通', on_spec:'需求明确', cooperative:'协作'};
    el('experienceResult').innerHTML = dimensions.map(([key,v]) => `<div class="agent"><h3>${safe(names[key] || key)}</h3><p>${v.support === 'UNKNOWN' ? '信息未知' : `归一分 ${Number(v.theta).toFixed(3)}`} · 有效权重 ${Number(v.effective_mass).toFixed(3)} · 对手节点 ${safe(v.counterparty_groups)} · 有效支持 ${Number(v.n_eff).toFixed(2)}</p>
      <details><summary>贡献与出处</summary><pre>${safe(JSON.stringify(v.contributions,null,2))}</pre></details></div>`).join('');
    const op = row.opportunity;
    if (op) el('experienceResult').insertAdjacentHTML('beforeend', `<p class="hint">模式 ${safe(op.mode)} · 观察状态 ${safe(op.state)} · 当前生效 ${safe(op.effective_state)}<br>${safe(op.reasons.join('、'))}${op.supported_decision?`<br>已有充分依据的决定保留至 ${safe(new Date(op.supported_decision.expires_at*1000).toLocaleString())}；来源暂不可达不会自动解除。`:''}${op.review_required?'<br>原限制已到期，进入观察；可核对新事实或明确解除。':''}</p>`);
  }
  async function rebuild(item) {
    const row = await request('/v1/reputation/rebuild', {subject:{kind:'service', provider_did:item.provider_did, service_id:item.service_id}, current_version:item.version || ''}, {'Idempotency-Key':crypto.randomUUID()});
    renderReputation(row);
  }
  async function run(button, action) {
    button.disabled = true;
    try {await action();} catch(e) {notice(e.message,true);} finally {button.disabled = false;}
  }
  el('experienceRead').onclick = e => run(e.currentTarget, async () => {
    if (querying) return;
    const item = target(); querying = true;
    try {
      const q = await request('/v1/experience/queries', {subject:{kind:'service',provider_did:item.provider_did,service_id:item.service_id}}, {'Idempotency-Key':crypto.randomUUID()});
      let row = q; const until = Date.now() + 15000;
      while (row.state === 'RUNNING' && Date.now() < until) {
        el('experienceCoverage').textContent = `已完成来源 ${row.completed_sources.length}/${row.sources.length}，收到 ${row.received_count} 条新意见。`;
        await new Promise(resolve => setTimeout(resolve,400));
        row = await request('/v1/experience/queries/' + encodeURIComponent(q.query_id));
      }
      el('experienceCoverage').textContent = `本轮来源完成 ${row.completed_sources.length}/${row.sources.length}；${row.known_sources_complete ? '本轮已知来源已查完' : '来源覆盖不完整'}。全网历史完整性未知。` + (row.missing_sources || []).map(s => ` 缺失：${s.reason}`).join('');
      await rebuild(item);
    } finally {querying = false;}
  });
  el('experienceRebuild').onclick = e => run(e.currentTarget, () => rebuild(target()));
  el('experienceRefresh').onclick = e => run(e.currentTarget, refreshExperience);
  for (const [id,visibility] of [['experiencePublish','PUBLIC'],['experienceWithdraw','PARTIES_ONLY']]) el(id).onclick = e => run(e.currentTarget, async () => {
    if (!el('experienceFeedback').value) throw Error('先为一笔真实交易写反馈');
    await request('/v1/feedback/publications', {feedback_id:el('experienceFeedback').value, visibility, public_note:visibility === 'PUBLIC' ? el('experienceNote').value : ''});
    notice(visibility === 'PUBLIC' ? '已发布作者签名的公开意见。' : '已发布撤回声明，私人原反馈保留。');
    await refreshExperience();
  });
  el('experienceSavePolicy').onclick = e => run(e.currentTarget, async () => {
    const current = await request('/v1/policies');
    const response = await fetch('/v1/policies/local-business/1', {method:'PUT', headers:{'Content-Type':'application/json','If-Match':`"${current.policy.revision}"`}, body:JSON.stringify({values:{...current.policy.values,mode:el('experienceMode').value}})});
    const data = await response.json(); if (!response.ok) throw Error(data.error || '保存失败');
    notice('本机策略模式已保存。'); await refreshExperience();
  });
  refreshExperience().catch(e => notice(e.message,true));
})();
