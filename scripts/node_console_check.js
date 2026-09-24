/* 本机节点控制台（runtime.html）渲染核验 —— 与平台 ui_check.js 同一层级：
   语法/纯逻辑已由 pytest 覆盖（test_console_local_node.py 等），这里只做
   真浏览器才验得了的事：渲染、可见性、交互链路、截图、响应式。

   前置：scripts/node_console_check.sh（临时 home 起一个真 daemon，--no-p2p 且不连平台）
   用法：A2N_NODE_BASE=http://127.0.0.1:8771 OUT=<dir> \
         NODE_PATH=<node workspace>/node_modules node scripts/node_console_check.js

   注意：这是"未配置任何发现通道"的最小节点 —— 搜索必须报"没配通道"，
   而不是悄悄装成空结果；挂载一个本地 Agent 后它必须真的出现在「卖 Agent」里。
*/
let chromium;
try {
  ({ chromium } = require('playwright-core'));
} catch (e) {
  console.log('· 跳过：没装 playwright-core（NODE_PATH 指向含它的 node_modules 即可）');
  process.exit(0);
}
const fs = require('fs');
const path = require('path');

const BASE = process.env.A2N_NODE_BASE || 'http://127.0.0.1:8771';
const OUT = process.env.OUT || 'data/node-console-shots';
fs.mkdirSync(OUT, { recursive: true });

const CANDIDATES = [
  process.env.A2N_CHROME || '',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Users/Administrator/AppData/Local/ms-playwright/chromium_headless_shell-1228/'
    + 'chrome-headless-shell-win64/chrome-headless-shell.exe',
].filter(Boolean);

const fails = [];
const ok = (name, cond, extra = '') => {
  console.log((cond ? '  ✓ ' : '  ✗ ') + name + (extra ? '  ' + extra : ''));
  if (!cond) fails.push(name);
};

(async () => {
  const exe = CANDIDATES.find(p => fs.existsSync(p));
  if (!exe) {
    console.error('✗ 找不到可用浏览器内核，设 A2N_CHROME 指向 chrome.exe');
    process.exit(1);
  }
  console.log('  节点: ' + BASE + '/console');
  console.log('  浏览器内核: ' + exe);
  const browser = await chromium.launch({ executablePath: exe, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  // ① 打开即用：本机 /console 不需要配对，直接进控制台
  await page.goto(BASE + '/console', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  ok('打开 /console 不出现配对框', await page.locator('#pair').count() === 0);
  ok('连接徽章显示节点在线（cookie 自动引导）',
     (await page.textContent('#connection') || '').includes('节点在线'));
  ok('节点身份是 did:a2n:',
     (await page.textContent('#did') || '').startsWith('did:a2n:'));

  // ② 页签落位：默认「找 Agent」on，另两页必须真的 display:none（innerText 会骗人）
  const visible = sel => page.evaluate(s => {
    const el = document.querySelector(s);
    return !!el && getComputedStyle(el).display !== 'none';
  }, sel);
  ok('默认落在「找 Agent」页', await visible('#find'));
  ok('「卖 Agent」默认隐藏', !(await visible('#sell')));
  ok('「账户与运行」默认隐藏', !(await visible('#account')));

  // ③ 发现区渲染且非空话
  const disc = (await page.textContent('#discoveryState') || '').trim();
  ok('发现通道状态已渲染', disc.length > 0 && !disc.includes('正在读取'), disc);

  // ④ 没配任何通道时，搜索必须诚实报错（不许装成空结果）
  await page.fill('#skill', 'ocr');
  await page.click('#searchform button');
  await page.waitForTimeout(600);
  const notice = ((await page.textContent('#notice')) || '').trim();
  ok('无通道搜索给出明确提示（含「发现通道」）',
     notice.includes('发现通道') && await visible('#notice'), notice);

  // ⑤ 「卖 Agent」：挂一份自己的 Agent，它必须真的出现在列表里
  await page.click('.navbtn[data-page="sell"]');
  ok('点「卖 Agent」后该页真的显示', await visible('#sell'));
  ok('「找 Agent」同时真的隐藏', !(await visible('#find')));
  await page.evaluate(() => document.getElementById('mount').open = true);
  await page.fill('#agentName', '演示 OCR 成品');
  await page.fill('#agentSkill', 'ocr-demo');
  await page.fill('#endpoint', 'http://127.0.0.1:9/nowhere');
  await page.click('#mountform button[type="submit"], #mountform button:not([type])');
  await page.waitForTimeout(700);
  await page.evaluate(() => { const d = document.getElementById('detail'); if (d && d.open) d.close(); });
  await page.evaluate(() => refresh());
  await page.waitForTimeout(400);
  const bindings = (await page.textContent('#bindings')) || '';
  ok('挂载的 Agent 出现在「我的供给」列表',
     bindings.includes('演示 OCR 成品') && bindings.includes('ocr-demo'));
  ok('供给计数徽章变为 1', ((await page.textContent('#sellBadge')) || '').trim() === '1');
  ok('列表带上真实上游地址（不冒充本节点）',
     bindings.includes('http://127.0.0.1:9/nowhere'));

  // ⑥ 截图：每张必须真落盘且非空
  await page.click('.navbtn[data-page="find"]');
  await page.waitForTimeout(200);
  for (const [name, sel] of [['find', '#find'], ['sell', '#sell'], ['account', '#account']]) {
    if (name !== 'find') {
      await page.click(`.navbtn[data-page="${name}"]`);
      await page.waitForTimeout(200);
    }
    const file = path.join(OUT, `node-${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    const size = fs.existsSync(file) ? fs.statSync(file).size : 0;
    ok(`截图 ${path.basename(file)} 非空`, size > 10000, size + ' bytes');
  }

  // ⑦ 响应式：390px 宽度下页面不许横向溢出
  await page.setViewportSize({ width: 390, height: 800 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth);
  ok('390px 宽度无横向溢出', overflow <= 2, `溢出 ${overflow}px`);
  await page.screenshot({ path: path.join(OUT, 'node-narrow.png'), fullPage: false });

  ok('页面无 JS 运行时错误', errors.length === 0, errors.join(' | ').slice(0, 200));
  await browser.close();
  if (fails.length) {
    console.error(`✗ ${fails.length} 项未过：` + fails.join('；'));
    process.exit(1);
  }
  console.log('✓ 本机节点控制台渲染核验全过');
})().catch(e => { console.error('✗ 运行失败：' + e.message); process.exit(1); });
