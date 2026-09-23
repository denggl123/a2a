/*
 * Browser adapter for the local Agent runtime.
 *
 * The platform console remains the only user-facing control surface. The
 * loopback runtime is a separate security/process boundary (it owns local
 * credentials and local Agent forwarding), not a second product.
 */
(() => {
  let token = sessionStorage.getItem('a2n-local-token') || '';
  let base = sessionStorage.getItem('a2n-local-base') || 'http://127.0.0.1:8771';
  let pending = null;

  const style = document.createElement('style');
  style.textContent = `.a2n-local-dialog,.a2n-local-dialog *{box-sizing:border-box}
    .a2n-local-dialog input,.a2n-local-dialog select{min-width:0;width:100%}
    .a2n-local-dialog button{max-width:100%}
    @media(max-width:640px){
      .a2n-local-dialog{width:calc(100vw - 24px)!important;padding:18px!important}
      .a2n-local-dialog [data-account],.a2n-local-dialog [data-supply]{grid-template-columns:1fr!important}
      .a2n-local-dialog [data-account] button,.a2n-local-dialog [data-supply] button,
      .a2n-local-dialog [data-supply-account]{grid-column:1!important}
    }`;
  document.head.append(style);
  const dialog = document.createElement('dialog');
  dialog.className = 'a2n-local-dialog';
  dialog.style.cssText = 'width:min(880px,94vw);max-height:90vh;overflow:auto;border:1px solid #d4e3dc;border-radius:16px;padding:24px;color:#183a3a';
  dialog.innerHTML = `<div style="display:flex;gap:16px;align-items:center">
      <div style="flex:1"><div style="font-size:11px;letter-spacing:1.4px;color:#247464">LOCAL RUNTIME</div><h3 style="margin:4px 0 0">本机 Agent</h3></div>
      <button class="ghost" data-close>关闭</button>
    </div>
    <p class="muted">仍在当前平台控制台完成配置。本机后台只负责保管账户、连接本地 Agent，并为 AI 工作台提供稳定接入点。</p>
    <form data-pair>
      <label>本机后台地址<input data-base aria-label="本机后台地址" style="display:block;width:100%;margin:7px 0 12px"></label>
      <label>启动时显示的配对码<input data-code aria-label="本机配对码" inputmode="numeric" autocomplete="off" maxlength="8" style="display:block;width:100%;margin:7px 0 12px" required></label>
      <button>连接本机</button>
      <details style="margin:14px 0"><summary>本机后台尚未启动</summary><p class="muted">在项目环境运行以下命令。它是后台组件，不需要作为另一套控制台使用。</p><pre style="white-space:pre-wrap">python -m a2n_node serve</pre></details>
    </form>
    <section data-manage hidden>
      <div data-summary style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:18px 0"></div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span data-state class="muted"></span><span style="flex:1"></span><button class="ghost" data-refresh>刷新</button><button class="ghost" data-disconnect>断开本机</button></div>
      <details open style="margin-top:18px"><summary><b>本地账户</b> · API 凭据只保存在这台电脑</summary>
        <div data-accounts style="margin:12px 0"></div>
        <form data-account style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <input data-account-id aria-label="账户标识" placeholder="账户标识，如 openai-main" required>
          <input data-account-label aria-label="账户名称" placeholder="显示名称" required>
          <input data-header-name aria-label="请求头名称" placeholder="请求头，如 Authorization" required>
          <input data-header-value aria-label="请求头密钥" type="password" autocomplete="off" placeholder="请求头内容" required>
          <button style="grid-column:1/-1">保存本地账户</button>
        </form>
      </details>
      <details open style="margin-top:18px"><summary><b>我的供给</b> · 把本地或远程 Agent 接入并上架</summary>
        <div data-bindings style="margin:12px 0"></div>
        <form data-supply style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <input data-agent-name aria-label="Agent 名称" placeholder="名称，如发票识别" required>
          <input data-agent-skill aria-label="能力标识" placeholder="能力标识，如 invoice-ocr" required>
          <input data-agent-endpoint aria-label="Agent 地址" type="url" placeholder="http://127.0.0.1:9001" required>
          <select data-agent-protocol aria-label="Agent 协议"><option value="a2a">A2A</option><option value="json">普通 JSON HTTP</option></select>
          <select data-supply-account aria-label="供给账户" style="grid-column:1/-1"></select>
          <button style="grid-column:1/-1">添加本地或远程 Agent</button>
        </form>
      </details>
      <details open style="margin-top:18px"><summary><b>带回工作台的 Agent</b> · 在“找 Agent”中点击带回即可</summary>
        <div data-projections style="margin:12px 0"></div>
      </details>
      <details style="margin-top:18px"><summary><b>网络状态</b></summary><div data-network style="margin:12px 0"></div></details>
    </section>
    <p data-notice role="status" aria-live="polite" style="overflow-wrap:anywhere;margin:14px 0"></p>
    <div data-export hidden><p>已生成本地卡片。复制到支持 A2A 的工作台即可使用。</p><button data-copy>复制本地 Agent Card</button><pre data-card style="background:#eff5f2;padding:14px;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px"></pre></div>`;
  document.body.append(dialog);

  const el = selector => dialog.querySelector(selector);
  const notice = (text, bad = false) => {
    el('[data-notice]').textContent = text;
    el('[data-notice]').style.color = bad ? '#a33b32' : '';
  };
  const element = (tag, text, className = '') => {
    const out = document.createElement(tag);
    if (text != null) out.textContent = text;
    if (className) out.className = className;
    return out;
  };
  const action = (label, name, id, kind = 'ghost') => {
    const out = element('button', label, kind);
    out.type = 'button'; out.dataset.action = name; out.dataset.id = id;
    return out;
  };
  const empty = text => element('div', text, 'muted');
  const card = (title, subtitle) => {
    const out = element('div');
    out.style.cssText = 'border:1px solid #dce8e3;border-radius:10px;padding:12px;margin:8px 0';
    out.append(element('b', title));
    if (subtitle) { const sub = element('div', subtitle, 'muted'); sub.style.marginTop = '4px'; out.append(sub); }
    return out;
  };

  el('[data-base]').value = base;

  function validate(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)
        || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
        || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      throw Error('请填写本机后台地址，例如 http://127.0.0.1:8771');
    }
    return url.origin;
  }

  async function request(path, body) {
    const response = await fetch(base + path, {method: body ? 'POST' : 'GET',
      headers: {'Content-Type': 'application/json', 'X-A2N-Local-Token': token},
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(35000)});
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) {
        token = ''; sessionStorage.removeItem('a2n-local-token');
        el('[data-pair]').hidden = false; el('[data-manage]').hidden = true;
      }
      throw Error(data.error || '本机操作失败');
    }
    return data;
  }

  function accountOptions(accounts) {
    const select = el('[data-supply-account]');
    const old = select.value;
    select.replaceChildren(new Option('无需账户', ''));
    accounts.forEach(item => select.add(new Option(item.label || item.account_id, item.account_id)));
    if ([...select.options].some(option => option.value === old)) select.value = old;
  }

  function renderSummary(data, elapsed) {
    const peers = ((data.discovery || {}).peers || []);
    const values = [
      ['我的供给', data.bindings.length], ['已接入 Agent', data.projections.length],
      ['本地账户', data.accounts.length], ['在线邻居', peers.length],
      ['控制台往返', `${elapsed} ms`]
    ];
    const box = el('[data-summary]'); box.replaceChildren();
    values.forEach(([label, value]) => {
      const item = element('div'); item.style.cssText = 'border:1px solid #dce8e3;border-radius:10px;padding:12px;background:#f8fbf9';
      item.append(element('div', label, 'muted'), element('b', String(value)));
      box.append(item);
    });
  }

  function renderAccounts(accounts) {
    const box = el('[data-accounts]'); box.replaceChildren();
    if (!accounts.length) return box.append(empty('尚未保存本地账户；免费或无需凭据的 Agent 可直接使用。'));
    accounts.forEach(item => {
      const row = card(item.label || item.account_id, `${item.account_id} · ${item.headers.length} 项凭据`);
      row.append(action('删除', 'remove-account', item.account_id, 'danger'));
      box.append(row);
    });
  }

  function renderBindings(data) {
    const box = el('[data-bindings]'); box.replaceChildren();
    if (!data.bindings.length) return box.append(empty('还没有供给。添加一份调好的 Agent 即可开始。'));
    data.bindings.forEach(item => {
      const publication = (data.published || []).find(value => value.service_id === item.service_id);
      const state = publication ? ` · 平台${publication.state === 'published' ? '已上架' : publication.state}` : '';
      const row = card(item.name || item.service_id,
        `${item.source_kind === 'remote' ? '远程' : '本地'} · ${(item.skills || []).join(' / ') || '未声明能力'} · ${item.enabled ? '接单中' : '已暂停'}${state}`);
      const toggle = action(item.enabled ? '暂停' : '恢复', 'toggle-binding', item.service_id);
      toggle.dataset.enabled = String(!item.enabled); row.append(toggle);
      row.append(action('复制卡片', 'copy-card', item.service_id));
      row.append(action(publication ? '下架' : '上架', publication ? 'unpublish' : 'publish', item.service_id));
      const remove = action('卸载', 'remove-binding', item.service_id, 'danger');
      remove.dataset.published = String(Boolean(publication)); row.append(remove);
      box.append(row);
    });
  }

  function renderProjections(data) {
    const box = el('[data-projections]'); box.replaceChildren();
    if (!data.projections.length) return box.append(empty('还没有带回 Agent。在“找 Agent”里选择一位，点击“带回我的工作台”。'));
    data.projections.forEach(item => {
      const network = (data.network || []).find(value => value.target === item.projection_id);
      const routes = (item.routes || []).filter(route => route.enabled).map(route => route.name).join(' → ') || '未配置';
      const state = network ? (network.reachable ? `${network.rtt_ms} ms` : '暂不可达') : '等待测速';
      const row = card(item.name || item.projection_id, `${state} · 路径 ${routes} · ${item.url || ''}`);
      row.append(action('复制本地卡片', 'copy-card', item.projection_id));
      row.append(action('立即测速', 'probe', item.projection_id));
      row.append(action('移除', 'remove-projection', item.projection_id, 'danger'));
      box.append(row);
    });
  }

  function renderNetwork(data) {
    const discovery = data.discovery;
    const box = el('[data-network]'); box.replaceChildren();
    if (!discovery) return box.append(empty('P2P 发现未启用；本机 Agent 和平台通道仍可正常使用。'));
    const peers = discovery.peers || [], stats = discovery.stats || {};
    const measured = peers.filter(item => item.reachable && item.rtt_ms != null);
    const average = measured.length ? Math.round(measured.reduce((sum, item) => sum + Number(item.rtt_ms), 0) / measured.length) : null;
    box.append(card(discovery.running ? '发现服务在线' : '发现服务离线',
      `${peers.length} 个邻居 · UDP ${average == null ? '尚无往返样本' : `平均 ${average} ms`} · 收/发 ${stats.recv || 0}/${stats.sent || 0}${(discovery.network || {}).nat_traversal ? '' : ' · NAT 打洞尚未开启'}`));
  }

  function render(data, elapsed) {
    el('[data-pair]').hidden = true; el('[data-manage]').hidden = false;
    el('[data-state]').textContent = `本机后台在线 · 配置已保存 · ${String(data.node_did || '').slice(0, 28)}`;
    renderSummary(data, elapsed); renderAccounts(data.accounts || []);
    renderBindings(data); renderProjections(data); renderNetwork(data);
    accountOptions(data.accounts || []);
    const header = document.getElementById('local_node_button');
    if (header) header.textContent = '本机 Agent · 已连接';
  }

  async function status({quiet = false} = {}) {
    if (!token) return;
    try {
      const started = performance.now();
      const data = await request('/v1/runtime');
      render(data, Math.round(performance.now() - started));
      if (!quiet) notice('本机配置已同步。');
    } catch (error) {
      notice(error.name === 'TypeError' ? '未连接到本机后台，请检查节点是否启动。' : error.message, true);
    }
  }

  async function runImport() {
    if (!token || !pending) return;
    notice('正在带回本机…');
    try {
      const data = await request('/v1/projections', pending);
      el('[data-card]').textContent = JSON.stringify(data.card, null, 2);
      el('[data-export]').hidden = false; pending = null;
      notice('已带回本机，可以复制卡片到 AI 工作台。');
      await status({quiet: true});
    } catch (error) { notice(error.message, true); }
  }

  el('[data-pair]').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const next = validate(el('[data-base]').value.trim());
      if (next !== base) token = '';
      base = next;
      const data = await request('/v1/pairing', {code: el('[data-code]').value.trim()});
      token = data.token; sessionStorage.setItem('a2n-local-token', token); sessionStorage.setItem('a2n-local-base', base);
      el('[data-code]').value = ''; notice('本机已连接。'); await status({quiet: true}); await runImport();
    } catch (error) { notice(error.name === 'TypeError' ? '无法连接，请先启动本机后台。' : error.message, true); }
    finally { button.disabled = false; }
  };

  el('[data-account]').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      await request('/v1/accounts', {account_id: el('[data-account-id]').value.trim(),
        label: el('[data-account-label]').value.trim(),
        headers: {[el('[data-header-name]').value.trim()]: el('[data-header-value]').value}});
      el('[data-header-value]').value = ''; notice('本地账户已加密保存。'); await status({quiet: true});
    } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
  };

  el('[data-supply]').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const name = el('[data-agent-name]').value.trim(), skill = el('[data-agent-skill]').value.trim();
      await request('/v1/bindings/http', {card: {name, version: '1.0.0', skills: [{id: skill, name}],
        url: el('[data-agent-endpoint]').value.trim()}, endpoint: el('[data-agent-endpoint]').value.trim(),
        protocol: el('[data-agent-protocol]').value, account_ref: el('[data-supply-account]').value || null});
      notice('Agent 已接入本机，可以上架或复制卡片。'); await status({quiet: true});
    } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
  };

  dialog.addEventListener('click', async event => {
    const button = event.target.closest('button'); if (!button) return;
    const name = button.dataset.action, id = button.dataset.id;
    if (!name) return;
    button.disabled = true;
    try {
      if (name === 'toggle-binding') await request('/v1/bindings/state', {service_id: id, enabled: button.dataset.enabled === 'true'});
      else if (name === 'publish') await request('/v1/publish', {service_id: id, force: true});
      else if (name === 'unpublish') {
        if (!confirm('确认从平台下架？本机供给仍会保留。')) return;
        await request('/v1/unpublish', {service_id: id});
      } else if (name === 'probe') {
        const result = await request('/v1/network/probe', {projection_id: id});
        notice(result.reachable ? `连接耗时 ${result.rtt_ms} ms（未执行 Agent）` : '暂时无法连接对方。', !result.reachable);
      } else if (name === 'copy-card') {
        const value = await request('/a2a/' + encodeURIComponent(id) + '/.well-known/agent.json');
        await navigator.clipboard.writeText(JSON.stringify(value, null, 2)); notice('本地 Agent Card 已复制。');
      } else if (name === 'remove-account') {
        if (!confirm('确认删除这个本地账户？仍被 Agent 使用时不会删除。')) return;
        await request('/v1/accounts/remove', {account_id: id});
      } else if (name === 'remove-projection') {
        if (!confirm('确认移除这个本地投影？已复制到工作台的卡片将失效。')) return;
        await request('/v1/projections/remove', {projection_id: id});
      } else if (name === 'remove-binding') {
        const published = button.dataset.published === 'true';
        if (!confirm(published ? '确认同时从平台下架并卸载这份供给？' : '确认从本机卸载这份供给？')) return;
        await request('/v1/bindings/remove', {service_id: id, unpublish: published});
      }
      if (name !== 'copy-card') await status({quiet: true});
    } catch (error) { notice(error.message || '本机操作失败', true); }
    finally { button.disabled = false; }
  });

  el('[data-close]').onclick = () => dialog.close();
  el('[data-refresh]').onclick = () => status();
  el('[data-disconnect]').onclick = async () => {
    try { await request('/v1/disconnect', {}); } finally {
      token = ''; sessionStorage.removeItem('a2n-local-token');
      el('[data-pair]').hidden = false; el('[data-manage]').hidden = true;
      const header = document.getElementById('local_node_button'); if (header) header.textContent = '本机 Agent';
      notice('已断开本机。');
    }
  };
  el('[data-copy]').onclick = async () => {
    try { await navigator.clipboard.writeText(el('[data-card]').textContent); notice('本地 Agent Card 已复制。'); }
    catch (error) { notice('请手动选择下方卡片内容复制。', true); }
  };

  window.A2NLocal = {
    open() { if (!dialog.open) dialog.showModal(); status({quiet: true}); },
    importCard(cardValue, targetRef, principal) {
      pending = {card: cardValue, target_ref: targetRef, headers: principal ? {'X-Principal': principal} : {}};
      el('[data-export]').hidden = true;
      if (!dialog.open) dialog.showModal();
      if (token) runImport(); else notice('先连接本机，配对后会继续带回。');
    }
  };
})();
