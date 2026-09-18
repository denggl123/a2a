/* Visual-polish regression: responsive layout, search and portable Agent Card.
   Run against the isolated demo after a2a_smoke.py, with NODE_PATH configured.
   A2N_BASE / A2N_CHROME / OUT can override local defaults. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const BASE = process.env.A2N_BASE || 'http://127.0.0.1:8000';
const OUT = process.env.OUT || path.join(__dirname, '..', 'data', 'console-polish');
const exe = [process.env.A2N_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p => p && fs.existsSync(p));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: exe, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
    const page = await context.newPage();
    const shot = async name => {
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: path.join(OUT, name), fullPage: true, animations: 'disabled' });
    };
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => { errors.push(d.message()); d.dismiss(); });
    if (process.env.BASELINE_REV) {
      const { execFileSync } = require('node:child_process');
      const baseline = execFileSync('git', ['show', `${process.env.BASELINE_REV}:packages/a2n-server/src/a2n_server/web/console.html`],
        { cwd: path.join(__dirname, '..'), encoding: 'utf8' });
      await page.route(BASE + '/console', route => route.fulfill({ contentType: 'text/html', body: baseline }));
      await page.goto(BASE + '/console');
      await page.waitForFunction(() => document.querySelectorAll('#d_table .agent-row').length >= 3);
      await shot('before-discovery.png');
      await page.unroute(BASE + '/console');
    }
    await page.goto(BASE + '/console');
    await page.waitForFunction(() => document.querySelectorAll('#d_table .agent-row').length >= 3);
    const initialCount = await page.locator('#d_table .agent-row').count();
    assert.equal(await page.locator('#more').isVisible(), false);
    await page.waitForFunction(() => /\d+\s*ms/.test(document.querySelector('#network_live').textContent));
    await page.waitForFunction(() => document.querySelectorAll('#d_table .agent-network .latency').length >= 3
      && [...document.querySelectorAll('#d_table .agent-network .latency')].every(el => /\d+\s*ms/.test(el.textContent)));
    assert.match(await page.locator('#network_strip').innerText(), /不执行技能，不产生账单/);
    await page.waitForFunction(() => !document.querySelector('#network_strip button').disabled);
    await page.locator('#network_strip button').click();
    await page.waitForFunction(() => !document.querySelector('#network_strip button').disabled);
    // Live platform failure must not keep displaying the previous green RTT.
    await page.route(BASE + '/health', route => route.fulfill({ status: 503, body: '{}' }));
    await page.evaluate(() => _netPlatformPing());
    assert.match(await page.locator('#network_live').innerText(), /连接异常/);
    await page.unroute(BASE + '/health');
    await page.evaluate(() => _netPlatformPing());
    await shot('discovery-desktop.png');

    // Product-like details, with administrative metadata out of the browsing path.
    assert.equal(await page.locator('#d_table .agent-id').first().isVisible(), false);
    assert.equal(await page.locator('#d_table .agent-evidence').first().isVisible(), false);
    const realIds = await page.locator('#d_table .agent-row').evaluateAll(rows => rows.map(r=>r.dataset.agentId));
    await page.locator('#d_table .detail-action').first().click();
    await page.locator('#dr_body .ev-grid').waitFor();
    assert.ok(await page.locator('#dr_body .service-hero').isVisible());
    assert.equal(await page.locator('#dr_body .service-spec').getAttribute('open'), null);
    assert.equal(await page.locator('#dr_body button:has-text("编辑")').count(), 0);
    await shot('service-detail-desktop.png');
    await page.locator('#dr_body .service-actions button:has-text("Agent Card")').click();
    await page.locator('#card_body').waitFor({state:'attached'});
    await page.locator('#f_card .export-actions button').last().click();

    for (const view of ['list','compare','cards']) {
      await page.locator(`.view-switch [data-view="${view}"]`).click();
      assert.equal(await page.locator('#d_table').getAttribute('data-view'), view);
      assert.equal(await page.locator(`.view-switch [data-view="${view}"]`).getAttribute('aria-pressed'), 'true');
      assert.deepEqual(await page.locator('#d_table .agent-row').evaluateAll(rows=>rows.map(r=>r.dataset.agentId)), realIds);
      assert.equal(await page.locator('#d_table .agent-comparison').first().isVisible(), view==='compare');
      assert.equal(await page.locator('#d_table .agent-description').first().isVisible(), view==='cards');
      await shot(`discovery-${view}-desktop.png`);
    }
    await page.locator('.view-switch [data-view="compare"]').click();
    await page.reload();
    await page.waitForFunction(()=>document.querySelectorAll('#d_table .agent-row').length>=3);
    assert.equal(await page.locator('#d_table').getAttribute('data-view'), 'compare', '记住人工选择的展示方式');
    await page.locator('.view-switch [data-view="cards"]').click();

    // Runtime-only fixtures: many similar agents without registering fake services or writing the database.
    const realAgents = await page.evaluate(()=>_disc.agents);
    await page.evaluate(()=>{
      const template=_disc.agents[0];
      _disc.agents=Array.from({length:72},(_,i)=>{
        const a=JSON.parse(JSON.stringify(template));
        a.agent_id=`view-test-${String(i).padStart(2,'0')}`;
        a.name=`相似协作者 ${String(i).padStart(2,'0')}`;
        a.status='ACTIVE'; a.selfproof='signed'; a.reputation=i/100;
        a.network={reachable:true,rtt_ms:200-i,checked_at:new Date().toISOString()};
        a.evidence={trial:{trial:false,state:'GRADUATED'}};
        const skill=i%4===0?'translate':'ocr';
        a.card_json=JSON.stringify({skills:[{id:skill,name:skill==='ocr'?'OCR':'翻译'}],
          description:'仅用于界面测试的相似服务，不注册、不调用。',
          'x-a2n':{price_book:i===0?{}:{[skill]:{[i===1?'USDC':'CNY']:{dimensions:[{key:'call_count',amount:i}]}}}}});
        return a;
      });
      _disc.page=1; _disc.sort='ready'; _discRender();
    });
    assert.equal(await page.locator('#d_table .agent-row').count(), 24);
    assert.match(await page.locator('#d_pager').innerText(), /72.*1.*3/);
    await page.locator('#d_pager button:has-text("下一页")').click();
    assert.match(await page.locator('#d_pager').innerText(), /第 2/);
    assert.equal(await page.locator('#d_table .agent-row').first().getAttribute('data-agent-id'), 'view-test-24');
    await page.locator('.view-switch [data-view="list"]').click();
    assert.match(await page.locator('#d_pager').innerText(), /第 1/);
    await page.selectOption('#disc_sort','price');
    assert.equal(await page.locator('#d_table .agent-row').first().getAttribute('data-agent-id'), 'view-test-00');
    assert.equal(await page.locator('#d_table .agent-row').nth(1).getAttribute('data-agent-id'), 'view-test-02');
    await page.selectOption('#disc_sort','latency');
    assert.equal(await page.locator('#d_table .agent-row').first().getAttribute('data-agent-id'), 'view-test-71');
    await page.selectOption('#disc_sort','reputation');
    assert.equal(await page.locator('#d_table .agent-row').first().getAttribute('data-agent-id'), 'view-test-71');
    await page.selectOption('#disc_skill','translate');
    assert.equal(await page.locator('#d_table .agent-row').count(), 18);
    assert.equal(await page.locator('#d_pager button').count(), 0);
    await page.evaluate(agents=>{
      _disc.agents=agents;
      Object.assign(_disc,{skill:'',sort:'ready',view:'cards',page:1});
      _discRender();
    }, realAgents);
    const peerAgent = realAgents.find(a=>JSON.parse(a.card_json).accepts?.includes('peer_account'));
    if (peerAgent) {
      await page.locator(`#d_table [data-agent-id="${peerAgent.agent_id}"] .detail-action`).click();
      await page.locator('#dr_body .service-actions button:has-text("账户配对")').click();
      await page.locator('#f_pair .card').waitFor();
      assert.ok(await page.locator('#f_pair').isVisible(), '配对入口移入详情但功能保留');
      await page.evaluate(()=>document.getElementById('f_pair').innerHTML='');
    }

    await page.fill('#f_q', 'definitely-no-such-agent');
    await page.locator('#d_table .empty').waitFor();
    assert.match(await page.locator('#d_table').innerText(), /没有匹配/);
    await page.locator('#disc_reset').click();
    assert.equal(await page.locator('#f_q').inputValue(), '');
    assert.equal(await page.locator('#d_table .agent-row').count(), initialCount);
    await page.locator('.search-suggestions button').first().click();
    assert.equal(await page.locator('#f_q').inputValue(), 'OCR');
    assert.ok(await page.locator('#d_table .agent-row').count());
    await page.locator('#disc_reset').click();

    await page.locator('#d_table .card-action').first().click();
    await page.locator('#card_body').waitFor({ state: 'attached' });
    const original = await page.locator('#card_body').textContent();
    const parsed = JSON.parse(original);
    assert.ok(parsed.skills.length && parsed['x-a2n']);
    const cardUrl = await page.locator('#d_table .card-action').first().getAttribute('onclick');
    const agentId = cardUrl.match(/cardPanel\('([^']+)'\)/)[1];
    const response = await context.request.get(`${BASE}/a2a/${encodeURIComponent(agentId)}/.well-known/agent.json`);
    assert.deepEqual(parsed, await response.json(), '卡片必须完整保留服务端投影，不能拼造或删字段');
    await page.locator('#f_card .export-actions button').first().click();
    await page.waitForFunction(() => document.getElementById('card_out').textContent.includes('已复制'));
    // Windows clipboard normalizes LF to CRLF; content must remain identical.
    assert.equal((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n'), original);
    await page.locator('#f_card .export-actions button').nth(1).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `${BASE}/a2a/${agentId}/.well-known/agent.json`);
    await shot('agent-card-desktop.png');

    // Legacy browsers: collapsed JSON must become selectable for manual fallback.
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    });
    await page.locator('#f_card .export-actions button').first().click();
    assert.equal(await page.locator('#f_card details').getAttribute('open'), '');
    await page.locator('#f_card .export-actions button').last().click();

    const fits = async name => {
      const sizes = await page.evaluate(() => ({
        viewport: innerWidth, content: document.documentElement.scrollWidth,
      }));
      assert.ok(sizes.content <= sizes.viewport + 1, `${name}: viewport=${sizes.viewport}, content=${sizes.content}`);
    };
    for (const width of [2560, 1920, 1440, 820, 600, 390]) {
      await page.setViewportSize({ width, height: 900 });
      if (width > 760) {
        const layout = await page.evaluate(() => ({
          mainLeft: document.querySelector('main').getBoundingClientRect().left,
          sidebarRight: document.querySelector('nav').getBoundingClientRect().right + 16,
        }));
        assert.ok(layout.mainLeft >= layout.sidebarRight, `侧栏遮挡 ${width}: ${JSON.stringify(layout)}`);
      }
      for (const view of ['cards','list','compare']) {
        await page.locator(`.view-switch [data-view="${view}"]`).click();
        await fits(`发现页 ${view} ${width}`);
        assert.equal(await page.locator('#d_table .agent-access').first().isVisible(), true);
        await shot(`discovery-${view}-${width}.png`);
      }
      await page.locator('.view-switch [data-view="cards"]').click();
      await page.locator('#d_table .card-action').first().click();
      await page.locator('#card_body').waitFor({ state: 'attached' });
      await fits(`卡片 ${width}`);
      await shot(`agent-card-${width}.png`);
      await page.locator('#f_card .export-actions button').last().click();
    }
    await page.locator('nav button[data-tab="account"]').click();
    await page.locator('#acct_bind button').first().waitFor();
    await fits('账户页 390');
    await shot('account-mobile.png');
    await page.setViewportSize({ width: 1440, height: 1080 });
    await shot('account-desktop.png');
    await page.fill('#principal', 'acct:bob');
    await page.locator('nav button[data-tab="sell"]').click();
    await page.locator('#sell tbody tr').first().waitFor();
    await shot('provider-desktop.png');
    await page.setViewportSize({ width: 390, height: 900 });
    await fits('服务维护页 390');
    await page.locator('#sell tbody tr').first().click();
    await page.locator('#dr_body .ev-grid').waitFor();
    await fits('明细抽屉 390');
    await shot('agent-detail-mobile.png');
    assert.equal(await page.locator('#dr_body .service-spec').getAttribute('open'), '');
    assert.equal(await page.locator('#dr_body button:has-text("编辑上架")').count(), 1);
    await page.locator('#dr_body .service-actions button:has-text("Agent Card")').click();
    await page.locator('#card_body').waitFor({state:'attached'});
    assert.ok(await page.locator('#f_card').isVisible(), '供给方详情获取卡片必须切到可见发现页');
    await page.locator('#f_card .export-actions button').last().click();
    await page.locator('nav button[data-tab="sell"]').click();
    await page.locator('#sell tbody tr').first().click();
    await page.locator('#dr_body .service-actions button:has-text("试调用")').click();
    await page.locator('#f_call .card').waitFor();
    assert.ok(await page.locator('#f_call').isVisible(), '供给方详情试调用不能落入隐藏页面');
    assert.deepEqual(errors, []);
    console.log('✓ 三种展示 / 商品式详情 / 偏好记忆 / 72 个相似 Agent 的筛选排序分页 / 搜索 / 完整卡片与真实剪贴板通过');
    console.log('✓ 三种视图在 2560、1920、1440、820、600、390 宽度下无侧栏遮挡、无整页溢出');
    console.log(`✓ 响应式截图: ${OUT}`);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
