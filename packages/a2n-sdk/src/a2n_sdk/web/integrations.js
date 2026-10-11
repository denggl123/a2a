/* Explicit preview and mount: discovery never executes an upstream tool. */
(() => {
  'use strict';
  const parent=document.getElementById('sell');if(!parent)return;
  const section=document.createElement('section');section.className='panel';
  section.innerHTML='<h2>接入已有工具服务</h2><p class="sub">填写 MCP 服务地址，查看可用能力，再选择一个接入。查看列表不会调用工具；接入后由本节点提供统一交易、样品和评价。</p><label>MCP 服务地址<input id="mcpEndpoint" placeholder="http://127.0.0.1:8000/mcp"></label><label>访问账户<select id="mcpAccount"><option value="">无需账户</option></select></label><button id="mcpPreview">查看可接入的能力</button><label>选择能力<select id="mcpTool"></select></label><label><input type="checkbox" id="mcpListed">接入后立即上架</label><button id="mcpMount" disabled>接入选定能力</button><p id="mcpState" aria-live="polite"></p>';
  parent.append(section);const el=id=>document.getElementById(id);let preview=null,accountRef=null;
  async function accounts(){const state=await request('/v1/runtime');const chosen=el('mcpAccount').value;el('mcpAccount').replaceChildren(new Option('无需账户',''),...(state.accounts||[]).map(a=>new Option(a.label||a.account_id,a.account_id)));el('mcpAccount').value=chosen;}
  for(const id of ['mcpEndpoint','mcpAccount'])el(id).addEventListener('change',()=>{preview=null;el('mcpMount').disabled=true;el('mcpTool').replaceChildren();});
  el('mcpPreview').onclick=async()=>{el('mcpPreview').disabled=true;el('mcpMount').disabled=true;try{const endpoint=el('mcpEndpoint').value.trim();accountRef=el('mcpAccount').value||null;preview=await request('/v1/integrations/mcp/preview',{endpoint,account_ref:accountRef});if(endpoint!==el('mcpEndpoint').value.trim()||accountRef!==(el('mcpAccount').value||null)){preview=null;return;}el('mcpTool').replaceChildren(...preview.tools.map((t,i)=>new Option(t.card.name,String(i))));el('mcpMount').disabled=!preview.tools.length;el('mcpState').textContent=`找到 ${preview.tools.length} 项能力，尚未调用任何工具。`;}catch(e){preview=null;el('mcpState').textContent=e.message;}finally{el('mcpPreview').disabled=false;}};
  el('mcpMount').onclick=async()=>{if(!preview)return;el('mcpMount').disabled=true;try{const tool=preview.tools[Number(el('mcpTool').value)];const result=await request('/v1/bindings/http',{card:tool.card,endpoint:preview.endpoint,protocol:'mcp',account_ref:accountRef,listed:el('mcpListed').checked});el('mcpState').textContent='已接入：'+result.service_id;await refresh();}catch(e){el('mcpState').textContent=e.message;}finally{el('mcpMount').disabled=!preview;}};
  document.querySelectorAll('.navbtn').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.page==='sell')accounts().catch(e=>{el('mcpState').textContent=e.message;});}));
  const protocol=el('protocol');if(protocol)protocol.add(new Option('MCP 工具服务','mcp'));
})();
