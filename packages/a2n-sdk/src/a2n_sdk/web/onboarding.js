/* First-use checklist derives from the node's actual state. */
(() => {
  'use strict';
  const page=document.getElementById('find');if(!page)return;
  const box=document.createElement('section');box.className='panel';box.id='firstUse';
  box.innerHTML=`<h2>开始使用这台节点</h2><p class="sub">每个在线节点都提供基础发现服务。第一次使用，先连接一个你信任的公共节点，再寻找 Agent。</p>
    <ol id="firstUseSteps"></ol><label>公共节点地址<input id="firstUseAddress" placeholder="https://节点地址" autocomplete="url"></label>
    <button id="firstUseConnect">核验并连接这个节点</button><p id="firstUseConnection" role="status"></p>
    <button class="ghost" id="firstUseFind">寻找 Agent</button><button class="ghost" id="firstUseSell">提供我的 Agent</button><button class="ghost" id="firstUseAccount">设置信誉与积分规则</button>`;
  page.prepend(box);const el=id=>document.getElementById(id);
  async function load(){
    const status=await request('/v1/onboarding'),items=[
      [true,'完整节点已经启动，身份与数据保存在本机。'],
      [status.neighbors>0,'已核验邻居 '+status.neighbors+' 个；基础发现与样品服务开启。'],
      [status.outbound_settlement||status.public_nodes.length>0,status.outbound_settlement?'出站结算通道已建立。':'已配置公共节点 '+status.public_nodes.length+' 个。'],
      [status.imported>0,'待使用 Agent '+status.imported+' 个；加入后可以查看样品和取得交易条件。'],
      [true,'信誉规则：'+({SHADOW:'观察并记录（可在账户页调整）',SUGGEST:'给出建议',ENFORCE_LOCAL:'按本机规则限制交易',DISABLED:'已关闭算法限制'}[status.reputation_mode]||status.reputation_mode)]];
    el('firstUseSteps').replaceChildren(...items.map(([done,text])=>{const li=document.createElement('li');li.textContent=(done?'✓ ':'待设置：')+text;return li}));
    if(!el('firstUseAddress').value&&status.public_nodes[0])el('firstUseAddress').value=status.public_nodes[0];
  }
  el('firstUseConnect').onclick=async()=>{const b=el('firstUseConnect');b.disabled=true;el('firstUseConnection').textContent='正在核验节点身份…';try{const out=await request('/v1/onboarding/connect',{address:el('firstUseAddress').value.trim()});el('firstUseConnection').textContent=out.notice;await load();await refresh()}catch(e){el('firstUseConnection').textContent='尚未连接：'+e.message}finally{b.disabled=false}};
  el('firstUseFind').onclick=()=>{document.querySelector('[data-page="find"]')?.click();const input=el('skill');input.scrollIntoView({behavior:'smooth'});input.focus()};
  el('firstUseSell').onclick=()=>{document.querySelector('[data-page="sell"]')?.click();const panel=el('mount');panel.open=true;panel.scrollIntoView({behavior:'smooth'})};
  el('firstUseAccount').onclick=()=>document.querySelector('[data-page="account"]')?.click();
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.page==='find')load().catch(e=>notice(e.message,true))}));
  load().catch(()=>{});setInterval(()=>{if(!document.hidden&&page.classList.contains('on'))load().catch(()=>{})},5000);
})();
