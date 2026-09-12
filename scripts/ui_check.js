/* 控制台渲染核验（走查收口用）：用真浏览器打开 /console，逐页断言改过的点。
   这是唯一能自动化验证 console.html **渲染结果**的一层 —— 语法靠
   scripts/console_js_check.js、纯逻辑靠 scripts/console_logic_check.js，
   渲染只能真开浏览器。

   依赖可选：需要 playwright-core（managed node 的 workspace 里已有）。
   前置：scripts/sim_start.sh fresh + scripts/a2a_smoke.py
        （让库里有流水 / 已绑渠道 / 配对，否则断言会落空）
   用法：NODE_PATH=<node workspace>/node_modules node scripts/ui_check.js
*/
let chromium;
try {
  ({ chromium } = require('playwright-core'));
} catch (e) {
  console.log('· 跳过：没装 playwright-core（NODE_PATH 指向含它的 node_modules 即可）');
  process.exit(0);
}
const fs = require('fs');

const BASE = process.env.A2N_BASE || 'http://127.0.0.1:8000';
// playwright-core 只带驱动、不带内核：用机器上已有的内核，别去下载
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
  console.log('  浏览器内核: ' + exe);
  const browser = await chromium.launch({ executablePath: exe, args: ['--no-proxy-server'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE + '/console', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);

  // ---------- 找 Agent ----------
  const find = await page.locator('#find').innerHTML();
  ok('找 Agent：付费方式 + 追加筛选都在位',
     find.includes('我支持的付费方式') && find.includes('价格上限') && find.includes('属地不限'));
  ok('找 Agent：标记有图例', find.includes('看不懂标记') && find.includes('PROBATION=观察期'));
  ok('找 Agent：信誉有分档 + 可视化', /信誉 <b>\d+<\/b>/.test(find) && find.includes('class="bar"'));
  ok('找 Agent：内部 id 退到小字（主视觉给名称）',
     find.includes('内部标识：写 SDK') && !find.includes('class="sub mono">ag_'));
  const rows = await page.locator('#d_table .agent-row').count();
  ok('找 Agent：列出了 demo 样本', rows >= 3, `${rows} 行`);

  // ---------- 试调用面板 ----------
  await page.locator('#find button:has-text("试调用")').first().click();
  await page.waitForTimeout(400);
  const call = await page.locator('#f_call').innerHTML();
  ok('试调用：技能改为下拉', call.includes('<select id="fc_skill"'));
  ok('试调用：手填参数收进「高级」',
     call.includes('高级：指定技能 id / 手填参数') && call.includes('<details'));

  // ---------- 我的账户 ----------
  await page.locator('nav button[data-tab="account"]').click();
  await page.waitForTimeout(1500);
  const acct = await page.locator('#account').innerHTML();
  ok('账户页：余额卡标明「积分余额」', acct.includes('积分余额'));
  ok('账户页：余额卡里直接有充值入口',
     /积分余额[\s\S]{0,500}onclick="quickDeposit\(\)"/.test(acct));
  ok('账户页：说清与渠道付款的关系', acct.includes('渠道付款不走这个余额'));
  ok('账户页：按意图分流的付款向导',
     acct.includes('我该怎么付款') && acct.includes('偶尔调用') && acct.includes('长期合作'));
  ok('账户页：结算账户标注账期用途', acct.includes('长期合作 / 按账期结算用'));
  ok('账户页：x402 单独说明（不再和对等账户并列）', acct.includes('只收 x402 的 Agent 怎么办'));
  ok('账户页：已绑渠道显示「换绑」而非「去绑定」', acct.includes('换绑 ›'));

  const led = await page.locator('#ledger_body').innerText();
  console.log('    [流水预览] ' + led.split('\n').filter(Boolean).slice(0, 4).join(' | ').slice(0, 220));
  ok('账户页：流水渲染出内容（不是报错页）', led.length > 0 && !led.includes('加载失败'));

  // ---------- 高级功能：分组 + 演示开关隔离 ----------
  const more = await page.locator('#more').innerHTML();
  ok('高级功能：按「运营 / 审计·凭证」分组',
     more.includes('运营') && more.includes('审计 / 凭证'));
  await page.locator('#more button[data-tab="overview"]').click();
  await page.waitForTimeout(1000);
  const ov = await page.locator('#overview').innerHTML();
  ok('全网总览：演示操作收进折叠区', ov.includes('演示操作') && ov.includes('<details'));
  ok('全网总览：不再有裸露的主操作按钮',
     !/<div class="row">\s*<button onclick="quickDeposit\(\)"/.test(ov));

  // ---------- 卖 Agent：价格按元填 ----------
  await page.locator('nav button[data-tab="sell"]').click();
  await page.waitForTimeout(1000);
  await page.locator('button:has-text("+ 上架新 Agent")').click();
  await page.waitForTimeout(400);
  const shelf = await page.locator('#shelf_box').innerHTML();
  ok('上架：价格标注「按元填」', shelf.includes('价格（按元填，不用换算）'));
  ok('上架：有实时回显位', shelf.includes('id="sh_price_hint"'));

  const hintOf = async v => {
    await page.fill('#sh_price', v);
    await page.waitForTimeout(150);
    return (await page.locator('#sh_price_hint').innerText()).trim();
  };
  ok('上架：0.03 元 → 3 分', (await hintOf('0.03')).includes('¥0.03/次'), await hintOf('0.03'));
  ok('上架：¥0.03 也认', (await hintOf('¥0.03')).includes('¥0.03/次'));
  ok('上架：0.25 元 → 25 分', (await hintOf('0.25')).includes('¥0.25/次'));
  const bad = await hintOf('abc');
  ok('上架：非法输入被拦在保存之前', bad.includes('填数字'), bad);

  // 免费档：清空输入
  await page.fill('#sh_price', '');
  await page.waitForTimeout(150);
  ok('上架：留空＝不标价', (await page.locator('#sh_price_hint').innerText()).includes('不标价'));

  await browser.close();
  console.log('');
  if (errors.length) {
    console.log('  浏览器控制台报错：');
    [...new Set(errors)].slice(0, 8).forEach(e => console.log('   - ' + e));
  }
  if (fails.length) {
    console.log(`✗ 控制台渲染核验失败 ${fails.length} 项`);
    process.exit(1);
  }
  if (errors.length) {
    console.log('✗ 有 JS 报错，虽断言都过');
    process.exit(1);
  }
  console.log('✓ 控制台渲染核验通过');
})();
