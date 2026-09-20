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
// 每轮先把上一轮的截图清掉。不然改了名 / 删了页之后，旧图还躺在目录里冒充
// **这一轮的**证据 —— 2026-09-19 分类改名后就真出现过：目录里同时留着
// 「按分类筛选（数据转换）」和「（影视与视频）」两张 01c，旧的那张指向一个
// 已经不存在的分类。清掉的只是本目录的 .png（本脚本自己的产物），不碰别的。
const stale = fs.readdirSync(OUT).filter(f => /\.png$/i.test(f));
for (const f of stale) fs.unlinkSync(path.join(OUT, f));
if (stale.length) console.log(`  · 清掉上一轮残留截图 ${stale.length} 张`);
let shotCount = 0;   // 报出来的张数必须是**真拍到的**，不是写死的一个数
// 空态截图作为"走查证据"是不成立的：截了十几张全是"还没有…"，人眼复核会以为
// 功能没问题。所以哪页该有数据却没有，就在这里记一笔，最后一起报错退出。
const empty = [];

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1100 }, deviceScaleFactor: 1.5 });
  const shot = async (sel, name) => {
    // Windows 上 `/` 是路径分隔符：图名里带一个斜杠，图就会掉进嵌套目录，
    // 产物清单看着"有十几张"，实际有一个在别人的子目录里。挡住。
    if (/[\\/]/.test(name)) throw new Error('截图文件名不能含路径分隔符: ' + name);
    const el = page.locator(sel);
    // 元素"存在但被 hide 藏着"时，scrollIntoViewIfNeeded 会一直重试到超时（几十秒），
    // 报错只说 element is not visible，看不出是谁没显示。先显式判可见，把选择器直接报出来。
    if (!(await el.isVisible())) {
      throw new Error(`截图目标不可见（被 hide 藏着？）: ${sel} → ${name}`);
    }
    await el.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await el.screenshot({ path: path.join(OUT, name) });
    shotCount += 1;
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
  // 分类筛选：把「全部能力」换成按**行业**分类之后，选一个分类应当只留下该行业的服务。
  // 下拉本身是原生 select（截图拍不到展开的选项），所以截的是**筛完之后的结果** ——
  // 人眼要能看出"这一屏只剩短视频那类，没有 OCR 档"。
  await page.selectOption('#disc_skill', 'cat:影视与视频');
  await page.waitForTimeout(800);
  await shot('#find', '01c-找Agent-按分类筛选（影视与视频）.png');
  const catRows = await page.locator('#d_table .agent-row').count();
  if (catRows === 0) empty.push('按分类筛选后一行都没有（影视与视频分类里应当有服务）');
  await page.selectOption('#disc_skill', '');
  await page.waitForTimeout(600);
  // 卡片自证（P2）：点「仅已自证身份」后留下的每一行都必须带"可直接调用"。
  // 单截一张，人眼复核"未自证 ≠ 验过"这条在界面上真的说清了 —— 否则
  // "在你列表里"很容易被读成"平台验过了"。
  await page.locator('#discovery_filters > summary').click();
  await page.locator('#d_pay button:has-text("仅已自证身份")').click();
  await page.waitForTimeout(600);
  await shot('#find', '01b-找Agent-卡片自证（仅已自证 · 可直接调用徽标）.png');
  await page.locator('#d_pay button:has-text("仅已自证身份")').click();   // 复位，后续步骤不受影响
  await page.waitForTimeout(500);
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
  // 「自售列表 / 他人调用记录」都是供给方视角，**不切身份**：就用控制台开箱的
  // 默认身份截。一个身份本来就既买又卖 —— 货架与"别人调我的单"都挂在它名下，
  // 那才是人点进来看到的样子。
  // 上一版在这里先 fill('#principal','acct:bob') 再截，于是"开箱时自售列表是不是
  // 空的"这一条**截图从来没拍到过**：人眼看到空、证据里却摆着 bob 的货架
  // （2026-09-19 假证据，与 ui_check 的假绿同源）。bob 的供给视角改由
  // console_polish_check.js 覆盖，不在这里重复。
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
    empty.push('自售列表（默认身份开箱就该有自己的货架，空态即回归）');
  }
  await sub('#sell', 'calls', 1300);
  await shot('#s_calls', '06-卖Agent-他人调用记录.png');
  const pcRows = await page.locator('#s_calls tbody tr').count();
  console.log('    [他人调用记录] ' + pcRows + ' 行');
  if (pcRows === 0) empty.push('他人调用记录（smoke 让 dave 调过默认身份上架的档，应有 1 笔）');
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
  // 页头已无身份控件，账户页就按**开箱主体**截（不再需要"先切回 alice"这一步）。
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
  // 达成交易挂在配对关系上：借 dave（配对是 smoke 建的）。页头已无身份控件，
  // 用 <body data-principal> 这个**取数常量**切过去 —— 它是测试取值点，不是界面入口。
  await page.evaluate(() => { document.body.dataset.principal = 'acct:dave'; });
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

  // 结算与对账（P1 §3.2）：三个数（应结/已结/待处理）+ 金额按币种分行 + 待处理清单。
  // 人眼在这页要看的是"金额有没有被跨币种加成一个总数"（那正是刻意不做的事），
  // 以及"取数失败"有没有被偷换成"还没有结算"的空态。
  await page.locator('#more button[data-tab="settle"]').click();
  await page.waitForTimeout(1400);
  await shot('#settle', '14-高级功能-结算与对账（三数·币种分行·待处理）.png');
  const settleTxt = await page.locator('#settle').innerText();
  console.log('    [结算与对账] ' + settleTxt.split('\n').filter(Boolean).slice(0, 4).join(' | ').slice(0, 150));
  if (settleTxt.includes('加载失败')) empty.push('结算与对账 取数失败');
  else if (settleTxt.includes('今天还没有已结的账') && settleTxt.includes('没有待处理的结算'))
    // 「今天」按 **UTC** 算（写库同源，不许改成本地日），而 smoke 造的账可能落在
    // 昨天 —— 跑测正好跨过 UTC 零点时就会这样（北京时间 08:00 前后很常见）。
    // 那种空态是**事实**不是回归；先重跑一次 smoke 再截，别去查结算链路。
    empty.push('结算与对账（smoke 造过多笔结算，应有数据）'
               + ' —— 若本次跑测跨过 UTC 零点（北京时间 08:00 前后），'
               + 'smoke 的账落在「昨天」：重跑一次 smoke 再截即可');
  // 按**数据行**判，别全文搜"合计"：说明文案里本来就有"而不是给一个合计"这句解释。
  const curLines = await page.locator('#settle table').first().locator('tbody tr').allInnerTexts();
  if (curLines.some(t => (t.includes('CNY') && t.includes('USDC')) || /合计|总计/.test(t)))
    empty.push('结算页的金额行跨币种合并了（口径违规）');

  await browser.close();
  if (empty.length) {
    console.error('✗ 以下页面截到的是空态，作为证据不成立：\n  - ' + empty.join('\n  - '));
    process.exit(1);
  }
  // 报的数是真拍到的张数，也和目录里实际剩下的文件核对一遍 —— 对不上就说明
  // 目录里混进了别的东西（或者有图没写成），"17 张"这种写死的数会骗人。
  const onDisk = fs.readdirSync(OUT).filter(f => /\.png$/i.test(f)).length;
  if (onDisk !== shotCount) {
    console.error(`✗ 目录里有 ${onDisk} 张、本轮实拍 ${shotCount} 张，对不上（有残留或漏写）`);
    process.exit(1);
  }
  console.log(`✓ 截图输出到 ${OUT}（${shotCount} 张，逐页都非空）`);
})();
