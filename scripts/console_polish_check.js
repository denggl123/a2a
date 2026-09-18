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

    await page.fill('#f_q', 'definitely-no-such-agent');
    await page.locator('#d_table .empty').waitFor();
    assert.match(await page.locator('#d_table').innerText(), /没有匹配/);
    await page.locator('.results-head button').click();
    assert.equal(await page.locator('#f_q').inputValue(), '');
    assert.equal(await page.locator('#d_table .agent-row').count(), initialCount);
    await page.locator('.search-suggestions button').first().click();
    assert.equal(await page.locator('#f_q').inputValue(), 'OCR');
    assert.ok(await page.locator('#d_table .agent-row').count());
    await page.locator('.results-head button').click();

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
      await fits(`发现页 ${width}`);
      await shot(`discovery-${width}.png`);
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
    assert.deepEqual(errors, []);
    console.log('✓ 搜索 / 重置 / 完整卡片与真实剪贴板 / 备用复制 / 2560、1920、1440、820、600、390 响应式通过');
    console.log(`✓ 响应式截图: ${OUT}`);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
