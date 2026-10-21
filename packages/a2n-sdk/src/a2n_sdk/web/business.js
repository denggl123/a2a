/* Owner controls for negotiation, portable recovery and explicit code installation. */
(() => {
  'use strict';
  const account = document.getElementById('account'), sell = document.getElementById('sell');
  if (!account || !sell) return;
  const safe = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const el = id => document.getElementById(id);
  const section = (parent, html) => {const node=document.createElement('section');node.className='panel';node.innerHTML=html;parent.append(node);};
  section(account, `<h2>${I18N.t('biz.resolve.heading')}</h2><p class="sub">${I18N.t('biz.resolve.intro')}</p>
    <label>${I18N.t('biz.resolve.dispute_label')}<select id="resolutionDispute"></select></label><button class="ghost" id="resolutionRead">${I18N.t('biz.resolve.refresh')}</button>
    <div id="resolutionMessages"></div><label>${I18N.t('biz.resolve.text_label')}<textarea id="resolutionText" maxlength="500"></textarea></label>
    <label>${I18N.t('biz.resolve.kind_label')}<select id="resolutionKind"><option value="REPLY">${I18N.t('biz.resolve.kind.REPLY')}</option><option value="CLOSE">${I18N.t('biz.resolve.kind.CLOSE')}</option><option value="REWORK">${I18N.t('biz.resolve.kind.REWORK')}</option><option value="REFUND">${I18N.t('biz.resolve.kind.REFUND')}</option></select></label>
    <label>${I18N.t('biz.resolve.amount_label')}<input id="resolutionAmount" inputmode="numeric"></label><label>${I18N.t('biz.resolve.currency_label')}<input id="resolutionCurrency" maxlength="140" placeholder="${I18N.t('biz.resolve.currency_placeholder')}"></label>
    <button id="resolutionSend">${I18N.t('biz.resolve.send')}</button><label>${I18N.t('biz.resolve.proposal_label')}<select id="resolutionProposal"></select></label><button class="ghost" id="resolutionAccept">${I18N.t('biz.resolve.accept')}</button><button class="ghost" id="resolutionReject">${I18N.t('biz.resolve.reject')}</button><button class="ghost" id="resolutionRework">${I18N.t('biz.resolve.rework')}</button><button class="ghost" id="resolutionRetry">${I18N.t('biz.resolve.retry')}</button><p class="sub" id="resolutionDelivery"></p>`);
  section(account, `<h2>${I18N.t('biz.asset.heading')}</h2><p class="sub">${I18N.t('biz.asset.intro_a')}<span id="assetUploadLimit">${I18N.t('biz.asset.intro_loading')}</span>${I18N.t('biz.asset.intro_b')}</p>
    <label>${I18N.t('biz.asset.upload_label')}<input type="file" id="assetFile"></label><button class="ghost" id="assetUpload">${I18N.t('biz.asset.upload_btn')}</button><pre id="assetReference"></pre>
    <label>${I18N.t('biz.asset.trade_label')}<select id="assetTrade"></select></label><button class="ghost" id="assetRefresh">${I18N.t('biz.asset.refresh_btn')}</button><button class="ghost" id="assetFetch">${I18N.t('biz.asset.fetch_btn')}</button><div id="assetDownload"></div>`);
  section(account, `<h2>${I18N.t('biz.backup.heading')}</h2><p class="sub">${I18N.t('biz.backup.intro')}</p>
    <label>${I18N.t('biz.backup.password_label')}<input id="backupPassword" type="password" autocomplete="new-password"></label><button id="backupCreate">${I18N.t('biz.backup.create_btn')}</button>
    <label>${I18N.t('biz.backup.path_label')}<input id="backupPath"></label><button class="ghost" id="backupValidate">${I18N.t('biz.backup.validate_btn')}</button><pre id="backupResult"></pre>
    <p class="sub">${I18N.t('biz.backup.offline_note')}</p>
    <h3>${I18N.t('biz.backup.reconnect_heading')}</h3><label><input type="checkbox" id="reconnectEnable">${I18N.t('biz.backup.reconnect_enable')}</label>
    <p class="sub">${I18N.t('biz.backup.reconnect_note')}</p><button class="ghost" id="reconnectSave">${I18N.t('biz.backup.reconnect_save')}</button>`);
  section(sell, `<h2>${I18N.t('biz.pkg.heading')}</h2><p class="sub">${I18N.t('biz.pkg.intro')}</p>
    <label>${I18N.t('biz.pkg.file_label')}<input id="packageFile" type="file" accept=".json,application/json"></label><button class="ghost" id="packagePreview">${I18N.t('biz.pkg.preview_btn')}</button>
    <pre id="packagePreviewResult"></pre><label><input id="packageGrant" type="checkbox">${I18N.t('biz.pkg.grant')}</label><button id="packageInstall" disabled>${I18N.t('biz.pkg.install_btn')}</button>
    <p class="sub">${I18N.t('biz.pkg.note')}</p><div id="packageInstalled"></div>`);
  section(account, `<h2>${I18N.t('biz.upgrade.heading')}</h2><p class="sub">${I18N.t('biz.upgrade.intro')}</p>
    <label>${I18N.t('biz.upgrade.manifest_label')}<input type="file" accept=".json,application/json" id="upgradeManifest"></label><pre id="upgradeManifestView"></pre>
    <label>${I18N.t('biz.upgrade.path_label')}<input id="upgradePath"></label><button class="ghost" id="upgradeTrust">${I18N.t('biz.upgrade.trust_btn')}</button><button class="ghost" id="upgradePreview">${I18N.t('biz.upgrade.preview_btn')}</button>
    <label>${I18N.t('biz.upgrade.password_label')}<input type="password" id="upgradePassword" autocomplete="new-password"></label><button class="ghost" id="upgradePrepare">${I18N.t('biz.upgrade.prepare_btn')}</button>
    <label>${I18N.t('biz.upgrade.retry_label')}<select id="upgradeRetry"><option value="">${I18N.t('biz.upgrade.retry_placeholder')}</option></select></label><button id="upgradeApply" disabled>${I18N.t('biz.upgrade.apply_btn')}</button><pre id="upgradeResult"></pre>`);
  let state = {}, packageBody = null, packagePreview = null;
  let upgradeManifest = null, upgradePending = null;
  const upgradeLabel = s => {const v = I18N.text('biz.upgrade.state.' + s); return v === null ? s : v;};
  const viewLabel = k => {const v = I18N.text('biz.resolve.view.' + k); return v === null ? k : v;};
  async function run(button, action) {
    button.disabled = true;
    try {await action();} catch(e) {notice(e.message,true);} finally {button.disabled=(button.id==='packageInstall' && (!packagePreview||!el('packageGrant').checked))||(button.id==='upgradeApply'&&!upgradePending);}
  }
  async function load() {
    state = await request('/v1/runtime');
    showAssetLimit(state);
    const previous=el('resolutionDispute').value;
    el('resolutionDispute').innerHTML=(state.disputes||[]).filter(d=>d.trade_uid).map(d=>`<option value="${safe(d.id)}">${safe(d.reason)} · ${safe(d.task_id)}</option>`).join('') || '<option value="">'+I18N.t('biz.resolve.empty_disputes')+'</option>';
    if ([...el('resolutionDispute').options].some(o=>o.value===previous))el('resolutionDispute').value=previous;
    el('reconnectEnable').checked=state.reconnect_policy?.enabled===true;
    el('packageInstalled').innerHTML=(state.agent_packages||[]).map(p=>`<p>${I18N.t('biz.pkg.row',{name:safe(p.manifest.card.name),version:safe(p.manifest.version),state:I18N.t(p.active?'biz.pkg.installed':'biz.pkg.removed'),did:safe(p.manifest.author_did)})}</p>`).join('') || '<p class="sub">'+I18N.t('biz.pkg.empty')+'</p>';
    if(!upgradePending)el('upgradeResult').textContent=(state.upgrades?.pending||[]).map(p=>`${upgradeLabel(p.state)}${p.reason?' · '+p.reason:''}`).join('\n')||I18N.t('biz.upgrade.empty');
    const retry=el('upgradeRetry').value;el('upgradeRetry').innerHTML='<option value="">'+I18N.t('biz.upgrade.retry_placeholder')+'</option>'+(state.upgrades?.pending||[]).filter(p=>['PREPARED','FAILED','ROLLED_BACK_BINARY'].includes(p.state)).map(p=>`<option value="${safe(p.id)}">${safe(upgradeLabel(p.state))} · ${safe(p.manifest?.sha256?.slice(0,8))}</option>`).join('');if([...el('upgradeRetry').options].some(o=>o.value===retry))el('upgradeRetry').value=retry;
  }
  function showAssetLimit(runtime) {
    const bytes=runtime.maintenance?.asset_limits?.file_bytes;
    if(!Number.isSafeInteger(bytes)||bytes<1)throw Error(I18N.t('biz.asset.err_no_limit'));
    el('assetUploadLimit').textContent=I18N.t('biz.asset.limit',{mib:bytes/1048576});
    return bytes;
  }
  async function messages() {
    await load();
    const id=el('resolutionDispute').value;
    if (!id) {el('resolutionMessages').textContent=I18N.t('biz.resolve.no_dispute');return;}
    const result=await request('/v1/disputes/'+encodeURIComponent(id)+'/messages');
    el('resolutionMessages').innerHTML=(result.messages||[]).map(m=>`<article class="agent"><b>${safe(viewLabel(m.kind))}</b> · ${safe(m.author_did)}<p>${safe(m.body.text||m.body.proposal_hash)}</p>${m.kind==='PROPOSAL'?`<p>${safe(m.body.action)} ${safe(result.amount_minor_decimal?.[m.message_id]??m.body.amount_minor??'')} ${safe(m.body.currency)}</p>`:''}</article>`).join('') || '<p class="sub">'+I18N.t('biz.resolve.empty_messages')+'</p>';
    el('resolutionProposal').innerHTML=(result.messages||[]).filter(m=>m.kind==='PROPOSAL').map(m=>`<option value="${safe(m.message_id)}">${safe(m.body.action)} · ${safe(m.body.text)}</option>`).join('') || '<option value="">'+I18N.t('biz.resolve.no_proposal')+'</option>';
    const uid=(state.disputes||[]).find(d=>d.id===id)?.trade_uid;
    const out=(state.resolution_outbox||[]).filter(m=>m.trade_uid===uid);
    const agreements=(state.resolution_agreements||[]).filter(a=>a.proposal.trade_uid===uid);
    el('resolutionDelivery').textContent=out.map(m=>I18N.t('biz.resolve.delivery_item',{id:m.message_id.slice(-8),state:I18N.t(m.state==='DELIVERED'?'biz.resolve.delivered':'biz.resolve.pending')})).join('；') + agreements.map(a=>' '+I18N.t('biz.resolve.agreement',{state:I18N.t(a.state==='BOTH_ACCEPTED'?'biz.resolve.both_accepted':'biz.resolve.one_accepted'),execution:a.execution})).join('；');
  }
  el('resolutionRead').onclick=e=>run(e.currentTarget,messages);
  el('resolutionDispute').onchange=()=>messages().catch(e=>notice(e.message,true));
  el('resolutionSend').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, action=el('resolutionKind').value, text=el('resolutionText').value.trim();
    if(!id||!text)throw Error(I18N.t('biz.resolve.err_pick_and_text'));
    const value=el('resolutionCurrency').value.trim(),currency=value.startsWith('points:')?value:value.toUpperCase();
    const body=action==='REPLY'?{kind:'REPLY',body:{text}}:{kind:'PROPOSAL',body:{action,text,amount_minor:action==='REFUND'?el('resolutionAmount').value:0,currency:action==='REFUND'?currency:''}};
    await request('/v1/disputes/'+encodeURIComponent(id)+'/messages',body,{'Idempotency-Key':crypto.randomUUID()});
    notice(I18N.t('biz.resolve.sent'));el('resolutionText').value='';await messages();
  });
  el('resolutionAccept').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, proposal_id=el('resolutionProposal').value;if(!id||!proposal_id)throw Error(I18N.t('biz.resolve.err_pick_full_proposal'));
    if(!confirm(I18N.t('biz.resolve.confirm_accept')))return;
    await request('/v1/disputes/'+encodeURIComponent(id)+'/agreements',{proposal_id},{'Idempotency-Key':crypto.randomUUID()});await messages();
  });
  el('resolutionReject').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, proposal_id=el('resolutionProposal').value;if(!id||!proposal_id)throw Error(I18N.t('biz.resolve.err_pick_proposal'));
    await request('/v1/disputes/'+encodeURIComponent(id)+'/decisions',{proposal_id,accepted:false},{'Idempotency-Key':crypto.randomUUID()});await messages();
  });
  el('resolutionRework').onclick=e=>run(e.currentTarget,async()=>{
    const proposal_id=el('resolutionProposal').value;if(!proposal_id)throw Error(I18N.t('biz.resolve.err_pick_rework'));
    if(!confirm(I18N.t('biz.resolve.confirm_rework')))return;
    const result=await request('/v1/resolutions/rework',{proposal_id});notice(result.ok?I18N.t('biz.resolve.rework_ok'):I18N.t('biz.resolve.rework_result',{result:result.error||result.state}));await messages();await refresh();
  });
  el('resolutionRetry').onclick=e=>run(e.currentTarget,async()=>{
    await load();const uid=(state.disputes||[]).find(d=>d.id===el('resolutionDispute').value)?.trade_uid;
    for(const row of (state.resolution_outbox||[]).filter(r=>r.trade_uid===uid&&r.state!=='DELIVERED'))await request('/v1/resolutions/retry',{message_id:row.message_id});await messages();
  });
  el('assetUpload').onclick=e=>run(e.currentTarget,async()=>{
    const limit=showAssetLimit(await request('/v1/runtime'));
    const file=el('assetFile').files[0];if(!file||file.size<1||file.size>limit)throw Error(I18N.t('biz.asset.err_size',{mib:limit/1048576}));
    const response=await fetch('/v1/assets/upload',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});const row=await response.json();if(!response.ok)throw Error(row.error||I18N.t('biz.asset.upload_failed'));
    el('assetReference').textContent=JSON.stringify({assets:[row]},null,2);notice(I18N.t('biz.asset.saved'));
  });
  async function assetTrades(){
    const result=await request('/v1/trades');const calls=state.recent_calls||[];const options=[];
    for(const trade of result.trades||[]){if(trade.role!=='buyer'||trade.execution!=='DELIVERED')continue;
      const row=calls.find(r=>r.scope===trade.scope&&r.task_id===trade.task_id);if(!row)continue;
      const detail=await request('/v1/calls/detail?scope='+encodeURIComponent(trade.scope)+'&task_id='+encodeURIComponent(trade.task_id));
      for(const ref of detail.assets||[])options.push({uid:trade.trade_uid,id:ref.asset_id,label:I18N.t('biz.asset.trade_option',{task:trade.task_id,mime:ref.mime_type,size:ref.size})});
    }
    el('assetTrade').innerHTML=options.map(o=>`<option value="${safe(o.uid+'|'+o.id)}">${safe(o.label)}</option>`).join('')||'<option value="">'+I18N.t('biz.asset.empty_trades')+'</option>';
  }
  el('assetFetch').onclick=e=>run(e.currentTarget,async()=>{
    const [trade_uid,asset_id]=el('assetTrade').value.split('|');if(!trade_uid||!asset_id)throw Error(I18N.t('biz.asset.err_pick_trade'));
    const ref=await request('/v1/assets/fetch',{trade_uid,asset_id});el('assetDownload').innerHTML=`<a href="/v1/assets/${encodeURIComponent(ref.asset_id)}/content" download>${I18N.t('biz.asset.download_link',{size:safe(ref.size)})}</a>`;
  });
  el('assetRefresh').onclick=e=>run(e.currentTarget,async()=>{await load();await assetTrades();});
  for(const [id,path] of [['backupCreate','/v1/backups'],['backupValidate','/v1/restores/validate']])el(id).onclick=e=>run(e.currentTarget,async()=>{
    const password=el('backupPassword').value;if(password.length<12)throw Error(I18N.t('biz.backup.err_password'));
    el('backupResult').textContent=I18N.t('biz.backup.working');
    try {const row=await request(path,{password,...(id==='backupValidate'?{path:el('backupPath').value}:{})});el('backupResult').textContent=JSON.stringify(row,null,2);if(row.path)el('backupPath').value=row.path;}
    finally {el('backupPassword').value='';}
  });
  el('reconnectSave').onclick=e=>run(e.currentTarget,async()=>{
    const current=await request('/v1/runtime');await request('/v1/reconnect/policy',{enabled:el('reconnectEnable').checked,expected_revision:current.reconnect_policy.revision});notice(I18N.t('biz.backup.saved'));await load();
  });
  el('packageFile').onchange=()=>{packageBody=null;packagePreview=null;el('packageGrant').checked=false;el('packageInstall').disabled=true;el('packagePreviewResult').textContent=I18N.t('biz.pkg.hint_check');};
  el('packageGrant').onchange=()=>{el('packageInstall').disabled=!packagePreview||!el('packageGrant').checked;};
  el('packagePreview').onclick=e=>run(e.currentTarget,async()=>{
    const file=el('packageFile').files[0];if(!file||file.size>500000)throw Error(I18N.t('biz.pkg.err_size'));
    packageBody=JSON.parse(await file.text());packagePreview=await request('/v1/packages/preview',packageBody);el('packagePreviewResult').textContent=JSON.stringify(packagePreview,null,2);el('packageInstall').disabled=!el('packageGrant').checked;
  });
  el('packageInstall').onclick=e=>run(e.currentTarget,async()=>{
    if(!packagePreview||!el('packageGrant').checked)throw Error(I18N.t('biz.pkg.err_grant'));
    await request('/v1/packages/install',{...packageBody,accepted_digest:packagePreview.preview_digest,granted_permissions:{network:'NONE',filesystem:'NONE'},command_id:crypto.randomUUID()});
    notice(I18N.t('biz.pkg.installed_notice'));packagePreview=null;el('packageGrant').checked=false;await refresh();await load();
  });
  el('upgradeManifest').onchange=async()=>{
    upgradeManifest=null;upgradePending=null;el('upgradeApply').disabled=true;
    try{const file=el('upgradeManifest').files[0];if(!file||file.size>10000)throw Error(I18N.t('biz.upgrade.err_size'));upgradeManifest=JSON.parse(await file.text());el('upgradeManifestView').textContent=JSON.stringify(upgradeManifest,null,2);}catch(e){notice(e.message,true);}
  };
  el('upgradeTrust').onclick=e=>run(e.currentTarget,async()=>{
    const author_did=upgradeManifest?.author_did;if(!author_did)throw Error(I18N.t('biz.upgrade.err_manifest'));
    if(!confirm(I18N.t('biz.upgrade.confirm_trust',{did:author_did})))return;
    await request('/v1/upgrades/trust',{author_did,trusted:true});notice(I18N.t('biz.upgrade.trusted'));
  });
  el('upgradePreview').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradeManifest)throw Error(I18N.t('biz.upgrade.err_no_manifest'));const result=await request('/v1/upgrades/preview',{manifest:upgradeManifest,path:el('upgradePath').value});el('upgradeResult').textContent=JSON.stringify(result,null,2);
  });
  el('upgradePrepare').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradeManifest)throw Error(I18N.t('biz.upgrade.err_no_manifest'));
    try{upgradePending=await request('/v1/upgrades/prepare',{manifest:upgradeManifest,path:el('upgradePath').value,password:el('upgradePassword').value});el('upgradeResult').textContent=JSON.stringify(upgradePending,null,2);el('upgradeApply').disabled=false;}finally{el('upgradePassword').value='';}
  });
  el('upgradeApply').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradePending)throw Error(I18N.t('biz.upgrade.err_prepare'));if(!confirm(I18N.t('biz.upgrade.confirm_apply')))return;
    await request('/v1/upgrades/apply',{pending_id:upgradePending.id});el('upgradeResult').textContent=I18N.t('biz.upgrade.queued');upgradePending=null;
  });
  el('upgradeRetry').onchange=()=>{const row=(state.upgrades?.pending||[]).find(p=>p.id===el('upgradeRetry').value);upgradePending=row||null;el('upgradeApply').disabled=!row;if(row){upgradeManifest=row.manifest;el('upgradePath').value=row.destination;el('upgradeManifestView').textContent=JSON.stringify(row.manifest,null,2);el('upgradeResult').textContent=row.reason||I18N.t('biz.upgrade.picked')}};
  load().then(assetTrades).catch(e=>notice(e.message,true));
})();
