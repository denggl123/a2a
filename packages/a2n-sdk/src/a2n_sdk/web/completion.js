/* Local product maintenance and explicitly granted optional execution. */
(() => {
  'use strict';const account=document.getElementById('account');if(!account)return;
  const panel=document.createElement('section');panel.className='panel';panel.id='maintenancePanel';
  panel.innerHTML=`<h2>备份与容量</h2><p id="maintenanceState" class="sub"></p>
    <label><input type="checkbox" id="maintenanceEnabled">定期创建加密备份</label>
    <label>备份间隔（小时）<input type="number" id="maintenanceHours" min="1" max="720" value="24"></label>
    <label>保留定期备份数<input type="number" id="maintenanceKeep" min="1" max="90" value="7"></label>
    <label>独立恢复口令（至少 12 字符，请另行保管）<input type="password" id="maintenancePassword" autocomplete="new-password"></label>
    <button id="maintenanceSave">保存备份安排</button><button class="ghost" id="maintenanceBackup">立即按此安排备份</button><button class="ghost" id="maintenanceArchive">归档已完成评审与旧试算</button>
    <p class="sub">归档保留加密历史；不会删除交易和积分账本。自动轮替只清理本安排创建的备份。</p>
    <label>单文件上限（MiB）<input id="assetFileLimit" type="number" min="1" max="1024"></label><label>全部文件容量（MiB）<input id="assetTotalLimit" type="number" min="1" max="65536"></label><label>文件数上限<input id="assetCountLimit" type="number" min="1" max="4096"></label><button id="assetLimitSave">保存容量限制</button>
    <details><summary>独立质量评审器</summary><p class="sub">代码评审使用无网络容器，需本机 Docker。语义评审会向你配置的服务发送所选成果与要求；只有明确登记的评审规则才执行。</p>
    <label>已安装的 Python 容器镜像<input id="reviewerImage" placeholder="例如 a2n-agent:latest"></label><label><input id="reviewerExecution" type="checkbox">允许独立执行交付代码并按买方用例测试</label>
    <label>可选语义评审地址<input id="reviewerURL" placeholder="https://…"></label><label>访问凭据<input id="reviewerToken" type="password" autocomplete="off"></label><label><input id="reviewerExternal" type="checkbox">允许向此评审器发送登记的任务结果</label><label><input id="reviewerHTTP" type="checkbox">允许本地 HTTP 评审</label>
    <button id="reviewerSave">保存独立评审设置</button><label>已安装的媒体检查容器（PDF / 视频）<input id="mediaInspectorImage" placeholder="a2n-utilities:20261009"></label><button id="mediaInspectorSave">连接媒体检查器</button><pre id="reviewerState"></pre></details>
    <details><summary>本地测试钱包</summary><p class="sub">连接已启动的 Anvil（链编号 31337），生成独立测试账户并注入测试币。已有钱包不会被替换。不会自动批准付款额度。</p>
    <label>本地测试链地址<input id="testWalletRPC" value="http://127.0.0.1:18945"></label><button id="testWalletCreate">创建并连接测试钱包</button><p id="testWalletState" class="sub"></p></details>`;
  account.append(panel);const el=id=>document.getElementById(id);let state=null;
  const workflows=document.createElement('section');workflows.className='panel';workflows.innerHTML=`<h2>安装工作流 Agent</h2><p class="sub">工作流使用包含 Python / 模型依赖的不可变容器镜像。安装后可以在供给页上架；升级和回滚保持同一个服务身份及前十次样品。</p>
    <label>签名工作流文件<input id="workflowFile" type="file" accept="application/json,.json"></label><label>工作流清单<textarea id="workflowManifest" rows="8" placeholder="粘贴发布者提供的签名清单"></textarea></label>
    <button class="ghost" id="workflowPreview">查看发布者、依赖与资源要求</button><pre id="workflowTerms"></pre>
    <label><input id="workflowGrant" type="checkbox">我允许此工作流使用清单中的容器与资源权限</label><label><input id="workflowDownload" type="checkbox">依赖镜像不存在时下载该不可变镜像</label><button id="workflowInstall">安装已查看的工作流</button>
    <details><summary>创建我自己的工作流清单</summary><p class="sub">填写不含 proof、author_did 的工作流定义，然后用本节点身份签名。此操作不执行工作流。</p><button class="ghost" id="workflowSign">以我的节点身份签名并导出</button></details>
    <label>回滚服务标识<input id="workflowRollbackService"></label><label>以前版本的清单摘要<input id="workflowRollbackDigest"></label><button class="ghost" id="workflowRollback">回滚此工作流</button><button class="ghost" id="workflowRefresh">更新安装进度</button><pre id="workflowState"></pre>`;account.append(workflows);let workflowPreview=null;
  async function load(){const r=await request('/v1/runtime');state=r.maintenance;if(!state)return;
    const p=state.policy;el('maintenanceEnabled').checked=p.enabled;el('maintenanceHours').value=p.interval_hours;el('maintenanceKeep').value=p.keep;
    el('assetFileLimit').value=state.asset_limits.file_bytes/1048576;el('assetTotalLimit').value=state.asset_limits.total_bytes/1048576;el('assetCountLimit').value=state.asset_limits.count;
    el('maintenanceState').textContent=`文件占用 ${(state.asset_bytes/1048576).toFixed(1)} MiB · 磁盘剩余 ${(state.disk_free_bytes/1073741824).toFixed(1)} GiB${state.capacity_warning?' · 接近容量限制，请检查':''} · 定期备份 ${state.backup.state||'未启用'} · 已归档评审 ${state.archived_plans} / 试算 ${state.archived_models}`;
    el('reviewerState').textContent=JSON.stringify(r.reviewers||{},null,2);
    const pay=await request('/v1/payment-coordination');el('testWalletState').textContent=pay.test_environment?`${pay.test_environment.notice} 钱包 ${pay.wallet_address}`:'';
    el('workflowState').textContent=JSON.stringify({installed:(r.workflows||[]).map(x=>({name:x.manifest.card.name,package_id:x.manifest.package_id,service_id:x.service_id,version:x.manifest.version,digest:x.preview_digest,active:x.active})),installing:r.workflow_installs||[]},null,2);
  }
  function action(id,fn){el(id).onclick=async e=>{e.currentTarget.disabled=true;try{await fn();await load()}catch(err){notice(err.message,true)}finally{e.currentTarget.disabled=false}}}
  action('maintenanceSave',async()=>{await request('/v1/maintenance/configure',{enabled:el('maintenanceEnabled').checked,interval_hours:Number(el('maintenanceHours').value),keep:Number(el('maintenanceKeep').value),password:el('maintenancePassword').value,expected_revision:state.policy.revision});el('maintenancePassword').value='';notice('备份安排已保存，请保管恢复口令。')});
  action('maintenanceBackup',()=>request('/v1/maintenance/backup-now',{}));action('maintenanceArchive',()=>request('/v1/maintenance/archive',{}));
  action('assetLimitSave',()=>request('/v1/assets/limits',{file_bytes:Number(el('assetFileLimit').value)*1048576,total_bytes:Number(el('assetTotalLimit').value)*1048576,count:Number(el('assetCountLimit').value),expected_revision:state.asset_limits.revision}));
  action('reviewerSave',async()=>{await request('/v1/reviewers/configure',{python_image:el('reviewerImage').value.trim(),semantic_url:el('reviewerURL').value.trim(),token:el('reviewerToken').value,allow_http:el('reviewerHTTP').checked,grant_execution:el('reviewerExecution').checked,grant_external_review:el('reviewerExternal').checked});el('reviewerToken').value=''});
  action('testWalletCreate',()=>request('/v1/payment-coordination/test-wallet',{rpc_url:el('testWalletRPC').value.trim()}));
  action('mediaInspectorSave',()=>request('/v1/assets/media/configure',{image:el('mediaInspectorImage').value.trim()}));
  el('workflowFile').onchange=async()=>{const file=el('workflowFile').files[0];if(file){if(file.size>65536){notice('清单过大',true);return}el('workflowManifest').value=await file.text();workflowPreview=null}};
  action('workflowPreview',async()=>{workflowPreview=await request('/v1/workflows/preview',{manifest:JSON.parse(el('workflowManifest').value)});el('workflowTerms').textContent=JSON.stringify(workflowPreview,null,2)});
  action('workflowInstall',async()=>{if(!workflowPreview||!el('workflowGrant').checked)throw Error('请先查看清单并授权资源');const manifest=JSON.parse(el('workflowManifest').value);const result=await request('/v1/workflows/install',{manifest,accepted_digest:workflowPreview.preview_digest,granted_permissions:workflowPreview.manifest.permissions,command_id:crypto.randomUUID(),download_dependencies:el('workflowDownload').checked});notice(result.phase?'安装任务已登记，请更新进度。':'工作流已安装，可在供给页上架。')});
  action('workflowSign',async()=>{const manifest=await request('/v1/workflows/sign',{manifest:JSON.parse(el('workflowManifest').value)});el('workflowManifest').value=JSON.stringify(manifest,null,2);const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='agent-workflow.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);workflowPreview=null});
  action('workflowRollback',()=>request('/v1/workflows/rollback',{service_id:el('workflowRollbackService').value.trim(),version_digest:el('workflowRollbackDigest').value.trim()}));
  action('workflowRefresh',load);
  const retryLabel=document.createElement('label');retryLabel.textContent='重试失败或中断的安装任务';
  const retryInput=document.createElement('input');retryInput.id='workflowRetryJob';retryLabel.append(retryInput);workflows.append(retryLabel);
  const retryButton=document.createElement('button');retryButton.id='workflowRetry';retryButton.textContent='继续安装';workflows.append(retryButton);
  action('workflowRetry',()=>request('/v1/workflows/retry',{job_id:el('workflowRetryJob').value.trim()}));
  load().catch(e=>notice(e.message,true));
})();
