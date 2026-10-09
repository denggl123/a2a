/* Local discovery sessions; all private preferences remain on this node. */
let coordSession = null, coordWatch = 0;
function rememberCoordSession(sid){try{if(sid)localStorage.setItem('a2n.coord.search',sid);else localStorage.removeItem('a2n.coord.search')}catch(_){}}
const coordStates = {RUNNING:'正在发现',SATISFIED:'已达到本次满意条件',PAUSED:'已暂停',
  BUDGET_REACHED:'本轮额度已用完',FRONTIER_EXHAUSTED:'已查完当前已知范围',ISOLATED:'尚无可用邻居',
  CANCELLED:'已取消',EXPIRED:'会话已过期'};
function renderCoordSession(){
  const bar=$('#coordSession'); if(!coordSession){bar.hidden=true;return}
  bar.hidden=false;
  const s=coordSession,u=s.budget_used||{},r=s.budget_remaining||{};
  $('#coordProgress').textContent=`${coordStates[s.state]||s.state} · 第 ${s.round} 轮 · ${s.candidate_count} 个商品 · 待查 ${s.frontier_count} 项 · 本轮请求 ${u.remote_operations||0} 次，剩余 ${r.remote_operations||0} 次`;
  $('#coordPause').disabled=s.state!=='RUNNING';
  $('#coordContinue').disabled=['RUNNING','CANCELLED','EXPIRED'].includes(s.state);
}
async function loadCoordSession(sid){
  const generation=coordWatch;
  for(let attempt=0;attempt<3;attempt++){
    const s=await request(`/v1/coord/searches/${encodeURIComponent(sid)}`);
    let cursor='',revision=null,items=[],errors=[],changed=false;
    do{
      let page;
      try{page=await request(`/v1/coord/searches/${encodeURIComponent(sid)}/candidates?limit=100${cursor?'&result_cursor='+encodeURIComponent(cursor):''}`)}
      catch(e){if(/RESULT_CHANGED/.test(e.message)){changed=true;break}throw e}
      if(revision!==null&&revision!==page.result_revision){changed=true;break}
      revision=page.result_revision;items.push(...(page.items||[]));errors=page.errors||[];
      cursor=page.next_result_cursor||'';
      if(items.length>4096)throw Error('候选过多，请缩小本次发现范围');
    }while(cursor);
    if(changed)continue;
    if(coordSession?.search_id!==sid||generation!==coordWatch)return;
    coordSession={...s,result_revision:revision};
    pool=items.filter(i=>i.cards.length).map(i=>({card:i.cards.at(-1).card,key:i.key,
      routes:i.routes,sources:i.sources,verification:i.verification,source:'coordination',headers:{},search_id:sid}));
    $('#searchErrors').textContent=errors.map(e=>e.error).join('；');
    applyDisc();renderCoordSession();
    document.dispatchEvent(new CustomEvent('a2n:candidates',{detail:{search_id:sid,result_revision:revision,state:s.state}}));
    return coordSession;
  }
  return coordSession; // keep the previous complete pool during changing pages
}
async function watchCoordSession(sid){
  const generation=++coordWatch;
  try{while(generation===coordWatch){const s=await loadCoordSession(sid);if(!s||s.state!=='RUNNING')break;
    await new Promise(resolve=>setTimeout(resolve,400));}}
  catch(e){notice(e.message,true)}
}
async function searchCoordination(skill){
  ++coordWatch;
  if(coordSession?.state==='RUNNING'){
    const previous=await request(`/v1/coord/searches/${coordSession.search_id}`);
    if(previous.state==='RUNNING')await request(`/v1/coord/searches/${previous.search_id}/pause`,{},
      {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${previous.revision}"`});
  }
  coordSession=await request('/v1/coord/searches',{skill,preferences:{min_candidates:3}},
    {'Idempotency-Key':crypto.randomUUID()});
  rememberCoordSession(coordSession.search_id);
  renderCoordSession();await watchCoordSession(coordSession.search_id);
  /* 「没找到」有两种完全不同的原因，界面不许混成同一句话：
     ISOLATED = 一个发现通道都没配，这轮**没向任何节点发问**；
     其余状态 = 问过了，失败原因逐条列在 #searchErrors 里。 */
  const failures=($('#searchErrors').textContent||'').trim();
  if(coordSession?.state==='ISOLATED')
    notice('本机没有配置任何发现通道，这一轮没有向任何节点发问。可在「我的账户 · 连接节点」里添加 P2P 种子或公共节点地址。',true);
  else if(failures && pool.length)
    notice(`已发现 ${pool.length} 个商品；部分来源查询失败：${failures}`);
  else if(failures)
    notice(`「${skill}」已向外发出查询，但没能取回可核验的商品：${failures}`,true);
  else notice(`已发现 ${pool.length} 个商品；需要更多时可以继续发现。`);
}
async function controlCoordSession(action){
  if(!coordSession)return;
  try{
    const s=await request(`/v1/coord/searches/${coordSession.search_id}`);
    const body=action==='resume'?{preferences:{min_candidates:Math.min(256,s.candidate_count+3)}}:{};
    coordSession=await request(`/v1/coord/searches/${s.search_id}/${action}`,body,
      {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${s.revision}"`});
    renderCoordSession();await watchCoordSession(s.search_id);
  }catch(e){notice(e.message,true)}
}
$('#coordPause').onclick=()=>controlCoordSession('pause');
$('#coordContinue').onclick=()=>controlCoordSession('resume');

const previousRenderPublic=renderPublic;
renderPublic=function(){
  previousRenderPublic();
  const p=snapshot.public_service||{},services=p.services||{};
  $('#publicState').textContent='基础协调发现和免费交付样品始终公开，不能关闭。哈希见证、任务中继和加密文件转送由你分别选择。'+
    (p.directory_available?` 对外入口：${p.directory_url}`:' 可连接公共节点，通过出站协调邮箱参与发现。');
  $('#publicSwitches').innerHTML='<span>公开交付样品：始终开启</span> '+[['witness','提供哈希见证'],['task_relay','提供密封任务中继'],['blob_cache','提供有额度的加密文件转送']]
    .map(([key,label])=>`<label style="margin-right:18px"><input type="checkbox" data-public-service="${key}" ${services[key]?'checked':''}> ${label}</label>`).join('');
};
$('#publicSwitches').addEventListener('change',async e=>{
  const input=e.target.closest('[data-public-service]');if(!input)return;
  input.disabled=true;
  try{await request('/v1/public-services',{services:{[input.dataset.publicService]:input.checked}});await refresh();notice('公共服务设置已保存。')}
  catch(err){input.checked=!input.checked;notice(err.message,true)}finally{input.disabled=false}
});

const previousFoundDetail=showFoundDetail;
showFoundDetail=function(index){
  previousFoundDetail(index);
  const item=found[index];if(!item?.search_id)return;
  const box=$('#detailExtra');
  box.insertAdjacentHTML('beforeend',`<p>已归并 ${item.routes?.length||0} 条已验证通道。</p><button class="ghost" data-coord-plan="${index}">查看通道</button> <button class="ghost" data-coord-plan="${index}" data-probe-coord="true">检查入口可达性</button><div id="coordRoutes"></div>`);
};
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-coord-plan]');if(!button)return;
  button.disabled=true;
  try{
    const item=found[Number(button.dataset.coordPlan)];if(!item?.search_id)throw Error('请重新选择商品');
    const s=await request(`/v1/coord/searches/${item.search_id}`);
    const plan=await request(`/v1/coord/searches/${item.search_id}/route-plan`,{key:item.key,probe:button.dataset.probeCoord==='true'},
      {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${s.revision}"`});
    const routes=$('#coordRoutes');if(!routes)return;
    routes.innerHTML=plan.choices.length?plan.choices.map(r=>{
      const o=r.observation||{},label=r.channel_type==='direct_a2a'?'直连':'密封中继';
      return `<p><label><input type="radio" name="coordRoute" value="${esc(r.route_id)}" ${r.route_id===(item.selected_route_id||plan.preferred_route_id)?'checked':''}> ${label} · ${o.control_reachable===true?'入口已核验':o.control_reachable===false?'入口暂不可达':'尚未检查'}${o.rtt_ms!=null?' · '+o.rtt_ms+' ms':''}</label></p>`;
    }).join('')+'<p class="sub">此检查只读取签名商品卡，不执行 Agent。实际调用仍可能失败。</p>':'通道声明已过期，请继续发现。';
    item.selected_route_id=plan.preferred_route_id;
    routes.onchange=event=>{item.selected_route_id=event.target.value};
  }catch(err){notice(err.message,true)}finally{button.disabled=false}
});
renderCoordSession();renderPublic();
try{const sid=localStorage.getItem('a2n.coord.search');if(sid){coordSession={search_id:sid};watchCoordSession(sid)}}catch(_){}
