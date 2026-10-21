/* Explicit owner payment choices; decimal amounts never pass through Number. */
(() => {
  'use strict';
  const account=document.getElementById('account'),buy=document.getElementById('find');
  if(!account||!buy)return;
  const el=id=>document.getElementById(id),safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function section(parent,html){const s=document.createElement('section');s.className='panel';s.innerHTML=html;parent.append(s)}
  section(account,`<h2>${I18N.t('pay.heading')}</h2><p class="sub">${I18N.t('pay.intro')}</p>
    <p id="paymentWallet">${I18N.t('pay.wallet_none')}</p><label>${I18N.t('pay.keystore_label')}<input id="paymentKeystore" type="file" accept=".json,application/json"></label>
    <label>${I18N.t('pay.password_label')}<input id="paymentPassword" type="password" autocomplete="off"></label>
    <details><summary>${I18N.t('pay.channel_heading')}</summary><p class="sub">${I18N.t('pay.channel_note')}</p>
    <label><input id="paymentNative" type="checkbox">${I18N.t('pay.native_enable')}</label><label>${I18N.t('pay.chain_label')}<input id="paymentChain" inputmode="numeric" placeholder="${I18N.t('pay.chain_placeholder')}"></label>
    <label>${I18N.t('pay.rpc_label')}<input id="paymentRPC" placeholder="https://…"></label><label>${I18N.t('pay.currency_label')}<input id="paymentCurrency" value="ETH" maxlength="12"></label>
    <label><input id="paymentX402" type="checkbox">${I18N.t('pay.x402_enable')}</label><label>${I18N.t('pay.token_label')}<input id="paymentTokenCurrency" value="USDC" maxlength="12"></label>
    <label>${I18N.t('pay.token_address_label')}<input id="paymentToken" placeholder="0x…"></label><label>${I18N.t('pay.domain_label')}<input id="paymentDomain" placeholder="USDC"></label><label>${I18N.t('pay.domain_version_label')}<input id="paymentDomainVersion" value="2"></label>
    <label>${I18N.t('pay.facilitator_label')}<input id="paymentFacilitator" placeholder="https://…"></label>
    <label>${I18N.t('pay.confirmations_label')}<input id="paymentConfirmations" value="3" type="number" min="1" max="64"></label>
    <label><input id="paymentHTTP" type="checkbox">${I18N.t('pay.allow_http')}</label></details>
    <button id="paymentConfigure">${I18N.t('pay.configure_btn')}</button><p class="sub" id="paymentConfigured"></p>
    <h3>${I18N.t('pay.budget_heading')}</h3><p class="sub">${I18N.t('pay.budget_note')}</p>
    <label>${I18N.t('pay.budget_currency')}<input id="paymentBudgetCurrency" value="ETH"></label><label>${I18N.t('pay.budget_per_trade')}<input id="paymentPerTrade" inputmode="numeric" value="0"></label>
    <label>${I18N.t('pay.budget_exposure')}<input id="paymentExposure" inputmode="numeric" value="0"></label><label>${I18N.t('pay.budget_daily')}<input id="paymentDaily" inputmode="numeric" value="0"></label>
    <label>${I18N.t('pay.budget_counterparty')}<input id="paymentCounterparty" inputmode="numeric" value="0"></label><button id="paymentBudget">${I18N.t('pay.budget_save')}</button>
    <h3>${I18N.t('pay.orders_heading')}</h3><button class="ghost" id="paymentRefresh">${I18N.t('pay.refresh_btn')}</button><div id="paymentProposals"></div><div id="paymentOrders"></div>
    <h3>${I18N.t('pay.incoming_heading')}</h3><div id="paymentIncoming"></div>
    <h3>${I18N.t('pay.refund_heading')}</h3><p class="sub">${I18N.t('pay.refund_note')}</p>
    <label>${I18N.t('pay.refund_proposal_label')}<select id="paymentRefundProposal"></select></label><label>${I18N.t('pay.refund_fee_label')}<input id="paymentRefundFee" inputmode="numeric" value="0"></label>
    <button id="paymentRefund">${I18N.t('pay.refund_btn')}</button><button class="ghost" id="paymentRefundQuery">${I18N.t('pay.refund_query_btn')}</button><p id="paymentRefundState"></p>`);
  section(buy,`<h2>${I18N.t('pay.buy_heading')}</h2><p class="sub">${I18N.t('pay.buy_intro')}</p>
    <label>${I18N.t('pay.agent_label')}<select id="paymentAgent"></select></label><label>${I18N.t('pay.skill_label')}<select id="paymentSkill"></select></label><div id="paymentEditor"></div>
    <button id="paymentQuote">${I18N.t('pay.quote_btn')}</button><div id="paymentOffer"></div>
    <label>${I18N.t('pay.option_label')}<select id="paymentOption" disabled></select></label><label>${I18N.t('pay.fee_label')}<input id="paymentFee" inputmode="numeric" value="0"></label>
    <button id="paymentApprove" disabled>${I18N.t('pay.approve_btn')}</button><button id="paymentFree" disabled>${I18N.t('pay.free_btn')}</button>
    <p id="paymentPlanState"></p><details id="paymentFixedInput" hidden><summary>${I18N.t('pay.fixed_input_summary')}</summary><pre></pre></details><button id="paymentPay" disabled>${I18N.t('pay.pay_btn')}</button><button id="paymentExecute" disabled>${I18N.t('pay.execute_btn')}</button><div id="paymentCallResult" aria-live="polite"></div>`);
  section(account,`<h3>${I18N.t('pay.settlement_heading')}</h3><p class="sub">${I18N.t('pay.settlement_intro')}</p>
    <div id="settlementEditor"></div>
    <details><summary>${I18N.t('pay.settlement_edit')}</summary><label>${I18N.t('pay.settlement_provider')}<textarea id="settlementProvider">null</textarea></label>
    <label>${I18N.t('pay.settlement_buyer')}<textarea id="settlementBuyer">null</textarea></label>
    <p class="sub">${I18N.t('pay.settlement_example')}</p></details>
    <label><input type="checkbox" id="settlementAutomatic">${I18N.t('pay.settlement_auto')}</label><button id="settlementSave">${I18N.t('pay.settlement_save')}</button><p id="settlementState"></p>`);
  const autoButton=document.createElement('button');autoButton.id='paymentAutomatic';autoButton.textContent=I18N.t('pay.automatic_btn');autoButton.disabled=true;el('paymentQuote').after(autoButton);
  const policyEditor=window.A2NSettlementUI.mount(el('settlementEditor'));
  for(const id of ['settlementProvider','settlementBuyer'])el(id).onchange=()=>{try{policyEditor.set({provider_methods:JSON.parse(el('settlementProvider').value),buyer_preferences:JSON.parse(el('settlementBuyer').value)})}catch(e){notice(I18N.t('pay.policy_format_error',{error:e.message}),true)}};
  let quote=null,plan=null,budgetRevision=0,settlementRevision=0,automaticCommand=null;
  let inputCard=null,inputScope='',inputLoad=0,inputRevision=0;
  function invalidateInput(){inputRevision++;quote=null;plan=null;automaticCommand=null;for(const id of ['paymentFree','paymentApprove','paymentPay','paymentExecute','paymentAutomatic'])el(id).disabled=true;el('paymentOffer').textContent=I18N.t('pay.input_changed');el('paymentFixedInput').hidden=true;el('paymentPlanState').textContent='';}
  const editor=window.A2NForms.editor(el('paymentEditor'),invalidateInput);
  async function loadEditor(){const scope=el('paymentAgent').value;if(scope===inputScope)return;const seq=++inputLoad;invalidateInput();if(!scope){inputScope='';inputCard=null;editor.setCard({});el('paymentSkill').replaceChildren();return}
    const card=await request('/a2a/'+encodeURIComponent(scope)+'/.well-known/agent.json');if(seq!==inputLoad||scope!==el('paymentAgent').value)return;
    inputScope=scope;inputCard=card;el('paymentSkill').innerHTML=(card.skills||[]).map(s=>`<option value="${safe(s.id)}">${safe(s.name||s.id)}</option>`).join('');editor.setCard(card,el('paymentSkill').value);
  }
  el('paymentAgent').onchange=()=>loadEditor().catch(e=>notice(e.message,true));el('paymentSkill').onchange=()=>{invalidateInput();editor.setCard(inputCard,el('paymentSkill').value)};
  const PLAN_PREFIX='pp_';
  const stateLabel=s=>{const v=I18N.text('pay.state.'+s);return v===null?null:v};
  function planMode(m,record){switch(m){case 'EARN':return I18N.t('pay.plan_mode.EARN');case 'DEBT':return I18N.t('pay.plan_mode.DEBT');case 'PAY':return I18N.t('pay.plan_mode.PAY',{cost:record.funding_cost_decimal,issuer:record.operations[0].issuer_did});default:return undefined}}
  const optionMode=m=>m==='EARN'?I18N.t('pay.option_mode.EARN'):m==='DEBT'?I18N.t('pay.option_mode.DEBT'):m==='PAY'?I18N.t('pay.option_mode.PAY'):undefined;
  function showResult(out){window.A2NForms.renderOutcome(el('paymentCallResult'),out)}
  document.addEventListener('a2n:progress',e=>{const box=el('paymentCallResult');let preview=box.querySelector('[data-stream-preview]');if(!preview){preview=document.createElement('pre');preview.dataset.streamPreview='1';box.replaceChildren(preview)}preview.textContent=(preview.textContent+e.detail.delta).slice(-20000)});
  function selectPlan(row){plan=row;const p=row.record;el('paymentPlanState').textContent=I18N.t('pay.plan_accepted',{amount:p.terms.amount_minor_decimal||p.terms.amount_minor,currency:p.terms.currency,payee:p.terms.payee});
    el('paymentFixedInput').hidden=false;el('paymentFixedInput').querySelector('pre').textContent=JSON.stringify(row.request?.payload??I18N.t('pay.original_input'),null,2);
    if(p.terms.method==='a2n-points/1'){el('paymentPlanState').textContent=planMode(p.terms.mode,p)+' '+I18N.t('pay.plan_reserved_suffix');el('paymentPay').disabled=true;el('paymentExecute').disabled=row.state!=='PREPARED';el('paymentExecute').textContent=I18N.t('pay.execute_points_btn');return}
    el('paymentPay').disabled=p.terms.flow!=='upfront';el('paymentExecute').disabled=p.terms.flow==='upfront';el('paymentExecute').textContent=p.terms.flow==='authorization'?I18N.t('pay.authorize_and_call_btn'):I18N.t('pay.call_paid_btn');
  }
  async function run(button,fn){button.disabled=true;try{await fn()}catch(e){notice(e.message,true)}finally{button.disabled=['paymentFree','paymentApprove','paymentAutomatic'].includes(button.id)?!quote:['paymentPay','paymentExecute'].includes(button.id)?!plan:false}}
  async function load(){const [status,runtime,risk]=await Promise.all([request('/v1/payment-coordination'),request('/v1/runtime'),request('/v1/risk')]);
    budgetRevision=risk.policy.revision;
    if(status.settlement_policy){const p=status.settlement_policy;settlementRevision=p.revision;policyEditor.set(p);el('settlementProvider').value=JSON.stringify(p.provider_methods,null,2);el('settlementBuyer').value=JSON.stringify(p.buyer_preferences,null,2);el('settlementAutomatic').checked=p.automatic;el('settlementState').textContent=p.automatic?I18N.t('pay.settlement_auto_on'):I18N.t('pay.settlement_auto_off')}
    el('paymentWallet').textContent=status.wallet_address?I18N.t('pay.wallet_line',{address:status.wallet_address}):I18N.t('pay.wallet_none_chain');
    el('paymentConfigured').textContent=I18N.t('pay.configured_line',{native:status.native.length,x402:I18N.t(status.x402.state==='CONFIGURED'?'pay.channel_ready':'pay.channel_unset'),provider:I18N.t(status.provider_x402?'pay.channel_done':'pay.channel_unset')});
    const chosen=el('paymentAgent').value;
    el('paymentAgent').innerHTML=(runtime.projections||[]).map(p=>`<option value="${safe(p.projection_id)}">${safe(p.name||p.projection_id)}</option>`).join('')||'<option value="">'+I18N.t('pay.agent_empty')+'</option>';
    if([...el('paymentAgent').options].some(o=>o.value===chosen))el('paymentAgent').value=chosen;
    await loadEditor();
    el('paymentProposals').innerHTML=(status.proposals||[]).map(p=>`<article class="agent"><b>${I18N.t('pay.proposal_broken')}</b><p>${I18N.t('pay.amount_minor',{amount:safe(p.amount_minor_decimal),currency:safe(p.currency)})}</p><button data-payment-plan="${safe(p.plan_id)}" data-payment-action="accept">${I18N.t('pay.proposal_resume')}</button></article>`).join('');
    el('paymentOrders').innerHTML=status.orders.slice().reverse().map(o=>{const p=o.attempts.at(-1),id=(p.plan_id||String()).startsWith(PLAN_PREFIX)?p.plan_id:null;return `<article class="agent"><b>${safe(p.state==='READY'&&p.new_payment_allowed===false?I18N.t('pay.expired_cancellable'):stateLabel(o.state)||o.state)}</b><p>${I18N.t('pay.order_line',{amount:safe(p.amount_minor_decimal||p.amount_minor),currency:safe(p.currency),trade:safe(o.trade_uid.slice(-12)),attempts:o.attempts.length})}</p><p class="sub">${safe(p.reference||I18N.t('pay.no_reference'))}</p>${id?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="reconcile">${I18N.t('pay.reconcile_btn')}</button>${p.state==='READY'?`${p.new_payment_allowed!==false?`<button data-payment-plan="${safe(id)}" data-payment-action="${p.payment_flow==='upfront'?'pay':'execute'}">${p.payment_flow==='upfront'?I18N.t('pay.pay_original_btn'):I18N.t('pay.authorize_and_call_btn')}</button>`:''}<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="cancel">${I18N.t('pay.cancel_btn')}</button>`:''}${p.state==='CONFIRMED'?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="execute">${I18N.t('pay.continue_btn')}</button>`:''}`:''}</article>`}).join('')||'<p class="sub">'+I18N.t('pay.orders_empty')+'</p>';
    el('paymentIncoming').innerHTML=(status.incoming||[]).slice().reverse().map(p=>`<article class="agent"><b>${safe(p.authorization_state==='EXPIRED'?I18N.t('pay.incoming_expired'):stateLabel(p.state)||p.state)}</b><p>${I18N.t('pay.amount_minor',{amount:safe(p.amount_minor_decimal),currency:safe(p.currency)})}</p><p class="sub">${I18N.t('pay.incoming_buyer',{did:safe(p.buyer_did)})}</p></article>`).join('')||'<p class="sub">'+I18N.t('pay.incoming_empty')+'</p>';
    el('paymentRefundProposal').innerHTML=(runtime.resolution_agreements||[]).filter(a=>a.state==='BOTH_ACCEPTED'&&a.proposal.body.action==='REFUND').map(a=>`<option value="${safe(a.proposal.message_id)}">${safe(a.amount_minor_decimal||a.proposal.body.amount_minor)} ${safe(a.proposal.body.currency)} · ${safe(a.execution)}</option>`).join('')||'<option value="">'+I18N.t('pay.refund_empty')+'</option>';
  }
  el('paymentConfigure').onclick=e=>run(e.currentTarget,async()=>{const chain='eip155:'+el('paymentChain').value.trim(),rpc=el('paymentRPC').value.trim(),confirmations=Number(el('paymentConfirmations').value),allow_http=el('paymentHTTP').checked;
    const config={native:[],allow_http};if(el('paymentNative').checked)config.native.push({currency:el('paymentCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,allow_http});
    if(el('paymentX402').checked)config.x402={allow_http,assets:[{currency:el('paymentTokenCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,asset:el('paymentToken').value.trim(),name:el('paymentDomain').value.trim(),version:el('paymentDomainVersion').value.trim()}]};
    if(el('paymentFacilitator').value.trim())config.facilitator_url=el('paymentFacilitator').value.trim();
    const body={config},file=el('paymentKeystore').files[0];if(file){body.keystore=JSON.parse(await file.text());body.password=el('paymentPassword').value}
    await request('/v1/payment-coordination/configure',body);el('paymentPassword').value='';el('paymentKeystore').value='';await load();notice(I18N.t('pay.configured_notice'))});
  el('settlementSave').onclick=e=>run(e.currentTarget,async()=>{await request('/v1/payment-coordination/policy',{expected_revision:settlementRevision,...policyEditor.get(),automatic:el('settlementAutomatic').checked});await load();el('paymentAutomatic').disabled=true;notice(I18N.t('pay.settlement_saved'))});
  el('paymentBudget').onclick=e=>run(e.currentTarget,async()=>{await request('/v1/payment-coordination/budget',{currency:el('paymentBudgetCurrency').value.trim().toUpperCase(),per_trade:el('paymentPerTrade').value,total_exposure:el('paymentExposure').value,daily_spend:el('paymentDaily').value,per_counterparty:el('paymentCounterparty').value,expected_revision:budgetRevision});await load();notice(I18N.t('pay.budget_saved'))});
  el('paymentRefresh').onclick=e=>run(e.currentTarget,load);
  el('paymentQuote').onclick=e=>run(e.currentTarget,async()=>{await loadEditor();if(!inputScope)throw Error(I18N.t('pay.err_no_agent'));const input=editor.get(),revision=inputRevision;
    const prepared={task_id:'pay_'+crypto.randomUUID(),skill:el('paymentSkill').value,payload:input};
    if(window.a2nTaskReview)await window.a2nTaskReview.prepare(el('paymentAgent').value,prepared);
    const offered=await request('/v1/trades/quote',{projection_id:el('paymentAgent').value,request:prepared});if(revision!==inputRevision)return;quote=offered;plan=null;
    if(window.a2nSelection)try{await window.a2nSelection.confirmChoice(el('paymentAgent').value,prepared,inputCard)}catch(err){notice(I18N.t('pay.selection_untracked',{error:err.message}),true)}
    el('paymentOffer').textContent=quote.payment_state==='NOT_REQUIRED'?(quote.free_reason==='FREE_INITIAL'?I18N.t('pay.offer_free_initial'):I18N.t('pay.offer_free_other')):quote.options.length?I18N.t('pay.offer_options'):I18N.t('pay.offer_no_common');
    el('paymentOption').innerHTML=quote.options.map((o,i)=>`<option value="${i}">${safe(o.method==='a2n-points/1'?optionMode(o.mode):o.method==='x402/2'?I18N.t('pay.option_method.x402'):I18N.t('pay.option_method.native'))} · ${safe(o.amount_minor_decimal||o.amount_minor)} ${safe(o.method==='a2n-points/1'?I18N.t('pay.option_unit.points'):o.currency)} · ${safe(o.method==='a2n-points/1'?I18N.t('pay.option_settle.points'):o.flow==='upfront'?I18N.t('pay.option_settle.upfront'):I18N.t('pay.option_settle.authorization'))}</option>`).join('');
    const match=await request('/v1/trades/match',{offer_id:quote.offer_id});if(revision!==inputRevision)return;if(match.option_index!==null)el('paymentOption').value=String(match.option_index);automaticCommand=crypto.randomUUID();el('paymentAutomatic').disabled=!(match.state==='NOT_REQUIRED'||match.automatic&&match.state==='MATCHED');
    el('paymentOption').disabled=!quote.options.length;el('paymentApprove').disabled=!quote.options.length;el('paymentFree').disabled=quote.payment_state!=='NOT_REQUIRED';el('paymentPay').disabled=el('paymentExecute').disabled=true;el('paymentPlanState').textContent=match.state==='MATCHED'?I18N.t('pay.matched'):I18N.t('pay.not_accepted')});
  async function watchAutomatic(command,revision){
    for(let attempt=0;attempt<60;attempt++){
      if(command!==automaticCommand||revision!==inputRevision)return;
      try{
        const job=await request('/v1/trades/automatic?command_id='+encodeURIComponent(command));
        if(command!==automaticCommand||revision!==inputRevision||!job)return;
        el('paymentPlanState').textContent=(stateLabel(job.state)||job.state||I18N.t('pay.auto_preparing'))+(job.finished?'':' · '+I18N.t('pay.auto_checking'));
        if(job.finished){showResult(job.result?.delivery||job.result);await load();await refresh();return}
        if(job.action_required){el('paymentPlanState').textContent=I18N.t('pay.auto_blocked');return}
      }catch(_){el('paymentPlanState').textContent=I18N.t('pay.auto_unreadable')}
      await new Promise(resolve=>setTimeout(resolve,2000));
    }
    if(command===automaticCommand)el('paymentPlanState').textContent+=' · '+I18N.t('pay.auto_elsewhere');
  }
  el('paymentAutomatic').onclick=e=>run(e.currentTarget,async()=>{if(!quote)throw Error(I18N.t('pay.err_no_quote'));if(quote.free_reason==='FREE_INITIAL'&&!confirm(I18N.t('pay.confirm_automatic')))return;const revision=inputRevision,command=automaticCommand;const result=await request('/v1/trades/auto-execute',{offer_id:quote.offer_id,command_id:command});showResult(result.delivery||result);el('paymentPlanState').textContent=stateLabel(result.state)||result.state;await load();await refresh();watchAutomatic(command,revision)});
  el('paymentApprove').onclick=e=>run(e.currentTarget,async()=>{const revision=inputRevision,body={offer_id:quote.offer_id,option_index:Number(el('paymentOption').value),fee_cap_minor:quote.options[Number(el('paymentOption').value)].method==='a2n-points/1'?'0':el('paymentFee').value,command_id:crypto.randomUUID()};if(el('pointsFundingIssuer')?.value)body.funding_issuer=el('pointsFundingIssuer').value;if(el('pointsMaxCost')?.value.trim())body.max_cost=el('pointsMaxCost').value.trim();const prepared=await request('/v1/trades/prepare',body);
    if(revision===inputRevision)selectPlan(prepared);else notice(I18N.t('pay.input_changed_notice'));await load()});
  el('paymentFree').onclick=e=>run(e.currentTarget,async()=>{if(quote.free_reason==='FREE_INITIAL'&&!confirm(I18N.t('pay.confirm_free')))return;showResult(await request('/v1/trades/free-execute',{offer_id:quote.offer_id},{Accept:'text/event-stream'}));await load();await refresh()});
  el('paymentPay').onclick=e=>run(e.currentTarget,async()=>{const result=await request('/v1/trades/plans/'+plan.record.plan_id+'/pay',{});el('paymentPlanState').textContent=stateLabel(result.state)||result.state;el('paymentExecute').disabled=result.state!=='CONFIRMED';await load()});
  el('paymentExecute').onclick=e=>run(e.currentTarget,async()=>{showResult(await request('/v1/trades/plans/'+plan.record.plan_id+'/execute',{},{Accept:'text/event-stream'}));await load();await refresh()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-payment-plan]');if(!b)return;run(b,async()=>{const result=await request('/v1/trades/plans/'+b.dataset.paymentPlan+'/'+b.dataset.paymentAction,{});if(b.dataset.paymentAction==='accept')selectPlan(result);showResult(result);await load();await refresh()})});
  for(const [id,path] of [['paymentRefund','refund'],['paymentRefundQuery','refund-reconcile']])el(id).onclick=e=>run(e.currentTarget,async()=>{const out=await request('/v1/payment-coordination/'+path,{proposal_id:el('paymentRefundProposal').value,fee_cap_minor:el('paymentRefundFee').value});el('paymentRefundState').textContent=I18N.t('pay.refund_state',{state:stateLabel(out.state)||out.state})+(out.reference?' · '+out.reference:'');await load()});
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(['account','find'].includes(b.dataset.page))load().catch(e=>notice(e.message,true))}));
  load().catch(()=>{});
  window.A2NTradeUI={async open(scope){await load();el('paymentAgent').value=scope;await loadEditor();document.querySelector('[data-page="find"]')?.click();el('paymentAgent').scrollIntoView({behavior:'smooth'})}};
})();
