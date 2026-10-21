/* First-use checklist derives from the node's actual state. */
(() => {
  'use strict';
  const page=document.getElementById('find');if(!page)return;
  const box=document.createElement('section');box.className='panel';box.id='firstUse';
  box.innerHTML='<h2>'+I18N.t('onboarding.heading')+'</h2><p class="sub">'+I18N.t('onboarding.intro')+'</p>\n    <ol id="firstUseSteps"></ol><label>'+I18N.t('onboarding.address')+'<input id="firstUseAddress" placeholder="'+I18N.t('onboarding.address_placeholder')+'" autocomplete="url"></label>\n    <button id="firstUseConnect">'+I18N.t('onboarding.connect_btn')+'</button><p id="firstUseConnection" role="status"></p>\n    <button class="ghost" id="firstUseFind">'+I18N.t('onboarding.find_btn')+'</button><button class="ghost" id="firstUseSell">'+I18N.t('onboarding.sell_btn')+'</button><button class="ghost" id="firstUseAccount">'+I18N.t('onboarding.account_btn')+'</button>';
  page.prepend(box);const el=id=>document.getElementById(id);
  async function load(){
    const status=await request('/v1/onboarding'),items=[
      [true,I18N.t('onboarding.step_identity')],
      [status.neighbors>0,I18N.t('onboarding.step_neighbors',{count:status.neighbors})],
      [status.outbound_settlement||status.public_nodes.length>0,status.outbound_settlement?I18N.t('onboarding.step_outbound'):I18N.t('onboarding.step_public_nodes',{count:status.public_nodes.length})],
      [status.imported>0,I18N.t('onboarding.step_imported',{count:status.imported})],
      [true,I18N.t('onboarding.reputation_prefix')+(I18N.text('onboarding.reputation.'+status.reputation_mode)||status.reputation_mode)]];
    el('firstUseSteps').replaceChildren(...items.map(([done,text])=>{const li=document.createElement('li');li.textContent=(done?'✓ ':I18N.t('onboarding.step_pending'))+text;return li}));
    if(!el('firstUseAddress').value&&status.public_nodes[0])el('firstUseAddress').value=status.public_nodes[0];
  }
  el('firstUseConnect').onclick=async()=>{const b=el('firstUseConnect');b.disabled=true;el('firstUseConnection').textContent=I18N.t('onboarding.verifying');try{const out=await request('/v1/onboarding/connect',{address:el('firstUseAddress').value.trim()});el('firstUseConnection').textContent=out.notice;await load();await refresh()}catch(e){el('firstUseConnection').textContent=I18N.t('onboarding.not_connected',{message:e.message})}finally{b.disabled=false}};
  el('firstUseFind').onclick=()=>{document.querySelector('[data-page="find"]')?.click();const input=el('skill');input.scrollIntoView({behavior:'smooth'});input.focus()};
  el('firstUseSell').onclick=()=>{document.querySelector('[data-page="sell"]')?.click();const panel=el('mount');panel.open=true;panel.scrollIntoView({behavior:'smooth'})};
  el('firstUseAccount').onclick=()=>document.querySelector('[data-page="account"]')?.click();
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.page==='find')load().catch(e=>notice(e.message,true))}));
  load().catch(()=>{});setInterval(()=>{if(!document.hidden&&page.classList.contains('on'))load().catch(()=>{})},5000);
})();
