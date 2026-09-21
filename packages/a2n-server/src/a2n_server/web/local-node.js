/* Browser adapter only: pairing, import, and local status. Business stays in SDK. */
(() => {
  let token = sessionStorage.getItem('a2n-local-token') || '';
  let base = sessionStorage.getItem('a2n-local-base') || 'http://127.0.0.1:8771';
  let pending = null;
  const dialog = document.createElement('dialog');
  dialog.style.cssText = 'width:min(660px,94vw);border:1px solid #d4e3dc;border-radius:16px;padding:24px;color:#183a3a';
  dialog.innerHTML = `<div style="display:flex;gap:16px;align-items:center"><h3 style="margin:0;flex:1">本机节点</h3><button class="ghost" data-close>关闭</button></div>
    <p class="muted">连接一次，就能把发现的 Agent 带回你的 AI 工作台。</p>
    <form data-pair><label>本机节点地址<input data-base aria-label="本机节点地址" style="display:block;width:100%;margin:7px 0 12px"></label>
    <label>启动时显示的配对码<input data-code aria-label="本机配对码" inputmode="numeric" autocomplete="off" maxlength="8" style="display:block;width:100%;margin:7px 0 12px" required></label>
    <button>连接节点</button><details style="margin:14px 0"><summary>还没有启动节点？</summary><p class="muted">在项目环境中运行：</p><pre style="white-space:pre-wrap">python -m a2n_node serve --open</pre><p class="muted">打开本机管理页后，可生成配对码连接这里。</p></details></form>
    <div data-state hidden></div><p data-notice role="status" aria-live="polite" style="overflow-wrap:anywhere"></p>
    <div data-export hidden><p>已生成本地卡片。复制到支持 A2A 的工作台即可使用。</p><button data-copy>复制本地 Agent Card</button><pre data-card style="background:#eff5f2;padding:14px;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px"></pre></div>`;
  document.body.append(dialog);
  const el = s => dialog.querySelector(s);
  el('[data-base]').value = base;
  const notice = text => { el('[data-notice]').textContent = text; };
  function validate(value) {
    const u = new URL(value);
    if (!['http:', 'https:'].includes(u.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(u.hostname)
        || u.username || u.password || u.pathname !== '/' || u.search || u.hash) throw Error('请填写本机节点地址，例如 http://127.0.0.1:8771');
    return u.origin;
  }
  async function request(path, body) {
    const r = await fetch(base + path, {method:body ? 'POST' : 'GET',
      headers:{'Content-Type':'application/json', 'X-A2N-Local-Token':token},
      body:body ? JSON.stringify(body) : undefined, signal:AbortSignal.timeout(12000)});
    const data = await r.json();
    if (!r.ok) {
      if (r.status === 401) { token = ''; sessionStorage.removeItem('a2n-local-token'); el('[data-pair]').hidden = false; }
      throw Error(data.error || '连接失败');
    }
    return data;
  }
  async function status() {
    if (!token) return;
    try {
      const started = performance.now(), d = await request('/v1/runtime');
      el('[data-pair]').hidden = true; el('[data-state]').hidden = false;
      el('[data-state]').replaceChildren();
      const p = document.createElement('p');
      p.textContent = `节点在线 · ${Math.round(performance.now()-started)} ms · ${d.bindings.length} 份供给 · ${d.projections.length} 个已接入 Agent`;
      const a = document.createElement('a'); a.href = base + '/console'; a.target = '_blank'; a.rel = 'noopener'; a.textContent = '打开本机管理页';
      const b = document.createElement('button'); b.className = 'ghost'; b.style.marginLeft = '16px'; b.textContent = '断开连接';
      b.onclick = async () => { try { await request('/v1/disconnect', {}); } finally { token=''; sessionStorage.removeItem('a2n-local-token'); el('[data-pair]').hidden=false; el('[data-state]').hidden=true; } };
      el('[data-state]').append(p,a,b);
      const header=document.getElementById('local_node_button'); if(header)header.textContent='本机节点 · 已连接';
    } catch(e) { notice(e.name === 'TypeError' ? '未连接到本机节点，请检查节点是否启动。' : e.message); }
  }
  async function runImport() {
    if (!token || !pending) return;
    notice('正在导入到本机…');
    try {
      const d = await request('/v1/projections', pending);
      el('[data-card]').textContent = JSON.stringify(d.card,null,2);
      el('[data-export]').hidden = false; pending = null;
      notice('导入成功，配置已保存在本机。'); await status();
    } catch(e) { notice(e.message); }
  }
  el('[data-pair]').onsubmit = async e => {
    e.preventDefault(); const button=e.submitter; button.disabled=true;
    try {
      const next=validate(el('[data-base]').value.trim());
      if(next!==base)token=''; base=next;
      const d=await request('/v1/pairing',{code:el('[data-code]').value.trim()});
      token=d.token; sessionStorage.setItem('a2n-local-token',token); sessionStorage.setItem('a2n-local-base',base);
      el('[data-code]').value=''; notice('已连接。'); await status(); await runImport();
    } catch(e) { notice(e.name==='TypeError'?'无法连接，请先启动本机节点。':e.message); }
    finally { button.disabled=false; }
  };
  el('[data-close]').onclick = () => dialog.close();
  el('[data-copy]').onclick = async () => { try { await navigator.clipboard.writeText(el('[data-card]').textContent); notice('本地 Agent Card 已复制。'); } catch(e) { notice('请手动选择下方卡片内容复制。'); } };
  window.A2NLocal = {
    open() { if(!dialog.open)dialog.showModal(); status(); },
    importCard(card, target_ref, principal) {
      pending = {card, target_ref, headers:principal ? {'X-Principal':principal} : {}};
      el('[data-export]').hidden = true;
      if(!dialog.open)dialog.showModal();
      if(token)runImport(); else notice('先连接本机节点，配对完成后会继续导入。');
    }
  };
})();
