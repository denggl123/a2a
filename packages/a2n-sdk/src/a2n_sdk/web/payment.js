/* Explicit owner payment choices; decimal amounts never pass through Number. */
(() => {
  'use strict';
  const account=document.getElementById('account'),buy=document.getElementById('find');
  if(!account||!buy)return;
  const el=id=>document.getElementById(id),safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function section(parent,html){const s=document.createElement('section');s.className='panel';s.innerHTML=html;parent.append(s)}
  section(account,`<h2>对等支付设置</h2><p class="sub">平台手续费为零。买卖双方使用自己的钱包；自动结算需要你先设置方式、顺序和额度。付款与交付、质量评价、退款分别记录。</p>
    <p id="paymentWallet">尚未配置钱包</p><label>导入加密钱包文件<input id="paymentKeystore" type="file" accept=".json,application/json"></label>
    <label>钱包文件口令<input id="paymentPassword" type="password" autocomplete="off"></label>
    <details><summary>支付通道配置</summary><p class="sub">已有未确认付款时，系统会保留原通道用于核对。首次配置可使用测试网络。</p>
    <label><input id="paymentNative" type="checkbox">直接链上转账</label><label>链编号<input id="paymentChain" inputmode="numeric" placeholder="例如 84532"></label>
    <label>链节点地址<input id="paymentRPC" placeholder="https://…"></label><label>原生币名称<input id="paymentCurrency" value="ETH" maxlength="12"></label>
    <label><input id="paymentX402" type="checkbox">x402 授权付款</label><label>代币名称<input id="paymentTokenCurrency" value="USDC" maxlength="12"></label>
    <label>代币合约地址<input id="paymentToken" placeholder="0x…"></label><label>代币签名名称<input id="paymentDomain" placeholder="USDC"></label><label>代币签名版本<input id="paymentDomainVersion" value="2"></label>
    <label>结算服务地址（提供 x402 收款时需要）<input id="paymentFacilitator" placeholder="https://…"></label>
    <label>等待确认区块数<input id="paymentConfirmations" value="3" type="number" min="1" max="64"></label>
    <label><input id="paymentHTTP" type="checkbox">允许本地测试使用 HTTP</label></details>
    <button id="paymentConfigure">保存钱包与通道</button><p class="sub" id="paymentConfigured"></p>
    <h3>我允许的付款额度</h3><p class="sub">未设置额度时不会付款。直接转账额度包含你批准的网络费上限。这里填写币种最小单位整数（ETH 为 wei，USDC 为百万分之一币）。</p>
    <label>币种<input id="paymentBudgetCurrency" value="ETH"></label><label>单笔上限<input id="paymentPerTrade" inputmode="numeric" value="0"></label>
    <label>同时未确定的总额度<input id="paymentExposure" inputmode="numeric" value="0"></label><label>每日上限<input id="paymentDaily" inputmode="numeric" value="0"></label>
    <label>同一交易对方上限<input id="paymentCounterparty" inputmode="numeric" value="0"></label><button id="paymentBudget">保存额度</button>
    <h3>支付订单</h3><button class="ghost" id="paymentRefresh">刷新付款事实</button><div id="paymentProposals"></div><div id="paymentOrders"></div>
    <h3>收到的交易约定</h3><div id="paymentIncoming"></div>
    <h3>执行已达成的退款</h3><p class="sub">双方先接受退款方案，再由卖方明确执行逆向付款。直接转账退回原生币；x402 通过独立代币授权退回原币种。退款不会自动改变质量评价。</p>
    <label>双方已接受的退款方案<select id="paymentRefundProposal"></select></label><label>退款网络费上限（最小单位整数）<input id="paymentRefundFee" inputmode="numeric" value="0"></label>
    <button id="paymentRefund">明确执行退款</button><button class="ghost" id="paymentRefundQuery">核对原退款</button><p id="paymentRefundState"></p>`);
  section(buy,`<h2>按双方约定调用 Agent</h2><p class="sub">先向卖方询价，再选择双方都支持的付款方式。免费服务不会生成付款；前 10 次免费技术交付一定公开为脱敏样品。</p>
    <label>待使用的 Agent<select id="paymentAgent"></select></label><label>要使用的能力<select id="paymentSkill"></select></label><div id="paymentEditor"></div>
    <button id="paymentQuote">取得本次交易条件</button><div id="paymentOffer"></div>
    <label>选择支付方式<select id="paymentOption" disabled></select></label><label>允许的网络费上限（最小单位整数，x402 填 0）<input id="paymentFee" inputmode="numeric" value="0"></label>
    <button id="paymentApprove" disabled>明确接受这一份交易条件</button><button id="paymentFree" disabled>明确调用免费服务</button>
    <p id="paymentPlanState"></p><details id="paymentFixedInput" hidden><summary>本交易固定的输入</summary><pre></pre></details><button id="paymentPay" disabled>明确支付这笔转账</button><button id="paymentExecute" disabled>调用已约定的服务</button><div id="paymentCallResult" aria-live="polite"></div>`);
  section(account,`<h3>结算方式与顺序</h3><p class="sub">供应方设置收款支持列表，使用方设置付款方式及优先顺序。只匹配双方实际可用的方式。前十次样品完全跳过结算。</p>
    <div id="settlementEditor"></div>
    <details><summary>编辑结算规则</summary><label>供应方支持列表（null 沿用现有通道，[] 停用收款）<textarea id="settlementProvider">null</textarea></label>
    <label>使用方优先顺序（按从上到下排列）<textarea id="settlementBuyer">null</textarea></label>
    <p class="sub">例如：[{"method":"a2n-points/1","mode":"EARN","max_amount_minor":"100"},{"method":"x402/2","currency":"USDC","max_amount_minor":"1000000"}]。金额为最小单位整数文字；直接转账另设 fee_cap_minor。积分 PAY 可指定 funding_issuer 和 max_cost。</p></details>
    <label><input type="checkbox" id="settlementAutomatic">授权在规则和已配置额度内自动结算</label><button id="settlementSave">保存结算规则</button><p id="settlementState"></p>`);
  const autoButton=document.createElement('button');autoButton.id='paymentAutomatic';autoButton.textContent='按我的顺序自动匹配并调用';autoButton.disabled=true;el('paymentQuote').after(autoButton);
  const policyEditor=window.A2NSettlementUI.mount(el('settlementEditor'));
  for(const id of ['settlementProvider','settlementBuyer'])el(id).onchange=()=>{try{policyEditor.set({provider_methods:JSON.parse(el('settlementProvider').value),buyer_preferences:JSON.parse(el('settlementBuyer').value)})}catch(e){notice('结算规则格式错误：'+e.message,true)}};
  let quote=null,plan=null,budgetRevision=0,settlementRevision=0,automaticCommand=null;
  let inputCard=null,inputScope='',inputLoad=0,inputRevision=0;
  function invalidateInput(){inputRevision++;quote=null;plan=null;automaticCommand=null;for(const id of ['paymentFree','paymentApprove','paymentPay','paymentExecute','paymentAutomatic'])el(id).disabled=true;el('paymentOffer').textContent='输入已变化，请重新取得交易条件。既有订单保留在账户页。';el('paymentFixedInput').hidden=true;el('paymentPlanState').textContent='';}
  const editor=window.A2NForms.editor(el('paymentEditor'),invalidateInput);
  async function loadEditor(){const scope=el('paymentAgent').value;if(scope===inputScope)return;const seq=++inputLoad;invalidateInput();if(!scope){inputScope='';inputCard=null;editor.setCard({});el('paymentSkill').replaceChildren();return}
    const card=await request('/a2a/'+encodeURIComponent(scope)+'/.well-known/agent.json');if(seq!==inputLoad||scope!==el('paymentAgent').value)return;
    inputScope=scope;inputCard=card;el('paymentSkill').innerHTML=(card.skills||[]).map(s=>`<option value="${safe(s.id)}">${safe(s.name||s.id)}</option>`).join('');editor.setCard(card,el('paymentSkill').value);
  }
  el('paymentAgent').onchange=()=>loadEditor().catch(e=>notice(e.message,true));el('paymentSkill').onchange=()=>{invalidateInput();editor.setCard(inputCard,el('paymentSkill').value)};
  const PLAN_PREFIX='pp_';
  const labels={READY:'等待明确付款',UNKNOWN:'结果未知 · 原订单已锁定',CONFIRMED:'付款已确认',FAILED:'确定失败或付款前取消',NOT_REQUIRED:'本次无需付款'};
  function showResult(out){window.A2NForms.renderOutcome(el('paymentCallResult'),out)}
  document.addEventListener('a2n:progress',e=>{const box=el('paymentCallResult');let preview=box.querySelector('[data-stream-preview]');if(!preview){preview=document.createElement('pre');preview.dataset.streamPreview='1';box.replaceChildren(preview)}preview.textContent=(preview.textContent+e.detail.delta).slice(-20000)});
  function selectPlan(row){plan=row;const p=row.record;el('paymentPlanState').textContent=`已接受：${p.terms.amount_minor_decimal||p.terms.amount_minor} ${p.terms.currency}（最小单位），收款方 ${p.terms.payee}，平台手续费 0。`;
    el('paymentFixedInput').hidden=false;el('paymentFixedInput').querySelector('pre').textContent=JSON.stringify(row.request?.payload??'原输入保存在调用记录中',null,2);
    if(p.terms.method==='a2n-points/1'){const modes={EARN:'你本次不用支付；交付后供应方增加自己的积分。',DEBT:'你本次记欠账；交付后供应方增加自己的积分。',PAY:`本次使用 ${p.funding_cost_decimal} 个 ${p.operations[0].issuer_did} 发行的积分。`};el('paymentPlanState').textContent=modes[p.terms.mode]+' 各方已预留，尚未执行服务。';el('paymentPay').disabled=true;el('paymentExecute').disabled=row.state!=='PREPARED';el('paymentExecute').textContent='明确调用服务并按原条件结算';return}
    el('paymentPay').disabled=p.terms.flow!=='upfront';el('paymentExecute').disabled=p.terms.flow==='upfront';el('paymentExecute').textContent=p.terms.flow==='authorization'?'明确授权付款并调用 Agent':'调用已付款的 Agent'}
  async function run(button,fn){button.disabled=true;try{await fn()}catch(e){notice(e.message,true)}finally{button.disabled=['paymentFree','paymentApprove','paymentAutomatic'].includes(button.id)?!quote:['paymentPay','paymentExecute'].includes(button.id)?!plan:false}}
  async function load(){const [status,runtime,risk]=await Promise.all([request('/v1/payment-coordination'),request('/v1/runtime'),request('/v1/risk')]);
    budgetRevision=risk.policy.revision;
    if(status.settlement_policy){const p=status.settlement_policy;settlementRevision=p.revision;policyEditor.set(p);el('settlementProvider').value=JSON.stringify(p.provider_methods,null,2);el('settlementBuyer').value=JSON.stringify(p.buyer_preferences,null,2);el('settlementAutomatic').checked=p.automatic;el('settlementState').textContent=p.automatic?'已授权按使用方顺序自动匹配；未知付款只核对原单。':'当前使用逐笔确认。'}
    el('paymentWallet').textContent=status.wallet_address?'本节点钱包：'+status.wallet_address:'尚未配置链上钱包；积分服务可单独使用';
    el('paymentConfigured').textContent=`直接转账通道 ${status.native.length} 个；x402 ${status.x402.state==='CONFIGURED'?'可付款':'未配置'}；x402 收款 ${status.provider_x402?'已配置':'未配置'}。旧交易保留原通道，新设置用于新交易。`;
    const chosen=el('paymentAgent').value;
    el('paymentAgent').innerHTML=(runtime.projections||[]).map(p=>`<option value="${safe(p.projection_id)}">${safe(p.name||p.projection_id)}</option>`).join('')||'<option value="">先把一个 Agent 加入待使用</option>';
    if([...el('paymentAgent').options].some(o=>o.value===chosen))el('paymentAgent').value=chosen;
    await loadEditor();
    el('paymentProposals').innerHTML=(status.proposals||[]).map(p=>`<article class="agent"><b>交易条件确认中断 · 尚未付款</b><p>${safe(p.amount_minor_decimal)} ${safe(p.currency)}（最小单位）</p><button data-payment-plan="${safe(p.plan_id)}" data-payment-action="accept">恢复原条件的双方确认</button></article>`).join('');
    el('paymentOrders').innerHTML=status.orders.slice().reverse().map(o=>{const p=o.attempts.at(-1),id=(p.plan_id||String()).startsWith(PLAN_PREFIX)?p.plan_id:null;return `<article class="agent"><b>${safe(p.state==='READY'&&p.new_payment_allowed===false?'付款期限已过 · 可取消原计划':labels[o.state]||o.state)}</b><p>${safe(p.amount_minor_decimal||p.amount_minor)} ${safe(p.currency)}（最小单位） · ${safe(o.trade_uid.slice(-12))} · 尝试 ${o.attempts.length} 次</p><p class="sub">${safe(p.reference||'尚无链上交易标识')}</p>${id?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="reconcile">核对原付款与交付</button>${p.state==='READY'?`${p.new_payment_allowed!==false?`<button data-payment-plan="${safe(id)}" data-payment-action="${p.payment_flow==='upfront'?'pay':'execute'}">${p.payment_flow==='upfront'?'明确支付原转账':'明确授权付款并调用 Agent'}</button>`:''}<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="cancel">付款前取消</button>`:''}${p.state==='CONFIRMED'?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="execute">读取或继续约定调用</button>`:''}`:''}</article>`}).join('')||'<p class="sub">尚无付款订单。</p>';
    el('paymentIncoming').innerHTML=(status.incoming||[]).slice().reverse().map(p=>`<article class="agent"><b>${safe(p.authorization_state==='EXPIRED'?'付款接受期限已过 · 原付款事实仍可核对':labels[p.state]||({ACCEPTED:'双方已接受 · 等待付款',REVOKED:'付款前已撤销'}[p.state])||p.state)}</b><p>${safe(p.amount_minor_decimal)} ${safe(p.currency)}（最小单位）</p><p class="sub">买方 ${safe(p.buyer_did)} · 原钱包及通道已保留</p></article>`).join('')||'<p class="sub">尚无收到的收费交易约定。</p>';
    el('paymentRefundProposal').innerHTML=(runtime.resolution_agreements||[]).filter(a=>a.state==='BOTH_ACCEPTED'&&a.proposal.body.action==='REFUND').map(a=>`<option value="${safe(a.proposal.message_id)}">${safe(a.amount_minor_decimal||a.proposal.body.amount_minor)} ${safe(a.proposal.body.currency)} · ${safe(a.execution)}</option>`).join('')||'<option value="">尚无双方已接受的退款方案</option>';
  }
  el('paymentConfigure').onclick=e=>run(e.currentTarget,async()=>{const chain='eip155:'+el('paymentChain').value.trim(),rpc=el('paymentRPC').value.trim(),confirmations=Number(el('paymentConfirmations').value),allow_http=el('paymentHTTP').checked;
    const config={native:[],allow_http};if(el('paymentNative').checked)config.native.push({currency:el('paymentCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,allow_http});
    if(el('paymentX402').checked)config.x402={allow_http,assets:[{currency:el('paymentTokenCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,asset:el('paymentToken').value.trim(),name:el('paymentDomain').value.trim(),version:el('paymentDomainVersion').value.trim()}]};
    if(el('paymentFacilitator').value.trim())config.facilitator_url=el('paymentFacilitator').value.trim();
    const body={config},file=el('paymentKeystore').files[0];if(file){body.keystore=JSON.parse(await file.text());body.password=el('paymentPassword').value}
    await request('/v1/payment-coordination/configure',body);el('paymentPassword').value='';el('paymentKeystore').value='';await load();notice('支付通道已保存；结算按你的授权规则执行。')});
  el('settlementSave').onclick=e=>run(e.currentTarget,async()=>{await request('/v1/payment-coordination/policy',{expected_revision:settlementRevision,...policyEditor.get(),automatic:el('settlementAutomatic').checked});await load();el('paymentAutomatic').disabled=true;notice('收款支持方式和付款优先顺序已保存，请重新询价。')});
  el('paymentBudget').onclick=e=>run(e.currentTarget,async()=>{await request('/v1/payment-coordination/budget',{currency:el('paymentBudgetCurrency').value.trim().toUpperCase(),per_trade:el('paymentPerTrade').value,total_exposure:el('paymentExposure').value,daily_spend:el('paymentDaily').value,per_counterparty:el('paymentCounterparty').value,expected_revision:budgetRevision});await load();notice('付款额度已保存。')});
  el('paymentRefresh').onclick=e=>run(e.currentTarget,load);
  el('paymentQuote').onclick=e=>run(e.currentTarget,async()=>{await loadEditor();if(!inputScope)throw Error('先发现并加入一个 Agent。');const input=editor.get(),revision=inputRevision;
    const prepared={task_id:'pay_'+crypto.randomUUID(),skill:el('paymentSkill').value,payload:input};
    if(window.a2nTaskReview)await window.a2nTaskReview.prepare(el('paymentAgent').value,prepared);
    const offered=await request('/v1/trades/quote',{projection_id:el('paymentAgent').value,request:prepared});if(revision!==inputRevision)return;quote=offered;plan=null;
    if(window.a2nSelection)try{await window.a2nSelection.confirmChoice(el('paymentAgent').value,prepared,inputCard)}catch(err){notice('选用统计未关联：'+err.message,true)}
    el('paymentOffer').textContent=quote.payment_state==='NOT_REQUIRED'?(quote.free_reason==='FREE_INITIAL'?'本次处于前 10 次免费服务；技术交付将公开为脱敏样品。':'本次无需支付；公开范围依服务的样品规则执行。'):quote.options.length?'卖方已签名给出以下条件；金额与收款方在接受后固定。':'双方没有可用的共同支付方式；尚未付款，也未执行服务。';
    el('paymentOption').innerHTML=quote.options.map((o,i)=>`<option value="${i}">${safe(o.method==='a2n-points/1'?({EARN:'免费服务，供应方获得自家积分',DEBT:'使用方欠账，供应方获得自家积分',PAY:'用已有积分兑换服务'}[o.mode]):o.method==='x402/2'?'x402 授权付款':'直接链上转账')} · ${safe(o.amount_minor_decimal||o.amount_minor)} ${safe(o.method==='a2n-points/1'?'供应方积分':o.currency)} · ${safe(o.method==='a2n-points/1'?'交付后按约定记账':o.flow==='upfront'?'先付款再调用':'授权后交付并结算')}</option>`).join('');
    const match=await request('/v1/trades/match',{offer_id:quote.offer_id});if(revision!==inputRevision)return;if(match.option_index!==null)el('paymentOption').value=String(match.option_index);automaticCommand=crypto.randomUUID();el('paymentAutomatic').disabled=!(match.state==='NOT_REQUIRED'||match.automatic&&match.state==='MATCHED');
    el('paymentOption').disabled=!quote.options.length;el('paymentApprove').disabled=!quote.options.length;el('paymentFree').disabled=quote.payment_state!=='NOT_REQUIRED';el('paymentPay').disabled=el('paymentExecute').disabled=true;el('paymentPlanState').textContent=match.state==='MATCHED'?'已按你的顺序推荐结算方式，尚未接受收费条件。':'尚未接受收费条件。'});
  async function watchAutomatic(command,revision){
    for(let attempt=0;attempt<60;attempt++){
      if(command!==automaticCommand||revision!==inputRevision)return;
      try{
        const job=await request('/v1/trades/automatic?command_id='+encodeURIComponent(command));
        if(command!==automaticCommand||revision!==inputRevision||!job)return;
        el('paymentPlanState').textContent=(labels[job.state]||job.state||'原单准备中')+(job.finished?'':' · 后台核对原单中');
        if(job.finished){showResult(job.result?.delivery||job.result);await load();await refresh();return}
        if(job.action_required){el('paymentPlanState').textContent='原单等待授权处理；当前规则阻止继续付款。';return}
      }catch(_){el('paymentPlanState').textContent='暂时无法读取原单；后台保留恢复任务。'}
      await new Promise(resolve=>setTimeout(resolve,2000));
    }
    if(command===automaticCommand)el('paymentPlanState').textContent+=' · 可在账户页继续查看';
  }
  el('paymentAutomatic').onclick=e=>run(e.currentTarget,async()=>{if(!quote)throw Error('先取得本次交易条件。');if(quote.free_reason==='FREE_INITIAL'&&!confirm('本次免费技术交付一定公开为脱敏样品。继续调用？'))return;const revision=inputRevision,command=automaticCommand;const result=await request('/v1/trades/auto-execute',{offer_id:quote.offer_id,command_id:command});showResult(result.delivery||result);el('paymentPlanState').textContent=labels[result.state]||result.state;await load();await refresh();watchAutomatic(command,revision)});
  el('paymentApprove').onclick=e=>run(e.currentTarget,async()=>{const revision=inputRevision,body={offer_id:quote.offer_id,option_index:Number(el('paymentOption').value),fee_cap_minor:quote.options[Number(el('paymentOption').value)].method==='a2n-points/1'?'0':el('paymentFee').value,command_id:crypto.randomUUID()};if(el('pointsFundingIssuer')?.value)body.funding_issuer=el('pointsFundingIssuer').value;if(el('pointsMaxCost')?.value.trim())body.max_cost=el('pointsMaxCost').value.trim();const prepared=await request('/v1/trades/prepare',body);
    if(revision===inputRevision)selectPlan(prepared);else notice('输入已变化，原条件已经保存在账户订单中，请先核对该订单。');await load()});
  el('paymentFree').onclick=e=>run(e.currentTarget,async()=>{if(quote.free_reason==='FREE_INITIAL'&&!confirm('这是前 10 次免费服务。本次技术交付一定公开为脱敏样品；请确认输入中没有需要保密的业务内容。继续调用？'))return;showResult(await request('/v1/trades/free-execute',{offer_id:quote.offer_id},{Accept:'text/event-stream'}));await load();await refresh()});
  el('paymentPay').onclick=e=>run(e.currentTarget,async()=>{const result=await request('/v1/trades/plans/'+plan.record.plan_id+'/pay',{});el('paymentPlanState').textContent=labels[result.state]||result.state;el('paymentExecute').disabled=result.state!=='CONFIRMED';await load()});
  el('paymentExecute').onclick=e=>run(e.currentTarget,async()=>{showResult(await request('/v1/trades/plans/'+plan.record.plan_id+'/execute',{},{Accept:'text/event-stream'}));await load();await refresh()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-payment-plan]');if(!b)return;run(b,async()=>{const result=await request('/v1/trades/plans/'+b.dataset.paymentPlan+'/'+b.dataset.paymentAction,{});if(b.dataset.paymentAction==='accept')selectPlan(result);showResult(result);await load();await refresh()})});
  for(const [id,path] of [['paymentRefund','refund'],['paymentRefundQuery','refund-reconcile']])el(id).onclick=e=>run(e.currentTarget,async()=>{const out=await request('/v1/payment-coordination/'+path,{proposal_id:el('paymentRefundProposal').value,fee_cap_minor:el('paymentRefundFee').value});el('paymentRefundState').textContent='退款：'+(labels[out.state]||out.state)+(out.reference?' · '+out.reference:'');await load()});
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(['account','find'].includes(b.dataset.page))load().catch(e=>notice(e.message,true))}));
  load().catch(()=>{});
  window.A2NTradeUI={async open(scope){await load();el('paymentAgent').value=scope;await loadEditor();document.querySelector('[data-page="find"]')?.click();el('paymentAgent').scrollIntoView({behavior:'smooth'})}};
})();
