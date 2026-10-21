/* Local discovery sessions; all private preferences remain on this node. */
let coordSession = null, coordWatch = 0;
function rememberCoordSession(sid){try{if(sid)localStorage.setItem('a2n.coord.search',sid);else localStorage.removeItem('a2n.coord.search')}catch(_){}}
const coordStates = {RUNNING:'coord.state.RUNNING',SATISFIED:'coord.state.SATISFIED',PAUSED:'coord.state.PAUSED',
  BUDGET_REACHED:'coord.state.BUDGET_REACHED',FRONTIER_EXHAUSTED:'coord.state.FRONTIER_EXHAUSTED',ISOLATED:'coord.state.ISOLATED',
  CANCELLED:'coord.state.CANCELLED',EXPIRED:'coord.state.EXPIRED'};
function renderCoordSession(){
  const bar=$('#coordSession'); if(!coordSession){bar.hidden=true;return}
  bar.hidden=false;
  const s=coordSession,u=s.budget_used||{},r=s.budget_remaining||{};
  $('#coordProgress').textContent=I18N.t('coord.progress',{state:I18N.text(coordStates[s.state])||s.state,round:s.round,candidates:s.candidate_count,frontier:s.frontier_count,used:u.remote_operations||0,remaining:r.remote_operations||0});
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
      if(items.length>4096)throw Error(I18N.t('coord.too_many_candidates'));
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
    notice(I18N.t('coord.no_channel'),true);
  else if(failures && pool.length)
    notice(I18N.t('coord.found_partial',{count:pool.length,failures}));
  else if(failures)
    notice(I18N.t('coord.found_nothing',{skill,failures}),true);
  else notice(I18N.t('coord.found',{count:pool.length}));
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
  $('#publicState').textContent=I18N.t('coord.public_state')+
    (p.directory_available?I18N.t('coord.directory_entry',{url:p.directory_url}):I18N.t('coord.directory_fallback'));
  $('#publicSwitches').innerHTML='<span>'+I18N.t('coord.samples_always')+'</span> '+[['witness','coord.service.witness'],['task_relay','coord.service.task_relay'],['blob_cache','coord.service.blob_cache']]
    .map(([key,msg])=>`<label style="margin-right:18px"><input type="checkbox" data-public-service="${key}" ${services[key]?'checked':''}> ${I18N.t(msg)}</label>`).join('');
};
$('#publicSwitches').addEventListener('change',async e=>{
  const input=e.target.closest('[data-public-service]');if(!input)return;
  input.disabled=true;
  try{await request('/v1/public-services',{services:{[input.dataset.publicService]:input.checked}});await refresh();notice(I18N.t('coord.services_saved'))}
  catch(err){input.checked=!input.checked;notice(err.message,true)}finally{input.disabled=false}
});

const previousFoundDetail=showFoundDetail;
showFoundDetail=function(index){
  previousFoundDetail(index);
  const item=found[index];if(!item?.search_id)return;
  const box=$('#detailExtra');
  box.insertAdjacentHTML('beforeend',`<p>${I18N.t('coord.routes_merged',{count:item.routes?.length||0})}</p><button class="ghost" data-coord-plan="${index}">${I18N.t('coord.view_routes')}</button> <button class="ghost" data-coord-plan="${index}" data-probe-coord="true">${I18N.t('coord.probe_entry')}</button><div id="coordRoutes"></div>`);
};
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-coord-plan]');if(!button)return;
  button.disabled=true;
  try{
    const item=found[Number(button.dataset.coordPlan)];if(!item?.search_id)throw Error(I18N.t('coord.reselect'));
    const s=await request(`/v1/coord/searches/${item.search_id}`);
    const plan=await request(`/v1/coord/searches/${item.search_id}/route-plan`,{key:item.key,probe:button.dataset.probeCoord==='true'},
      {'Idempotency-Key':crypto.randomUUID(),'If-Match':`"${s.revision}"`});
    const routes=$('#coordRoutes');if(!routes)return;
    routes.innerHTML=plan.choices.length?plan.choices.map(r=>{
      const o=r.observation||{},label=I18N.text(r.channel_type==='direct_a2a'?'coord.channel.direct':'coord.channel.relay');
      return `<p><label><input type="radio" name="coordRoute" value="${esc(r.route_id)}" ${r.route_id===(item.selected_route_id||plan.preferred_route_id)?'checked':''}> ${label} · ${I18N.text(o.control_reachable===true?'coord.reach.verified':o.control_reachable===false?'coord.reach.unreachable':'coord.reach.unchecked')}${o.rtt_ms!=null?' · '+o.rtt_ms+' ms':''}</label></p>`;
    }).join('')+'<p class="sub">'+I18N.t('coord.route_note')+'</p>':I18N.t('coord.plan_expired');
    item.selected_route_id=plan.preferred_route_id;
    routes.onchange=event=>{item.selected_route_id=event.target.value};
  }catch(err){notice(err.message,true)}finally{button.disabled=false}
});
renderCoordSession();renderPublic();
try{const sid=localStorage.getItem('a2n.coord.search');if(sid){coordSession={search_id:sid};watchCoordSession(sid)}}catch(_){}
