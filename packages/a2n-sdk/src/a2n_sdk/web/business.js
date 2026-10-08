/* Owner controls for negotiation, portable recovery and explicit code installation. */
(() => {
  'use strict';
  const account = document.getElementById('account'), sell = document.getElementById('sell');
  if (!account || !sell) return;
  const safe = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const el = id => document.getElementById(id);
  const section = (parent, html) => {const node=document.createElement('section');node.className='panel';node.innerHTML=html;parent.append(node);};
  section(account, `<h2>和对方协商</h2><p class="sub">协商正文只给交易双方，发送时加密。双方接受关闭方案后关闭异议；同一版本和输入的补做需买方明确启动一次新任务。退款仍需真实通道。</p>
    <label>选择异议记录<select id="resolutionDispute"></select></label><button class="ghost" id="resolutionRead">刷新协商</button>
    <div id="resolutionMessages"></div><label>回复或方案说明<textarea id="resolutionText" maxlength="500"></textarea></label>
    <label>消息类型<select id="resolutionKind"><option value="REPLY">回复说明</option><option value="CLOSE">提出关闭异议</option><option value="REWORK">提出补做</option><option value="REFUND">提出退款</option></select></label>
    <label>退款金额（最小单位整数）<input id="resolutionAmount" inputmode="numeric"></label><label>退款币种或积分发行方<input id="resolutionCurrency" maxlength="140" placeholder="例如 CNY；积分填 points:发行方DID"></label>
    <button id="resolutionSend">签名并发送</button><label>待确认方案<select id="resolutionProposal"></select></label><button class="ghost" id="resolutionAccept">明确接受这份方案</button><button class="ghost" id="resolutionReject">拒绝这份方案</button><button class="ghost" id="resolutionRework">明确启动约定补做</button><button class="ghost" id="resolutionRetry">重试未送达消息</button><p class="sub" id="resolutionDelivery"></p>`);
  section(account, `<h2>私有文件交付</h2><p class="sub">原文件保存在加密账本，通过交易双方的签名和加密通道分段读取。免费采样一定公开；图像预览由节点生成有界、去元数据的缩略图，原文件和取件句柄不进入样品。单文件上限 16 MiB；直连不可用时可经已启用文件转送的公共节点取回，中转节点不能解密内容。</p>
    <label>上传到本机私有文件库<input type="file" id="assetFile"></label><button class="ghost" id="assetUpload">保存文件并生成交付引用</button><pre id="assetReference"></pre>
    <label>真实交易中的文件<select id="assetTrade"></select></label><button class="ghost" id="assetRefresh">刷新交付列表</button><button class="ghost" id="assetFetch">核验并取回文件</button><div id="assetDownload"></div>`);
  section(account, `<h2>便携备份与恢复</h2><p class="sub">备份包含节点身份、凭据和交易记录，以恢复口令重新加密。请把备份复制到其他设备；恢复前停止同一身份的原节点。</p>
    <label>恢复口令（至少 12 个字符）<input id="backupPassword" type="password" autocomplete="new-password"></label><button id="backupCreate">导出并完整验证</button>
    <label>待验证备份的本机绝对路径<input id="backupPath"></label><button class="ghost" id="backupValidate">验证恢复口令与文件</button><pre id="backupResult"></pre>
    <p class="sub">实际导入由离线恢复命令执行，运行中的节点会拒绝覆盖。</p>
    <h3>重连采样</h3><label><input type="checkbox" id="reconnectEnable">网络联系中断后恢复时，提供额外免费采样</label>
    <p class="sub">依据本机核验通信的观察间隔；默认每 24 小时最多 5 次，初始和重连累计最多 30 次。不重排首批样品，不叠加未用完额度。</p><button class="ghost" id="reconnectSave">保存采样策略</button>`);
  section(sell, `<h2>安装签名 Agent 包</h2><p class="sub">此版本接收 WASM JSON 包。代码在独立工作进程运行，只能读本次输入、写本次输出；不提供文件和网络接口。发布者签名用于确认来源，实际质量由本机交易履历体现。</p>
    <label>选择包文件（包含 manifest 与 module_base64）<input id="packageFile" type="file" accept=".json,application/json"></label><button class="ghost" id="packagePreview">核对签名和权限</button>
    <pre id="packagePreviewResult"></pre><label><input id="packageGrant" type="checkbox">授权运行预览中这一份代码和资源上限</label><button id="packageInstall" disabled>安装到本机供给</button>
    <p class="sub">安装后需在上方明确上架。创作者分成需要真实支付通道，目前不会自动产生分成付款。</p><div id="packageInstalled"></div>`);
  section(account, `<h2>可信发布者升级</h2><p class="sub">先明确可信的发布者，再核验其签名清单和本机程序文件。准备升级会导出并验证完整备份；应用升级会短暂停机，检查新程序并沿用同一身份和账本。启动失败时尝试切回旧程序，数据库恢复需使用备份。</p>
    <label>发布清单 JSON 文件<input type="file" accept=".json,application/json" id="upgradeManifest"></label><pre id="upgradeManifestView"></pre>
    <label>新 A2N.exe 的本机绝对路径<input id="upgradePath"></label><button class="ghost" id="upgradeTrust">明确信任清单中的发布者</button><button class="ghost" id="upgradePreview">核验签名与程序</button>
    <label>升级备份的恢复口令<input type="password" id="upgradePassword" autocomplete="new-password"></label><button class="ghost" id="upgradePrepare">备份并准备升级</button>
    <label>继续已准备或中断的升级<select id="upgradeRetry"><option value="">选择已有升级</option></select></label><button id="upgradeApply" disabled>明确应用已准备的升级</button><pre id="upgradeResult"></pre>`);
  let state = {}, packageBody = null, packagePreview = null;
  let upgradeManifest = null, upgradePending = null;
  async function run(button, action) {
    button.disabled = true;
    try {await action();} catch(e) {notice(e.message,true);} finally {button.disabled=(button.id==='packageInstall' && (!packagePreview||!el('packageGrant').checked))||(button.id==='upgradeApply'&&!upgradePending);}
  }
  async function load() {
    state = await request('/v1/runtime');
    const previous=el('resolutionDispute').value;
    el('resolutionDispute').innerHTML=(state.disputes||[]).filter(d=>d.trade_uid).map(d=>`<option value="${safe(d.id)}">${safe(d.reason)} · ${safe(d.task_id)}</option>`).join('') || '<option value="">尚无双方交易异议</option>';
    if ([...el('resolutionDispute').options].some(o=>o.value===previous))el('resolutionDispute').value=previous;
    el('reconnectEnable').checked=state.reconnect_policy?.enabled===true;
    el('packageInstalled').innerHTML=(state.agent_packages||[]).map(p=>`<p>${safe(p.manifest.card.name)} · 版本 ${safe(p.manifest.version)} · ${p.active?'已安装':'已卸载'} · 发布者 ${safe(p.manifest.author_did)}</p>`).join('') || '<p class="sub">尚未安装签名代码包。</p>';
    const upgradeLabels={PREPARED:'已准备，可明确重试',QUEUED:'正在排队',APPLYING:'正在切换程序',INSTALLED:'已完成',FAILED:'失败，可重新核验',ROLLED_BACK_BINARY:'已回到原程序',RECOVERY_REQUIRED:'需要检查程序或使用原备份恢复'};
    if(!upgradePending)el('upgradeResult').textContent=(state.upgrades?.pending||[]).map(p=>`${upgradeLabels[p.state]||p.state}${p.reason?' · '+p.reason:''}`).join('\n')||'尚无升级记录；当前开发产物没有正式发布者签名。';
    const retry=el('upgradeRetry').value;el('upgradeRetry').innerHTML='<option value="">选择已有升级</option>'+(state.upgrades?.pending||[]).filter(p=>['PREPARED','FAILED','ROLLED_BACK_BINARY'].includes(p.state)).map(p=>`<option value="${safe(p.id)}">${safe(upgradeLabels[p.state])} · ${safe(p.manifest?.sha256?.slice(0,8))}</option>`).join('');if([...el('upgradeRetry').options].some(o=>o.value===retry))el('upgradeRetry').value=retry;
  }
  async function messages() {
    await load();
    const id=el('resolutionDispute').value;
    if (!id) {el('resolutionMessages').textContent='先为一笔真实交易提出异议。';return;}
    const result=await request('/v1/disputes/'+encodeURIComponent(id)+'/messages');
    const labels={REPLY:'回复',PROPOSAL:'方案',ACCEPT:'接受',REJECT:'拒绝'};
    el('resolutionMessages').innerHTML=(result.messages||[]).map(m=>`<article class="agent"><b>${safe(labels[m.kind]||m.kind)}</b> · ${safe(m.author_did)}<p>${safe(m.body.text||m.body.proposal_hash)}</p>${m.kind==='PROPOSAL'?`<p>${safe(m.body.action)} ${safe(result.amount_minor_decimal?.[m.message_id]??m.body.amount_minor??'')} ${safe(m.body.currency)}</p>`:''}</article>`).join('') || '<p class="sub">尚未交换协商消息。</p>';
    el('resolutionProposal').innerHTML=(result.messages||[]).filter(m=>m.kind==='PROPOSAL').map(m=>`<option value="${safe(m.message_id)}">${safe(m.body.action)} · ${safe(m.body.text)}</option>`).join('') || '<option value="">尚无方案</option>';
    const uid=(state.disputes||[]).find(d=>d.id===id)?.trade_uid;
    const out=(state.resolution_outbox||[]).filter(m=>m.trade_uid===uid);
    const agreements=(state.resolution_agreements||[]).filter(a=>a.proposal.trade_uid===uid);
    el('resolutionDelivery').textContent=out.map(m=>`${m.message_id.slice(-8)}：${m.state==='DELIVERED'?'对方已签名收件':'尚未送达'}`).join('；') + agreements.map(a=>` 方案：${a.state==='BOTH_ACCEPTED'?'双方已接受':'一方已接受'}，执行：${a.execution}`).join('；');
  }
  el('resolutionRead').onclick=e=>run(e.currentTarget,messages);
  el('resolutionDispute').onchange=()=>messages().catch(e=>notice(e.message,true));
  el('resolutionSend').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, action=el('resolutionKind').value, text=el('resolutionText').value.trim();
    if(!id||!text)throw Error('选择交易异议并填写说明');
    const value=el('resolutionCurrency').value.trim(),currency=value.startsWith('points:')?value:value.toUpperCase();
    const body=action==='REPLY'?{kind:'REPLY',body:{text}}:{kind:'PROPOSAL',body:{action,text,amount_minor:action==='REFUND'?el('resolutionAmount').value:0,currency:action==='REFUND'?currency:''}};
    await request('/v1/disputes/'+encodeURIComponent(id)+'/messages',body,{'Idempotency-Key':crypto.randomUUID()});
    notice('消息已签名保存，正在取得对方收件确认。');el('resolutionText').value='';await messages();
  });
  el('resolutionAccept').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, proposal_id=el('resolutionProposal').value;if(!id||!proposal_id)throw Error('选择一份完整方案');
    if(!confirm('明确接受所选方案？关闭会在双方签名后执行，补做需买方另行启动，退款需真实通道。'))return;
    await request('/v1/disputes/'+encodeURIComponent(id)+'/agreements',{proposal_id},{'Idempotency-Key':crypto.randomUUID()});await messages();
  });
  el('resolutionReject').onclick=e=>run(e.currentTarget,async()=>{
    const id=el('resolutionDispute').value, proposal_id=el('resolutionProposal').value;if(!id||!proposal_id)throw Error('选择一份方案');
    await request('/v1/disputes/'+encodeURIComponent(id)+'/decisions',{proposal_id,accepted:false},{'Idempotency-Key':crypto.randomUUID()});await messages();
  });
  el('resolutionRework').onclick=e=>run(e.currentTarget,async()=>{
    const proposal_id=el('resolutionProposal').value;if(!proposal_id)throw Error('选择双方已经接受的补做方案');
    if(!confirm('以原版本和原输入执行约定的一次补做？原交易保留，补做会建立新的关联交易。'))return;
    const result=await request('/v1/resolutions/rework',{proposal_id});notice(result.ok?'约定补做已交付。':'补做结果：'+(result.error||result.state));await messages();await refresh();
  });
  el('resolutionRetry').onclick=e=>run(e.currentTarget,async()=>{
    await load();const uid=(state.disputes||[]).find(d=>d.id===el('resolutionDispute').value)?.trade_uid;
    for(const row of (state.resolution_outbox||[]).filter(r=>r.trade_uid===uid&&r.state!=='DELIVERED'))await request('/v1/resolutions/retry',{message_id:row.message_id});await messages();
  });
  el('assetUpload').onclick=e=>run(e.currentTarget,async()=>{
    const file=el('assetFile').files[0];if(!file||file.size<1||file.size>16777216)throw Error('选择不超过 16 MiB 的文件');
    const response=await fetch('/v1/assets/upload',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});const row=await response.json();if(!response.ok)throw Error(row.error||'上传失败');
    el('assetReference').textContent=JSON.stringify({assets:[row]},null,2);notice('文件已保存在本机私有库。真实 Agent 交付可返回这一引用。');
  });
  async function assetTrades(){
    const result=await request('/v1/trades');const calls=state.recent_calls||[];const options=[];
    for(const trade of result.trades||[]){if(trade.role!=='buyer'||trade.execution!=='DELIVERED')continue;
      const row=calls.find(r=>r.scope===trade.scope&&r.task_id===trade.task_id);if(!row)continue;
      const detail=await request('/v1/calls/detail?scope='+encodeURIComponent(trade.scope)+'&task_id='+encodeURIComponent(trade.task_id));
      for(const ref of detail.assets||[])options.push({uid:trade.trade_uid,id:ref.asset_id,label:trade.task_id+' · '+ref.mime_type+' · '+ref.size+' 字节'});
    }
    el('assetTrade').innerHTML=options.map(o=>`<option value="${safe(o.uid+'|'+o.id)}">${safe(o.label)}</option>`).join('')||'<option value="">尚无真实文件交付</option>';
  }
  el('assetFetch').onclick=e=>run(e.currentTarget,async()=>{
    const [trade_uid,asset_id]=el('assetTrade').value.split('|');if(!trade_uid||!asset_id)throw Error('先选择一笔真实文件交付');
    const ref=await request('/v1/assets/fetch',{trade_uid,asset_id});el('assetDownload').innerHTML=`<a href="/v1/assets/${encodeURIComponent(ref.asset_id)}/content" download>下载已核验文件（${safe(ref.size)} 字节）</a>`;
  });
  el('assetRefresh').onclick=e=>run(e.currentTarget,async()=>{await load();await assetTrades();});
  for(const [id,path] of [['backupCreate','/v1/backups'],['backupValidate','/v1/restores/validate']])el(id).onclick=e=>run(e.currentTarget,async()=>{
    const password=el('backupPassword').value;if(password.length<12)throw Error('恢复口令至少 12 个字符');
    el('backupResult').textContent='正在验证完整数据库和文件，请稍候…';
    try {const row=await request(path,{password,...(id==='backupValidate'?{path:el('backupPath').value}:{})});el('backupResult').textContent=JSON.stringify(row,null,2);if(row.path)el('backupPath').value=row.path;}
    finally {el('backupPassword').value='';}
  });
  el('reconnectSave').onclick=e=>run(e.currentTarget,async()=>{
    const current=await request('/v1/runtime');await request('/v1/reconnect/policy',{enabled:el('reconnectEnable').checked,expected_revision:current.reconnect_policy.revision});notice('重连采样策略已保存。');await load();
  });
  el('packageFile').onchange=()=>{packageBody=null;packagePreview=null;el('packageGrant').checked=false;el('packageInstall').disabled=true;el('packagePreviewResult').textContent='请先核对选中的包。';};
  el('packageGrant').onchange=()=>{el('packageInstall').disabled=!packagePreview||!el('packageGrant').checked;};
  el('packagePreview').onclick=e=>run(e.currentTarget,async()=>{
    const file=el('packageFile').files[0];if(!file||file.size>500000)throw Error('选择不超过 500 KB 的 JSON 包');
    packageBody=JSON.parse(await file.text());packagePreview=await request('/v1/packages/preview',packageBody);el('packagePreviewResult').textContent=JSON.stringify(packagePreview,null,2);el('packageInstall').disabled=!el('packageGrant').checked;
  });
  el('packageInstall').onclick=e=>run(e.currentTarget,async()=>{
    if(!packagePreview||!el('packageGrant').checked)throw Error('先核对包并明确授权');
    await request('/v1/packages/install',{...packageBody,accepted_digest:packagePreview.preview_digest,granted_permissions:{network:'NONE',filesystem:'NONE'},command_id:crypto.randomUUID()});
    notice('签名 Agent 包已安装，可在本机供给里检查并上架。');packagePreview=null;el('packageGrant').checked=false;await refresh();await load();
  });
  el('upgradeManifest').onchange=async()=>{
    upgradeManifest=null;upgradePending=null;el('upgradeApply').disabled=true;
    try{const file=el('upgradeManifest').files[0];if(!file||file.size>10000)throw Error('选择不超过 10 KB 的签名清单');upgradeManifest=JSON.parse(await file.text());el('upgradeManifestView').textContent=JSON.stringify(upgradeManifest,null,2);}catch(e){notice(e.message,true);}
  };
  el('upgradeTrust').onclick=e=>run(e.currentTarget,async()=>{
    const author_did=upgradeManifest?.author_did;if(!author_did)throw Error('先选择发布清单');
    if(!confirm('明确信任此发布者发行完整节点程序？请核对身份：'+author_did))return;
    await request('/v1/upgrades/trust',{author_did,trusted:true});notice('本机已保存你的发布者信任选择。');
  });
  el('upgradePreview').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradeManifest)throw Error('先选择签名清单');const result=await request('/v1/upgrades/preview',{manifest:upgradeManifest,path:el('upgradePath').value});el('upgradeResult').textContent=JSON.stringify(result,null,2);
  });
  el('upgradePrepare').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradeManifest)throw Error('先选择签名清单');
    try{upgradePending=await request('/v1/upgrades/prepare',{manifest:upgradeManifest,path:el('upgradePath').value,password:el('upgradePassword').value});el('upgradeResult').textContent=JSON.stringify(upgradePending,null,2);el('upgradeApply').disabled=false;}finally{el('upgradePassword').value='';}
  });
  el('upgradeApply').onclick=e=>run(e.currentTarget,async()=>{
    if(!upgradePending)throw Error('先备份并准备升级');if(!confirm('应用已准备的升级？节点会短暂停机，身份和账本沿用当前目录。'))return;
    await request('/v1/upgrades/apply',{pending_id:upgradePending.id});el('upgradeResult').textContent='升级已排队。节点重新就绪后刷新控制台查看安装结果。';upgradePending=null;
  });
  el('upgradeRetry').onchange=()=>{const row=(state.upgrades?.pending||[]).find(p=>p.id===el('upgradeRetry').value);upgradePending=row||null;el('upgradeApply').disabled=!row;if(row){upgradeManifest=row.manifest;el('upgradePath').value=row.destination;el('upgradeManifestView').textContent=JSON.stringify(row.manifest,null,2);el('upgradeResult').textContent=row.reason||'已选择原升级；程序与发布者信任将在应用前重新核验。'}};
  load().then(assetTrades).catch(e=>notice(e.message,true));
})();
