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
  console.error('✗ 缺少 playwright-core，浏览器核验未运行（请设置 NODE_PATH 指向含它的 node_modules）');
  process.exit(1);
}
const fs = require('fs');
const path = require('path');
const http = require('http');

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
  ok('「我的账户」默认隐藏', !(await visible('#account')));

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

  // ④a-2 发现页多维筛选：维度栏、排序、视图、浏览入口都要真的在；
  //     浏览 = 按已知能力清单逐条查，没有通道时也必须把失败说出来（不装成 0 个候选）。
  ok('发现页有左侧维度栏', await visible('#facets'));
  const facetsEmpty = ((await page.textContent('#facets')) || '').trim();
  ok('维度栏给诚实空态（还没候选时先说实话）',
     facetsEmpty.includes('筛选维度'), facetsEmpty.slice(0, 60));
  ok('排序与视图切换都在',
     await page.locator('#discSort').isVisible() && await page.locator('#vwList').isVisible());
  ok('浏览入口叫「已知能力清单」而不是"全网"',
     ((await page.textContent('#browseAll')) || '').includes('已知能力清单'));
  await page.click('#browseAll');
  await page.waitForTimeout(1200);
  const browseNotice = ((await page.textContent('#notice')) || '').trim();
  const browseErrors = ((await page.textContent('#searchErrors')) || '').trim();
  ok('浏览结果如实说"已知能力清单"（不冒充全网目录）',
     browseNotice.includes('已知能力清单'), browseNotice.slice(0, 90));
  ok('无通道浏览把通道失败说出来（不装成 0 个候选）',
     browseErrors.length > 0, browseErrors.slice(0, 90));
  await page.click('#vwList');
  await page.waitForTimeout(250);
  ok('切到紧凑视图后结果区仍在（空态也在）', await visible('#results'));
  await page.click('#vwCards');
  await page.waitForTimeout(150);

  // ④b 能力行情面板：没有事实就给诚实空态，不装作有数据
  const market = ((await page.textContent('#marketBody')) || '').trim();
  ok('行情面板给诚实空态（只发行情，不装作有数据）',
     market.includes('还没有行情事实'), market.slice(0, 60));

  // ④c 收据与对账面板：新节点没有成交，必须如实空态而不是画一张空表
  const settle = ((await page.textContent('#settlements')) || '').trim();
  ok('收据与对账面板给诚实空态（本机留存凭证才列，不装作有账）',
     settle.includes('还没有留存收据'), settle.slice(0, 60));

  // ④d/④e 公共服务开关与连接节点都落在「我的账户」页：先真的切过去，
  //     否则这两个面板 display:none，textContent 能读、但 fill 会判不可见。
  await page.click('.navbtn[data-page="account"]');
  await page.waitForTimeout(250);
  ok('切到「我的账户」页后该页真的显示', await visible('#account'));
  ok('「找 Agent」同时真的隐藏', !(await visible('#find')));

  // ④d 允许作为公共服务：默认关闭，且按钮落定（不再停在"正在读取…"）
  const pubToggle = ((await page.textContent('#publicToggle')) || '').trim();
  ok('公共服务开关按钮在账户页可见（不是靠 textContent 偷读隐藏节点）',
     await page.locator('#publicToggle').isVisible());
  ok('公共服务开关默认关闭且可读（不许停在"正在读取…"）',
     pubToggle.includes('开启公共服务'), pubToggle);
  const pubState = ((await page.textContent('#publicState')) || '').trim();
  ok('公共服务状态说明渲染完成',
     pubState.length > 0 && !pubState.includes('正在读取'), pubState.slice(0, 60));

  // ④d-2 「我不认的记录」：新节点还没有任何不认，必须如实空态；
  //      且措辞不许承诺第三方裁决（自持模式没有仲裁员，只有本机留痕）。
  const accountText = ((await page.textContent('#account')) || '');
  const disputesBox = ((await page.textContent('#disputesBox')) || '').trim();
  ok('「我不认的记录」面板给诚实空态',
     disputesBox.includes('还没有不认记录'), disputesBox.slice(0, 60));
  ok('面板不承诺第三方裁决（如实说没有仲裁员）',
     accountText.includes('没有第三方仲裁员'));

  // ④e 连接节点：两种输入语义不同（host:port=种子 / URL=目录源），
  //     且**未启用 P2P 时不许把"已保存"画成"已连上"**。这一条是本机节点
  //     用 --no-p2p 起的（个人电脑最小形态），所以走的正是那条诚实分支。
  const connectEmpty = ((await page.textContent('#connectList')) || '').trim();
  ok('连接节点面板给诚实空态（还没主动连过任何节点）',
     connectEmpty.includes('还没有主动连接任何节点'), connectEmpty.slice(0, 50));

  await page.fill('#connectAddr', '127.0.0.1:9799');
  await page.click('#connectform button');
  await page.waitForTimeout(800);
  const seedRows = ((await page.textContent('#connectList')) || '').trim();
  ok('填 host:port 后列表出现这条种子（带原文地址）',
     seedRows.includes('127.0.0.1:9799'), seedRows.slice(0, 60));
  ok('未启用 P2P 时如实说"已保存、启动时生效"，不画成已连上',
     seedRows.includes('P2P 未启用'), seedRows.slice(0, 90));

  await page.fill('#connectAddr', 'http://127.0.0.1:18787');
  await page.click('#connectform button');
  await page.waitForTimeout(800);
  const nodesRows = ((await page.textContent('#connectList')) || '').trim();
  ok('填 URL 后标为目录源（与种子分列，不合并成"已连接"）',
     nodesRows.includes('目录源') && nodesRows.includes('127.0.0.1:18787'),
     nodesRows.slice(0, 90));
  const discAfter = ((await page.textContent('#discoveryState')) || '').trim();
  ok('「发现通道」行的自愿公共节点数与连接列表同源',
     discAfter.includes('自愿公共节点 1 个'), discAfter);

  await page.click('[data-disconnect="127.0.0.1:9799"]');
  await page.waitForTimeout(800);
  const afterRemove = ((await page.textContent('#connectList')) || '').trim();
  ok('断开后这条种子从列表消失（目录源不受影响）',
     !afterRemove.includes('127.0.0.1:9799') && afterRemove.includes('18787'),
     afterRemove.slice(0, 60));

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

  // ⑤b R1-5 试用期与样品：**真实交付**才长履历，眼见为实（VISION §1.2 #12）。
  //     挂一份指向**活上游**的供给 → 真的调 10 次 → 控制台必须呈现出"10/10 + 样品"。
  //     反向也验：死上游调用失败，**不占名额、不长样品**（诚实，不虚增履历）。
  const upstream = http.createServer((req, res) => {
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      let got = {}; try { got = JSON.parse(body || '{}'); } catch (_) {}
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ deliverable: '短视频成片包：分镜 3 段 + 字幕 + 配乐',
                               skill: got.skill || '' }));
    });
  });
  await new Promise(r => upstream.listen(0, '127.0.0.1', r));
  const upPort = upstream.address().port;

  await page.evaluate(() => document.getElementById('mount').open = true);
  await page.fill('#agentName', '样品演示 · 短视频成片');
  await page.fill('#agentSkill', 'sample-demo');
  await page.fill('#endpoint', `http://127.0.0.1:${upPort}/a2a/sample-demo`);
  await page.selectOption('#protocol', 'json');
  await page.click('#mountform button[type="submit"], #mountform button:not([type])');
  await page.waitForTimeout(700);
  await page.evaluate(() => { const d = document.getElementById('detail'); if (d && d.open) d.close(); });
  await page.evaluate(() => refresh());
  await page.waitForTimeout(400);

  const callA2A = (sid, mid, text) => page.evaluate(async ({ sid, mid, text }) => {
    const r = await fetch('/a2a/' + encodeURIComponent(sid), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: mid, method: 'message/send',
        params: { message: { role: 'user', messageId: mid, parts: [{ kind: 'text', text }] } } }),
    });
    return r.status;
  }, { sid, mid, text });

  const liveSid = await page.evaluate(() =>
    (snapshot.bindings.find(b => (b.skills || []).includes('sample-demo')) || {}).service_id);
  const deadSid = await page.evaluate(() =>
    (snapshot.bindings.find(b => (b.skills || []).includes('ocr-demo')) || {}).service_id);
  ok('新供给未调用时如实显示 0/10（不虚报进度）',
     (await page.textContent('#bindings')).includes('免费试用 0/10'));

  // 同一单重试 3 次（同 messageId）→ 后端幂等，只算一次
  for (let i = 0; i < 3; i++) await callA2A(liveSid, 'retry-1', '重试同一单');
  await page.evaluate(() => refresh());
  await page.waitForTimeout(400);
  let bt = await page.textContent('#bindings');
  ok('同一单重试 3 次只计 1 次（幂等，不虚增履历）',
     bt.includes('免费试用 1/10'), (bt.match(/免费试用[^<]{0,10}/g) || []).join(' | '));

  // 死上游：调用必然失败 → 不许占名额、不许长样品
  await callA2A(deadSid, 'dead-1', '会失败的调用');
  await page.evaluate(() => refresh());
  await page.waitForTimeout(400);
  const deadCard = await page.evaluate(sid => {
    const art = [...document.querySelectorAll('#bindings article')]
      .find(a => (a.textContent || '').includes('演示 OCR 成品'));
    return art ? art.textContent : '';
  }, deadSid);
  ok('死上游调用失败不占名额（仍 0/10）', (deadCard || '').includes('免费试用 0/10'), deadCard.slice(0, 80));
  ok('死上游调用失败不生成样品（仍「暂无样品」）', (deadCard || '').includes('暂无样品'));

  // 补足到 10 次不同的完成调用 → 满额毕业 + 首批 10 条样品
  for (let i = 0; i < 9; i++) await callA2A(liveSid, 'live-' + i, `第 ${i} 单：做一条夏季促销短视频`);
  await page.evaluate(() => refresh());
  await page.waitForTimeout(500);
  bt = await page.textContent('#bindings');
  ok('满 10 次后显示「免费试用已满 10/10」（毕业）',
     bt.includes('免费试用已满 10/10'), (bt.match(/免费试用[^<]{0,10}/g) || []).join(' | '));
  ok('样品区出现（真实交付沉淀为公开履历）',
     bt.includes('交付样品') && bt.includes('真实调用沉淀'));
  ok('样品带需求摘要', bt.includes('做一条夏季促销短视频'));
  ok('样品带交付预览（就是真实交付的样子）', bt.includes('短视频成片包：分镜 3 段'));
  ok('样品锚定当时的 Agent 版本', bt.includes('v1.0.0'));
  ok('样品标出内容指纹（可核验、不可替换）', /指纹 [0-9a-f]{10}/.test(bt));
  ok('样品块折叠收纳（不占主信息）', await page.locator('#bindings details.samples').count() > 0);
  ok('供给头如实统计「已毕业 1 · 样品 10」',
     /已毕业 1/.test(await page.textContent('#trialBadge')) &&
     /样品 10/.test(await page.textContent('#trialBadge')),
     (await page.textContent('#trialBadge')) || '');

  // ⑤c R1 买方侧：试用声明在**调用前**可见 + 公开样例入口 + 刷新地址 + 试调用先确认（§6.4）。
  //     把本机供给的投影卡当"网络 Agent"导回来，就能在没有真远端的情况下验买方 UI。
  const projOut = await page.evaluate(async (sid) => {
    const card = await (await fetch('/a2a/' + encodeURIComponent(sid) + '/.well-known/agent.json')).json();
    const r = await fetch('/v1/projections', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card }),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }, liveSid);
  ok('把供给投影卡导成「待使用」成功', projOut.status === 201, JSON.stringify(projOut).slice(0, 160));

  await page.click('.navbtn[data-page="find"]');
  await page.evaluate(() => refresh());
  await page.waitForTimeout(500);
  const projArt = await page.evaluate(() => {
    const art = [...document.querySelectorAll('#projections article')]
      .find(a => (a.textContent || '').includes('样品演示'));
    return art ? art.outerHTML : '';
  });
  ok('买方卡在调用前就显示试用声明', /免费试用期|默认形成公开样品/.test(projArt), projArt.slice(0, 100));
  ok('买方卡带「看样例」入口，指向卖方公开面 /public/v1/samples',
     /data-samples="[^"]*\/public\/v1\/samples\?service_id=/.test(projArt));
  ok('买方卡带「刷新地址」（旧收藏有明路，不死在 404）', /data-refresh-projection=/.test(projArt));

  // 刷新地址：本机没有发现通道 → 必须**如实说没配**，不假装成功、不删收藏
  // 刷新地址：本机没有可用发现通道 → 必须**如实说没配/没找到**，不假装成功、不删收藏。
  // 刷新要等发现通道（可能含一个不可达的目录源）超时才回，所以轮询最终提示。
  const pidBefore = await page.evaluate(() => (snapshot.projections[0] || {}).projection_id);
  await page.evaluate(() => { const el = document.querySelector('[data-refresh-projection]'); if (el) el.click(); });
  let refreshNotice = '';
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(300);
    const t = ((await page.textContent('#notice')) || '').trim();
    if (/未能刷新|已核对|已更新到最新地址/.test(t)) { refreshNotice = t; break; }
  }
  ok('刷新地址在找不到/没配置发现通道时如实报告，不假装刷新成功', /未能刷新/.test(refreshNotice), refreshNotice);
  ok('刷新失败不删收藏', await page.evaluate(pid => !!(snapshot.projections.find(p => p.projection_id === pid)), pidBefore));

  // 试调用前必须先弹确认（"交付默认成公开样品"），取消掉 → 不真的发调用
  const callsBefore = await page.evaluate(() => (snapshot.recent_calls || []).length);
  let dialogMsg = '';
  page.once('dialog', async d => { dialogMsg = d.message(); await d.dismiss(); });
  await page.evaluate(() => { const el = document.querySelector('[data-try]'); if (el) el.click(); });
  await page.waitForTimeout(700);
  const callsAfter = await page.evaluate(() => (snapshot.recent_calls || []).length);
  ok('试调用前先弹确认', /继续这次试调用/.test(dialogMsg), dialogMsg);
  ok('确认文案声明「默认形成公开样品」', /默认形成公开样品/.test(dialogMsg), dialogMsg);
  ok('取消确认后不真的发出调用', callsAfter === callsBefore, `${callsBefore} → ${callsAfter}`);

  await new Promise(r => upstream.close(r));

  // ⑥ 截图：每张必须真落盘且非空
  // 先把样品折叠区展开，让"样品可看"留下可视证据（默认是折叠的）。
  await page.evaluate(() => document.querySelectorAll('#bindings details.samples').forEach(d => { d.open = true; }));
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
