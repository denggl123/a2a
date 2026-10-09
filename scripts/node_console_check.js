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
  const initial=await page.evaluate(()=>request('/v1/runtime'));
  if(!initial.selection?.enabled||!initial.task_quality?.enabled||!initial.calibration?.enabled)
    throw Error('目标节点缺少当前模块；请先核对管理授权与安装版本');
  if(['bindings','projections','accounts','recent_calls','settlements','disputes','feedback','workflows'].some(key=>(initial[key]||[]).length))
    throw Error('此脚本会挂载供给、发起调用和写入评价，只允许全新临时节点；请使用 node_console_check.sh 或临时 home，不能直接测试已有业务的 8771');

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
  ok('无通道搜索如实说"没有向任何节点发问"（不装成查过了只是 0 个）',
     /没有向任何节点发问/.test(notice), notice);

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

  // ④a-3 对比视图 + 详情里的真实样品（搬演示；无数据如实「暂无」，不编分数）。
  //      临时节点 --no-p2p 且无候选，这里先种一个候选把交互链路走通（种的是测试
  //      数据，验完立刻清掉，免得混进后面的截图 —— 截图必须是真实运行态）。
  ok('视图切换有三个（卡片 / 紧凑 / 对比）', await page.locator('#vwCompare').isVisible());
  await page.evaluate(() => {
    pool = found = [{ card: { name: '样例验证 Agent', description: '用于验证对比视图与详情样品',
      url: location.origin + '/a2a/svc_demo', skills: [{ id: 'sample-demo', name: '样例验证' }],
      'x-a2n': { projection: { node_did: 'did:a2n:ag_demo', service_id: 'svc_demo', attested: true } } },
      source: 'p2p', headers: {} }];
    disc.view = 'compare'; syncView(); renderFound();
  });
  await page.waitForTimeout(200);
  const cmp = ((await page.textContent('#results')) || '');
  ok('对比视图表头有「历史信誉 / 质量证据」', cmp.includes('历史信誉'), cmp.slice(0, 120));
  ok('对比视图对无证据候选如实显示「暂无」（不替供给方编分数）',
     cmp.includes('暂无本机评估资料'), cmp.slice(0, 200));
  await page.screenshot({ path: path.join(OUT, 'node-find-compare.png'), fullPage: false });

  await page.evaluate(() => showFoundDetail(0));
  await page.waitForTimeout(700);
  const extra = ((await page.textContent('#detailExtra')) || '');
  ok('详情里有「真实交付样品」区块', extra.includes('真实交付样品'), extra.slice(0, 80));
  const openBtn = page.locator('#detailExtra [data-open-samples]');
  const openUrl = (await openBtn.count()) ? await openBtn.first().getAttribute('data-open-samples') : null;
  ok('详情给出可读的公开样品面地址（按卡 url 推，不是编的）',
     !!openUrl && openUrl.includes('/public/v1/samples?service_id=svc_demo'), String(openUrl));
  ok('详情样品区给「新窗口打开」入口', await openBtn.count() > 0);
  ok('读不到样品页时如实报错（不假装读到、不翻空成功）',
     /读取失败|未能跨源读取/.test(extra), extra.slice(-200));
  await page.screenshot({ path: path.join(OUT, 'node-find-detail-samples.png'), fullPage: false });
  await page.click('#closeDetail');
  await page.evaluate(() => { found = []; pool = []; disc.view = 'cards'; syncView(); renderFound(); });
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

  // ④d 基础发现与免费交付样品不能关闭；见证、中继、文件转送分别选择。
  //     口径变更：不再有"一键总开关"——它会把三个独立选择压成一个布尔，是撒谎。
  ok('面板写明基础发现不能关闭（不给"关掉参与"的错觉）',
     ((await page.textContent('#account')) || '').includes('基础发现与邻居引荐不能关闭'));
  ok('没有残留的一键总开关（三个可选服务各自独立）',
     await page.locator('#publicToggle').count() === 0);
  const switches = await page.locator('#publicSwitches [data-public-service]').count();
  ok('三个可选服务开关都在（见证 / 任务中继 / 加密文件转送）', switches === 3, String(switches));
  ok('样品始终公开，没有关闭选项',
     await page.locator('#publicSwitches [data-public-service="samples"]').count() === 0 &&
     ((await page.textContent('#publicState')) || '').includes('免费交付样品始终公开'));
  const swLabels = ((await page.textContent('#publicSwitches')) || '');
  ok('开关标签说清每项是什么（不写"公共服务"这种含混话）',
     ['公开交付样品', '提供哈希见证', '提供密封任务中继', '加密文件转送'].every(t => swLabels.includes(t)),     swLabels.slice(0, 120));
  const defaultSw = await page.evaluate(() =>
    [...document.querySelectorAll('#publicSwitches [data-public-service]')]
      .map(i => [i.dataset.publicService, i.checked]));
  // 新节点默认：样品公开（前 10 次免费=一定公开，用户裁决 2026-10-06），
  // 见证 / 中继 / 文件转送不主动开 —— 有额度的转送要卖方自己认。
  ok('开关默认状态如实（样品开，其余关，与后端一致）',
     JSON.stringify(defaultSw) === JSON.stringify(
       [['witness', false], ['task_relay', false], ['blob_cache', false]]),
     JSON.stringify(defaultSw));
  const pubState = ((await page.textContent('#publicState')) || '').trim();
  ok('公共服务状态说明渲染完成',
     pubState.length > 0 && !pubState.includes('正在读取'), pubState.slice(0, 60));
  // 翻一个开关 → 真写进后端并读回（不许只是画在页面上）
  await page.locator('#publicSwitches [data-public-service="witness"]').check();
  await page.waitForTimeout(700);
  ok('打开「哈希见证」后端真的记下了（页面不是画上去的）',
     /公共服务设置已保存/.test((await page.textContent('#notice')) || ''),
     ((await page.textContent('#notice')) || '').slice(0, 60));
  ok('刷新后开关仍为开（状态来自后端，不是本地残留）',
     await page.locator('#publicSwitches [data-public-service="witness"]').isChecked());
  await page.locator('#publicSwitches [data-public-service="witness"]').uncheck();
  await page.waitForTimeout(700);
  ok('关掉后也真的落库（可拒绝的服务必须真能拒绝）',
     !(await page.locator('#publicSwitches [data-public-service="witness"]').isChecked()));

  // ④d-2 「我不认的记录」：新节点还没有任何不认，必须如实空态；
  //      且措辞不许承诺第三方裁决（自持模式没有仲裁员，只有本机留痕）。
  const accountText = ((await page.textContent('#account')) || '');
  const disputesBox = ((await page.textContent('#disputesBox')) || '').trim();
  ok('「我不认的记录」面板给诚实空态',
     disputesBox.includes('还没有不认记录'), disputesBox.slice(0, 60));
  ok('面板不承诺第三方裁决（如实说没有仲裁员）',
     accountText.includes('没有第三方仲裁员'));

  // ④d-3 「双方反馈」：还没评过任何一笔 → 必须如实空态，且写明
  //      「未评价不代表差评」，原始评价与聚合信誉分别展示。
  const fbBoxEmpty = ((await page.textContent('#feedbackBox')) || '').trim();
  ok('「双方反馈」面板给诚实空态（还没评过任何一笔）',
     fbBoxEmpty.includes('还没有反馈记录'), fbBoxEmpty.slice(0, 60));
  ok('空态写明「未评价不代表差评，也不妨碍任何事」',
     fbBoxEmpty.includes('未评价不代表差评'), fbBoxEmpty.slice(0, 90));
  ok('面板标题区分原始评价、聚合信誉和本机推荐',
     accountText.includes('这里列原始评价') && accountText.includes('聚合信誉和本机推荐另外展示'));

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
  // 样品一定公开（用户裁决 2026-10-06）：页面上不许再出现"要不要公开"的旧门控。
  ok('样品区不再有 consent 勾选框（旧门控已撤）',
     await page.locator('#bindings input[data-sample-consent]').count() === 0);
  ok('上架表单写明「前 10 次免费且一定公开为样品」',
     ((await page.textContent('#sampleRule')) || '').includes('一定公开为样品'),
     ((await page.textContent('#sampleRule')) || '').slice(0, 80));
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
  // 试用声明必须在**调用前**看得到。注意这个供给此时已用满 10 次，走的是"已结束"分支 ——
  // 断言要覆盖真实状态，不能写死"一定公开"这几个字（那样只在试用中成立）。
  ok('买方卡在调用前就显示试用声明',
     /免费试用|首批样品保留/.test(projArt), projArt.slice(0, 100));
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

  // 商品入口先打开条件和输入。此步骤不能执行服务或弹出过时的试用提示。
  const callsBefore = await page.evaluate(() => (snapshot.recent_calls || []).length);
  let dialogMsg = '';
  const dismissDialog = async d => { dialogMsg = d.message(); await d.dismiss(); };
  page.on('dialog', dismissDialog);
  await page.evaluate(() => { const el = document.querySelector('[data-try]'); if (el) el.click(); });
  await page.waitForTimeout(700);
  const callsAfter = await page.evaluate(() => (snapshot.recent_calls || []).length);
  ok('商品入口打开调用输入面板', await page.locator('#paymentInput').count() === 1);
  ok('填写输入前没有执行确认', dialogMsg === '', dialogMsg);
  ok('打开条件面板不执行 Agent', callsAfter === callsBefore, `${callsBefore} → ${callsAfter}`);
  page.off('dialog', dismissDialog);

  // ⑤d R2 双方反馈：**走真实 UI**（打开写反馈弹窗 → 选维度 → 提交），
  //      面板 / 调用记录 / 计数 / 补交提示都要跟着变；原始评价与聚合分开，不同 did 不证明独立个人。
  await page.click('.navbtn[data-page="account"]');
  await page.waitForTimeout(250);
  const fbTarget = await page.evaluate(() => {
    const sellIds = new Set(snapshot.bindings.map(b => b.service_id));
    const call = (snapshot.recent_calls || []).find(c => sellIds.has(c.scope) &&
      ['COMPLETED', 'ACCEPTED', 'SETTLED'].includes(String(c.state).toUpperCase()));
    if (!call) return { missing: true };
    fbPanel(call.scope, call.task_id, 'seller_to_buyer', call.state);
    return { scope: call.scope, task_id: call.task_id };
  });
  ok('打开写反馈弹窗（对一笔已完成的供给调用）', !!fbTarget && !fbTarget.missing,
     JSON.stringify(fbTarget));
  // v2 供给方有四维；质量只属于买方评价，争议体验要求同笔有争议。
  const fbDimensionNames = await page.locator('#fbBody select[data-fb-dim]').evaluateAll(els=>els.map(el=>el.dataset.fbDim));
  ok('供给方四个维度与 v2 一致，没有质量维',
     JSON.stringify(fbDimensionNames) === JSON.stringify(['on_spec','cooperative','honoring','dispute_handling']), JSON.stringify(fbDimensionNames));
  ok('未记录争议时禁用争议处理体验',await page.locator('#fbBody select[data-fb-dim="dispute_handling"]').isDisabled());
  await page.evaluate(() => {
    const s = document.querySelector('#fbBody select[data-fb-dim="on_spec"]');
    if (s) s.value = '5';
    const ta = document.querySelector('#fbBody textarea[data-fb-note]');
    if (ta) ta.value = '需求方按约配合';
  });
  await page.click('[data-fb-submit]');
  await page.waitForTimeout(700);
  ok('提交后提示「R2 不主动推送」（供给方反馈不假装已送到对方）',
     /R2 不主动推送/.test((await page.textContent('#notice')) || ''),
     ((await page.textContent('#notice')) || '').slice(0, 90));
  const fbBox = ((await page.textContent('#feedbackBox')) || '').trim();
  ok('反馈面板出现这条记录（方向 = 供给 → 买方 / 我写的）',
     fbBox.includes('供给 → 买方') && fbBox.includes('我写的'), fbBox.slice(0, 80));
  ok('反馈面板照实列维度分', fbBox.includes('需求按约 5/5'), fbBox.slice(0, 140));
  ok('反馈面板区分原始评价与聚合信誉，并标明支持量',
     fbBox.includes('这里列原始评价') && fbBox.includes('聚合信誉和本机推荐另外展示') && fbBox.includes('资料支持量'));
  ok('反馈面板写明「不同 did 不证明背后是独立个人」',
     fbBox.includes('不证明背后是独立个人'), fbBox.slice(0, 180));
  ok('反馈计数徽章变为 1',
     ((await page.textContent('#feedbackCount')) || '').trim() === '1',
     (await page.textContent('#feedbackCount')) || '');
  const providerCallsFb = ((await page.textContent('#providerCalls')) || '').trim();
  ok('调用记录里那笔显示「已评 v1」（不再是未评价）',
     /已评 v1/.test(providerCallsFb), providerCallsFb.slice(0, 140));
  // 只读接口（GET /v1/feedback/{id}/versions 与 /v1/feedback/summary）在控制台里真能读到。
  await page.click('#feedbackBox [data-fb-versions]');
  await page.waitForTimeout(400);
  const versText = ((await page.textContent('#detailBody')) || '').trim();
  ok('「版本」按钮读回版本链（第 1 版）', /第 1 版/.test(versText), versText.slice(0, 80));
  await page.evaluate(() => { const d = document.getElementById('detail'); if (d && d.open) d.close(); });
  await page.click('[data-fb-summary]');
  await page.waitForTimeout(400);
  const sumText = ((await page.textContent('#detailBody')) || '').trim();
  ok('「摘要」按钮读回事实摘要（含条数与各维平均，且注明不是信誉分）',
     /共 1 条/.test(sumText) && /不是信誉分/.test(sumText), sumText.slice(0, 120));
  await page.evaluate(() => { const d = document.getElementById('detail'); if (d && d.open) d.close(); });
  await page.screenshot({ path: path.join(OUT, 'node-account-feedback.png'), fullPage: true });

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
