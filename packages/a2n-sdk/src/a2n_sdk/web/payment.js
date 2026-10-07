/* Explicit owner payment choices; decimal amounts never pass through Number. */
(() => {
  'use strict';
  const account=document.getElementById('account'),buy=document.getElementById('find');
  if(!account||!buy)return;
  const el=id=>document.getElementById(id),safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function section(parent,html){const s=document.createElement('section');s.className='panel';s.innerHTML=html;parent.append(s)}
  section(account,`<h2>对等支付设置</h2><p class="sub">平台手续费为零。买卖双方使用自己的钱包；每笔付款都需你明确操作。付款与交付、质量评价、退款分别记录。</p>
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
    <label>待使用的 Agent<select id="paymentAgent"></select></label><label>本次输入（文字或 JSON）<textarea id="paymentInput" rows="4"></textarea></label>
    <button id="paymentQuote">取得本次交易条件</button><div id="paymentOffer"></div>
    <label>选择支付方式<select id="paymentOption" disabled></select></label><label>允许的网络费上限（最小单位整数，x402 填 0）<input id="paymentFee" inputmode="numeric" value="0"></label>
    <button id="paymentApprove" disabled>明确接受这一份交易条件</button><button id="paymentFree" disabled>明确调用免费服务</button>
    <p id="paymentPlanState"></p><button id="paymentPay" disabled>明确支付这笔转账</button><button id="paymentExecute" disabled>调用已约定的服务</button><pre id="paymentCallResult"></pre>`);
  let quote=null,plan=null,budgetRevision=0;
  const PLAN_PREFIX='pp_';
  const labels={READY:'等待明确付款',UNKNOWN:'结果未知 · 原订单已锁定',CONFIRMED:'付款已确认',FAILED:'确定失败或付款前取消',NOT_REQUIRED:'本次无需付款'};
  function showResult(out){const money=out.payment||out.settlement||(labels[out.state]?out:null),call=out.delivery||(out.result!==undefined?out:null),lines=[];
    if(money)lines.push('款项：'+(labels[money.state]||money.state));
    if(money?.reference)lines.push('链上交易标识：'+money.reference);
    if(call){lines.push('技术交付：'+(call.ok?'已完成':call.state||'待核对'));if(call.result!==undefined){const value=typeof call.result==='string'?call.result:JSON.stringify(call.result,null,2);lines.push('服务结果：\n'+value.slice(0,12000)+(value.length>12000?'\n结果较长，完整内容可在调用记录中查看。':''))}if(call.error)lines.push('说明：'+(typeof call.error==='string'?call.error:JSON.stringify(call.error)))}
    if(!call&&out.record)lines.push('双方条件已确认；后续付款仍需明确操作。');
    el('paymentCallResult').textContent=lines.join('\n')||'原订单状态已更新。'}
  function selectPlan(row){plan=row;const p=row.record;el('paymentPlanState').textContent=`已接受：${p.terms.amount_minor_decimal||p.terms.amount_minor} ${p.terms.currency}（最小单位），收款方 ${p.terms.payee}，平台手续费 0。`;
    el('paymentPay').disabled=p.terms.flow!=='upfront';el('paymentExecute').disabled=p.terms.flow==='upfront';el('paymentExecute').textContent=p.terms.flow==='authorization'?'明确授权付款并调用 Agent':'调用已付款的 Agent'}
  async function run(button,fn){button.disabled=true;try{await fn()}catch(e){notice(e.message,true)}finally{button.disabled=false}}
  async function load(){const [status,runtime,risk]=await Promise.all([request('/v1/payment-coordination'),request('/v1/runtime'),request('/v1/risk')]);
    budgetRevision=risk.policy.revision;
    el('paymentWallet').textContent=status.wallet_address?'本节点钱包：'+status.wallet_address:'尚未配置钱包，付款功能关闭';
    el('paymentConfigured').textContent=`直接转账通道 ${status.native.length} 个；x402 ${status.x402.state==='CONFIGURED'?'可付款':'未配置'}；x402 收款 ${status.provider_x402?'已配置':'未配置'}。`;
    const chosen=el('paymentAgent').value;
    el('paymentAgent').innerHTML=(runtime.projections||[]).map(p=>`<option value="${safe(p.projection_id)}">${safe(p.name||p.projection_id)}</option>`).join('')||'<option value="">先把一个 Agent 加入待使用</option>';
    if([...el('paymentAgent').options].some(o=>o.value===chosen))el('paymentAgent').value=chosen;
    el('paymentProposals').innerHTML=(status.proposals||[]).map(p=>`<article class="agent"><b>交易条件确认中断 · 尚未付款</b><p>${safe(p.amount_minor_decimal)} ${safe(p.currency)}（最小单位）</p><button data-payment-plan="${safe(p.plan_id)}" data-payment-action="accept">恢复原条件的双方确认</button></article>`).join('');
    el('paymentOrders').innerHTML=status.orders.slice().reverse().map(o=>{const p=o.attempts.at(-1),id=(p.plan_id||String()).startsWith(PLAN_PREFIX)?p.plan_id:null;return `<article class="agent"><b>${safe(labels[o.state]||o.state)}</b><p>${safe(p.amount_minor_decimal||p.amount_minor)} ${safe(p.currency)}（最小单位） · ${safe(o.trade_uid.slice(-12))} · 尝试 ${o.attempts.length} 次</p><p class="sub">${safe(p.reference||'尚无链上交易标识')}</p>${id?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="reconcile">核对原付款与交付</button>${p.state==='READY'?`<button data-payment-plan="${safe(id)}" data-payment-action="${p.payment_flow==='upfront'?'pay':'execute'}">${p.payment_flow==='upfront'?'明确支付原转账':'明确授权付款并调用 Agent'}</button><button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="cancel">付款前取消</button>`:''}${p.state==='CONFIRMED'?`<button class="ghost" data-payment-plan="${safe(id)}" data-payment-action="execute">读取或继续约定调用</button>`:''}`:''}</article>`}).join('')||'<p class="sub">尚无付款订单。</p>';
    el('paymentIncoming').innerHTML=(status.incoming||[]).slice().reverse().map(p=>`<article class="agent"><b>${safe(labels[p.state]||({ACCEPTED:'双方已接受 · 等待付款',REVOKED:'付款前已撤销'}[p.state])||p.state)}</b><p>${safe(p.amount_minor_decimal)} ${safe(p.currency)}（最小单位）</p><p class="sub">买方 ${safe(p.buyer_did)}</p></article>`).join('')||'<p class="sub">尚无收到的收费交易约定。</p>';
    el('paymentRefundProposal').innerHTML=(runtime.resolution_agreements||[]).filter(a=>a.state==='BOTH_ACCEPTED'&&a.proposal.body.action==='REFUND').map(a=>`<option value="${safe(a.proposal.message_id)}">${safe(a.amount_minor_decimal||a.proposal.body.amount_minor)} ${safe(a.proposal.body.currency)} · ${safe(a.execution)}</option>`).join('')||'<option value="">尚无双方已接受的退款方案</option>';
  }
  el('paymentConfigure').onclick=e=>run(e.currentTarget,async()=>{const chain='eip155:'+el('paymentChain').value.trim(),rpc=el('paymentRPC').value.trim(),confirmations=Number(el('paymentConfirmations').value),allow_http=el('paymentHTTP').checked;
    const config={native:[],allow_http};if(el('paymentNative').checked)config.native.push({currency:el('paymentCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,allow_http});
    if(el('paymentX402').checked)config.x402={allow_http,assets:[{currency:el('paymentTokenCurrency').value.trim().toUpperCase(),network:chain,rpc_url:rpc,confirmations,asset:el('paymentToken').value.trim(),name:el('paymentDomain').value.trim(),version:el('paymentDomainVersion').value.trim()}]};
    if(el('paymentFacilitator').value.trim())config.facilitator_url=el('paymentFacilitator').value.trim();
    const body={config},file=el('paymentKeystore').files[0];if(file){body.keystore=JSON.parse(await file.text());body.password=el('paymentPassword').value}
    await request('/v1/payment-coordination/configure',body);el('paymentPassword').value='';el('paymentKeystore').value='';await load();notice('支付设置已保存；每笔付款仍需明确操作。')});
  el('paymentBudget').onclick=e=>run(e.currentTarget,async()=>{await request('/v1/payment-coordination/budget',{currency:el('paymentBudgetCurrency').value.trim().toUpperCase(),per_trade:el('paymentPerTrade').value,total_exposure:el('paymentExposure').value,daily_spend:el('paymentDaily').value,per_counterparty:el('paymentCounterparty').value,expected_revision:budgetRevision});await load();notice('付款额度已保存。')});
  el('paymentRefresh').onclick=e=>run(e.currentTarget,load);
  el('paymentQuote').onclick=e=>run(e.currentTarget,async()=>{let input=el('paymentInput').value;try{input=JSON.parse(input)}catch(_){}
    quote=await request('/v1/payment-coordination/quote',{projection_id:el('paymentAgent').value,request:{task_id:'pay_'+crypto.randomUUID(),payload:input}});plan=null;
    el('paymentOffer').textContent=quote.payment_state==='NOT_REQUIRED'?(quote.free_reason==='FREE_INITIAL'?'本次处于前 10 次免费服务；技术交付将公开为脱敏样品。':'本次无需支付；公开范围依服务的样品规则执行。'):quote.options.length?'卖方已签名给出以下条件；金额与收款方在接受后固定。':'双方没有可用的共同支付方式；尚未付款，也未执行服务。';
    el('paymentOption').innerHTML=quote.options.map((o,i)=>`<option value="${i}">${safe(o.method==='x402/2'?'x402 授权付款':'直接链上转账')} · ${safe(o.amount_minor_decimal||o.amount_minor)} ${safe(o.currency)} · ${safe(o.network)} · ${safe(o.flow==='upfront'?'先付款再调用':'授权后交付并结算')}</option>`).join('');
    el('paymentOption').disabled=!quote.options.length;el('paymentApprove').disabled=!quote.options.length;el('paymentFree').disabled=quote.payment_state!=='NOT_REQUIRED';el('paymentPay').disabled=el('paymentExecute').disabled=true;el('paymentPlanState').textContent='尚未接受收费条件。'});
  el('paymentApprove').onclick=e=>run(e.currentTarget,async()=>{plan=await request('/v1/payment-coordination/prepare',{offer_id:quote.offer_id,option_index:Number(el('paymentOption').value),fee_cap_minor:el('paymentFee').value,command_id:crypto.randomUUID()});
    selectPlan(plan);await load()});
  el('paymentFree').onclick=e=>run(e.currentTarget,async()=>{showResult(await request('/v1/payment-coordination/free-execute',{offer_id:quote.offer_id}));await load();await refresh()});
  el('paymentPay').onclick=e=>run(e.currentTarget,async()=>{const result=await request('/v1/payment-coordination/plans/'+plan.record.plan_id+'/pay',{});el('paymentPlanState').textContent=labels[result.state]||result.state;el('paymentExecute').disabled=result.state!=='CONFIRMED';await load()});
  el('paymentExecute').onclick=e=>run(e.currentTarget,async()=>{showResult(await request('/v1/payment-coordination/plans/'+plan.record.plan_id+'/execute',{}));await load();await refresh()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-payment-plan]');if(!b)return;run(b,async()=>{const result=await request('/v1/payment-coordination/plans/'+b.dataset.paymentPlan+'/'+b.dataset.paymentAction,{});if(b.dataset.paymentAction==='accept')selectPlan(result);showResult(result);await load();await refresh()})});
  for(const [id,path] of [['paymentRefund','refund'],['paymentRefundQuery','refund-reconcile']])el(id).onclick=e=>run(e.currentTarget,async()=>{const out=await request('/v1/payment-coordination/'+path,{proposal_id:el('paymentRefundProposal').value,fee_cap_minor:el('paymentRefundFee').value});el('paymentRefundState').textContent='退款：'+(labels[out.state]||out.state)+(out.reference?' · '+out.reference:'');await load()});
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(['account','find'].includes(b.dataset.page))load().catch(e=>notice(e.message,true))}));
  load().catch(()=>{});
})();
