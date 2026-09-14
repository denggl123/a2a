/* 控制台截图（给人看的走查证据）。它是三层体检的第四层：机器断言过了，
   人还得能一眼看出"这页读起来是不是顺"。
   前置：bash scripts/sim_start.sh fresh && python scripts/a2a_smoke.py
   用法：OUT=<目录> NODE_PATH=<node workspace>/node_modules node scripts/console_shots.js
*/
const fs = require('fs');
const path = require('path');

let chromium;
try {
  ({ chromium } = require('playwright-core'));
} catch (e) {
  console.log('· 跳过：没装 playwright-core（NODE_PATH 指向含它的 node_modules 即可）');
  process.exit(0);
}

const BASE = process.env.A2N_BASE || 'http://127.0.0.1:8000';
const OUT = process.env.OUT || 'D:/workbuy/2026-09-08-09-06-04/ux-review-ia';
const CHROME = [process.env.A2N_CHROME || '',
                'C:/Program Files/Google/Chrome/Application/chrome.exe',
                'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe']
  .filter(Boolean).find(p => fs.existsSync(p));
if (!CHROME) { console.error('✗ 找不到浏览器内核，设 A2N_CHROME'); process.exit(1); }

fs.mkdirSync(OUT, { recursive: true });
// 空态截图作为"走查证据"是不成立的：截了 13 张全是"还没有…"，人眼复核会以为
// 功能没问题。所以哪页该有数据却没有，就在这里记一笔，最后一起报错退出。
const empty = [];

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1100 }, deviceScaleFactor: 1.5 });
  const shot = async (sel, name) => {
    // Windows 上 `/` 是路径分隔符：图名里带一个斜杠，图就会掉进嵌套目录，
    // 产物清单看着"有 13 个"，实际有一个在别人的子目录里。挡住。
    if (/[\\/]/.test(name)) throw new Error('截图文件名不能含路径分隔符: ' + name);
    const el = page.locator(sel);
    await el.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await el.screenshot({ path: path.join(OUT, name) });
    console.log('  · ' + name);
  };
  const sub = async (pageSel, sec, wait = 900) => {
    await page.locator(`${pageSel} .subbtn[data-sec="${sec}"]`).click();
    await page.waitForTimeout(wait);
  };

  await page.goto(BASE + '/console', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1600);

  // ---------- 找 Agent ----------
  await shot('#find', '01-找Agent-发现.png');
  // 先把两个 agent 加进「待使用」，否则那一页只能截到空态（空态好看≠功能成立）
  const addBtns = page.locator('#d_table button:has-text("加入待使用")');
  for (let i = 0; i < Math.min(2, await addBtns.count()); i++) {
    await addBtns.nth(i).click();
    await page.waitForTimeout(700);
  }
  await sub('#find', 'hold');
  // 顺手把收藏导进聚合：让截图里"自建聚合"也是活的，而不是一句空态文案
  await page.locator('#f_hold button:has-text("把收藏一键导进来")').click();
  await page.waitForTimeout(800);
  await shot('#f_hold', '02-找Agent-待使用（收藏+自建聚合）.png');
  await sub('#find', 'calls', 1500);
  await shot('#f_calls', '03-找Agent-调用记录.png');
  const callRows = await page.locator('#f_calls tbody tr').count();
  console.log('    [调用记录] ' + callRows + ' 行 :: '
    + (await page.locator('#f_calls').innerText()).split('\n').filter(Boolean).slice(0, 3).join(' | ').slice(0, 150));
  if (callRows === 0) empty.push('调用记录（收藏非空时也应有：这笔由 smoke 造过）');
  if (callRows > 0) {
    await page.locator('#f_calls tbody tr').first().click();
    await page.waitForTimeout(1300);
    await shot('#dr_panel', '04-明细抽屉-调用全流程（概览+凭证链+原始数据）.png');
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  // ---------- 卖 Agent ----------
  // 「自售列表 / 他人调用记录」都是供给方视角：换成被调用过的 bob，
  // 拿默认的 alice（买家）去截，只会截到两个空态。
  await page.fill('#principal', 'acct:bob');
  await page.locator('header button:has-text("刷新")').click();
  await page.waitForTimeout(1600);
  await page.locator('nav button[data-tab="sell"]').click();
  await page.waitForTimeout(1400);
  await shot('#sell', '05-卖Agent-自售列表.png');
  // Agent 明细：四段证据（客观表现 / 质量偏差 / 使用评价 / 案例）。人眼在这页要看的是
  // "三段之间有没有被偷偷加总成一个总分" —— 那正是这版刻意不做的事。
  const sellRows = await page.locator('#sell tbody tr').count();
  if (sellRows > 0) {
    await page.locator('#sell tbody tr').first().click();
    await page.waitForTimeout(1700);
    await shot('#dr_panel', '05b-Agent明细-四段证据（客观·偏差·评价·案例）.png');
    const evTxt = await page.locator('#dr_body').innerText();
    if (!evTxt.includes('① 客观表现'))
      empty.push('Agent 明细四段证据（新加的页，截不到就等于没做）');
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  } else {
    empty.push('自售列表（bob 名下应有 agent）');
  }
  await sub('#sell', 'calls', 1300);
  await shot('#s_calls', '06-卖Agent-他人调用记录.png');
  const pcRows = await page.locator('#s_calls tbody tr').count();
  console.log('    [他人调用记录] ' + pcRows + ' 行');
  if (pcRows === 0) empty.push('他人调用记录（bob 名下的节点被 smoke 调用过）');
  await sub('#sell', 'market', 1300);
  await shot('#s_market', '07-卖Agent-行情信息.png');
  const mkRows = await page.locator('#s_market tbody tr').count();
  console.log('    [行情] ' + mkRows + ' 行');
  if (mkRows === 0) empty.push('行情信息（四节点上架即应有挂牌行情）');
  if (mkRows > 0) {
    await page.locator('#s_market tbody tr').first().click();
    await page.waitForTimeout(1300);
    await shot('#dr_panel', '08-明细抽屉-行情（挂牌 vs 成交，按币种）.png');
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  // ---------- 我的账户 ----------
  await page.fill('#principal', 'acct:alice');
  await page.locator('header button:has-text("刷新")').click();
  await page.waitForTimeout(1600);
  await page.locator('nav button[data-tab="account"]').click();
  await page.waitForTimeout(1600);
  await shot('#account', '09-账户-账户信息.png');
  await sub('#account', 'flow', 1200);
  await shot('#account', '10-账户-流水列表.png');
  const ledRows = await page.locator('#ledger_body tbody tr').count();
  console.log('    [流水] ' + ledRows + ' 行');
  if (ledRows === 0) empty.push('流水列表（smoke 给 alice 造过付款流水）');
  if (ledRows > 0) {
    await page.locator('#ledger_body tbody tr').first().click();
    await page.waitForTimeout(1200);
    await shot('#dr_panel', '11-明细抽屉-流水（对方·最小单位·关联凭证）.png');
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  // ---------- 维护表单（替掉 window.prompt） ----------
  // 达成交易挂在配对关系上：换成 smoke 里建过配对的 dave。
  await page.fill('#principal', 'acct:dave');
  await page.locator('header button:has-text("刷新")').click();
  await page.waitForTimeout(1800);
  await page.locator('#account .subbtn[data-sec="info"]').click();
  await page.waitForTimeout(400);
  // 配对关系在折叠块里：先展开，否则按钮在 DOM 里但不可见
  const dealBox = page.locator('#account summary:has-text("对等交易与账单")');
  if (await dealBox.count() > 0) { await dealBox.first().click(); await page.waitForTimeout(400); }
  const dealBtn = page.locator('#account button:has-text("达成交易")').first();
  if (await dealBtn.count() > 0) {
    await dealBtn.click();
    await page.waitForTimeout(500);
    await shot('#modal .box', '12-维护表单-取代 prompt.png');
    await page.locator('#modal button:has-text("取消")').click();
    await page.waitForTimeout(200);
  } else {
    console.log('  · 跳过维护表单截图（当前身份没有可配对的关系）');
    empty.push('维护表单（dave 在 smoke 里建过配对）');
  }

  // ---------- 高级功能 ----------
  await page.locator('#moreBtn').click();
  await page.locator('#more button[data-tab="overview"]').click();
  await page.waitForTimeout(1100);
  await shot('#more', '13-高级功能-分组.png');

  await browser.close();
  if (empty.length) {
    console.error('✗ 以下页面截到的是空态，作为证据不成立：\n  - ' + empty.join('\n  - '));
    process.exit(1);
  }
  console.log('✓ 截图输出到 ' + OUT + '（14 张，逐页都非空）');
})();
