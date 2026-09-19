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
  // 上架名额（"允许被发现的数量"）：demo 的免费档声明了名额 3，行里该显示占用量。
  // 这条同时钉住"服务端的 seats 真投影到界面了" —— 少一个字段它就会红。
  const seatRows = await page.locator('#d_table .agent-row:has-text("名额 ")').count();
  ok('找 Agent：有名额的 agent 显示「名额 已占/上限」', seatRows >= 1,
     `带名额徽标的行=${seatRows}`);
  ok('找 Agent：不限名额的不挂"名额 不限"噪声徽标',
     (await page.locator('#d_table .badge:has-text("名额 不限")').count()) === 0);
  const discHtml = await page.locator('#d_table').innerHTML();
  ok('找 Agent：每行带试用状态（试用中 N/10 · 免费 / 已毕业）',
     discHtml.includes('试用中 ') || discHtml.includes('已毕业'));
  ok('找 Agent：试用/毕业徽标有解释（鼠标悬停能看懂判定）',
     discHtml.includes('免费期：') || discHtml.includes('免费期已走完'));

  // ---------- 分类筛选 ----------
  // 盯的是**界面上真的长出来的 optgroup**，不是源码里的映射表 ——
  // 映射表在纯逻辑层（console_logic_check）已经钉过；这两层不可互相替代。
  const catLabels = await page.locator('#disc_skill optgroup').evaluateAll(
    els => els.map(e => e.getAttribute('label')));
  ok('找 Agent：能力筛选按行业分类分组（optgroup 真渲染出来了）',
     catLabels.length >= 3, `分组=${JSON.stringify(catLabels)}`);
  ok('找 Agent：分类覆盖视频与法务两类（行业在前、平台节点在后）',
     catLabels.includes('影视与视频') && catLabels.includes('法务与合同'),
     JSON.stringify(catLabels));
  const firstOpt = (await page.locator('#disc_skill option').first().textContent()) || '';
  ok('找 Agent：未选时显示「全部分类」（不再叫「全部能力」）',
     firstOpt.trim() === '全部分类', firstOpt.trim());
  const catAllOpts = await page.locator('#disc_skill option[value^="cat:"]').count();
  ok('找 Agent：每个分类都能整体筛（cat: 选项在位）',
     catAllOpts >= 3, `cat: 选项=${catAllOpts}`);

  await page.selectOption('#disc_skill', 'cat:文档与识别');
  await page.waitForTimeout(600);
  const ocrRows = await page.locator('#d_table .agent-row').count();
  ok('找 Agent：选一个分类真的把列表筛窄了',
     ocrRows >= 1 && ocrRows <= rows, `${ocrRows}/${rows} 行`);
  await page.selectOption('#disc_skill', 'cat:影视与视频');
  await page.waitForTimeout(600);
  const videoRows = await page.locator('#d_table .agent-row').count();
  const videoHtml = await page.locator('#d_table').innerHTML();
  ok('找 Agent：影视与视频分类下留下的是短视频那些，不是 OCR 档',
     videoRows >= 1 && videoHtml.includes('短视频') && !videoHtml.includes('OCR 识别'),
     `${videoRows} 行`);
  await page.selectOption('#disc_skill', '');
  await page.waitForTimeout(500);

  // ---------- 一格价格里并排多种币种 ----------
  // 收费档同时挂了 CNY 与 USDC 两条独立挂牌：主价照旧是人民币那一句，
  // 其余币种以小字并排 —— 不换算、不相加（跨币种相加就是编汇率）。
  const priceHtml = await page.locator('#d_table').innerHTML();
  const altCount = await page.locator('#d_table .price-alt').count();
  ok('找 Agent：价格列把多币种小字并排渲染出来（不是只显示主价）',
     altCount >= 1, `.price-alt=${altCount}`);
  ok('找 Agent：并排的小字里能看到 USDC 挂牌价',
     priceHtml.includes('USDC/次'), priceHtml.includes('USDC/次') ? '' : '没看到 USDC 价');
  ok('找 Agent：主价仍是人民币那一句（不是把两个币种堆在一起当主价）',
     /price-chip">¥[\d.]+/.test(priceHtml),
     (priceHtml.match(/price-chip">[^<]{0,40}/) || [''])[0]);
  ok('找 Agent：两个币种分行、不出现相加出来的合计数',
     !/\+\s*USDC/.test(priceHtml) && !/USDC\s*\+/.test(priceHtml));

  // 卡片自证闸（P2）：发现和可用必须是同一件事 —— "在你列表里"就等于"验过了"。
  // demo 四档节点都是自签卡，所以它们该拿到绿徽标；"未自证"那条路走不通就
  // 说明闸门没生效（界面上会退回成"按自报信任"）。
  ok('找 Agent：给「仅已自证身份」筛选项', find.includes('仅已自证身份'));
  ok('找 Agent：已自证的节点拿到「可直接调用」绿徽标',
     discHtml.includes('可直接调用 · '), discHtml.includes('可直接调用 · ') ? '' : '一行都没有');
  // 结论必须是**服务端随行带出的**（不是前端猜的）：demo 四档都是自签卡
  const proofApi = await page.evaluate(async () => {
    const js = await (await fetch('/v1/registry/agents')).json();
    return js.map(a => ({ sp: a.selfproof, v: a.card_verified, why: a.card_verify_reason }));
  });
  ok('找 Agent：自证结论来自服务端字段（selfproof/card_verified）',
     proofApi.length >= 3 && proofApi.every(a => a.sp === 'signed' && a.v === true),
     JSON.stringify(proofApi.slice(0, 2)).slice(0, 120));
  ok('找 Agent：已自证带得出结论的来由（不是只有一颗徽标）',
     proofApi.every(a => (a.why || '').length > 0));
  const verifiedFilter = page.locator('#d_pay button:has-text("仅已自证身份")');
  if (await verifiedFilter.count()) {
    await page.locator('#discovery_filters > summary').click();
    await verifiedFilter.click();
    await page.waitForTimeout(700);
    const vRows = await page.locator('#d_table .agent-row').count();
    const vHtml = await page.locator('#d_table').innerHTML();
    ok('找 Agent：仅已自证 → 留下的每行都可直接调用（筛掉了未自证的）',
       vRows >= 1 && vRows <= rows && vHtml.includes('可直接调用 · '), `${vRows}/${rows} 行`);
    await verifiedFilter.click();               // 复位，别影响后面的走查
    await page.waitForTimeout(600);
  }

  // ---------- 试调用面板 ----------
  await page.locator('#d_table .detail-action').first().click();
  await page.locator('#dr_body .service-actions button:has-text("试调用")').click();
  await page.waitForTimeout(400);
  const call = await page.locator('#f_call').innerHTML();
  ok('试调用：技能改为下拉', call.includes('<select id="f_call_skill"'));
  ok('试调用：手填参数收进「高级」',
     call.includes('高级：指定技能 id / 手填参数') && call.includes('<details'));

  // ---------- 信息架构：三个主入口，入口内再分小页 ----------
  const navTabs = (await page.locator('nav > button.tabbtn').allInnerTexts()).filter(Boolean);
  ok('主入口只有三个（上手难度=入口个数）', navTabs.length === 3, navTabs.join(' / '));
  const subFind = (await page.locator('#find .subnav .subbtn').allInnerTexts())
    .map(t => t.split('·')[0].trim()).filter(Boolean);
  ok('找 Agent 三小页：发现 / 待使用 / 调用记录', subFind.length === 3, subFind.join(' | '));

  // 先收藏两个再往下走 —— 这是截图脚本踩出来的真实顺序，也是唯一能踩到
  // "空列表掩盖后端错误"的顺序：待使用空着时，后端回表循环根本不执行。
  // （曾经收藏非空 → /v1/console/managed 500 → 界面显示"你还没有发起过调用"。）
  const addBtns = page.locator('#d_table button:has-text("加入待使用")');
  const addN = Math.min(2, await addBtns.count());
  for (let i = 0; i < addN; i++) {
    await addBtns.nth(i).click();
    await page.waitForTimeout(600);
  }

  // 待使用：收藏 + 自建聚合合并成一页（用户提的正是这一点）
  await page.locator('#find .subbtn[data-sec="hold"]').click();
  await page.waitForTimeout(600);
  const hold = await page.locator('#f_hold').innerText();
  ok('待使用：收藏与自建聚合在同一页', hold.includes('收藏') && hold.includes('自建聚合'));
  ok('待使用：聚合可维护（导入收藏 / 导出配置 / 加候选）',
     hold.includes('把收藏一键导进来') && hold.includes('导出 Python 配置') && hold.includes('添加候选'));
  ok('待使用：策略与冷却可调', hold.includes('策略') && hold.includes('冷却基数'));
  const holdRows = await page.locator('#f_hold .agent-row').count();
  ok('待使用：收藏真的落到名单里（不是空态）', holdRows >= addN && addN > 0, `${holdRows} 行`);

  // 调用记录：点条目开明细抽屉（本次改版的核心交互）
  await page.locator('#find .subbtn[data-sec="calls"]').click();
  await page.waitForTimeout(1200);
  const myCalls = await page.locator('#f_calls').innerText();
  ok('调用记录：渲染出来', myCalls.includes('调用记录') && !myCalls.includes('加载失败'));
  ok('调用记录：收藏非空时也照常出数（不再把 500 翻成空态）',
     !myCalls.includes('这是取数失败'), myCalls.split('\n').filter(Boolean)[2] || '');
  const callRows = await page.locator('#f_calls tbody tr').count();
  ok('调用记录：有可点条目', callRows >= 1, `${callRows} 行`);
  if (callRows >= 1) {
    await page.locator('#f_calls tbody tr').first().click();
    await page.waitForTimeout(1200);
    const dr = await page.locator('#dr_body').innerText();
    ok('明细抽屉：打开且给出概览', dr.includes('任务') && dr.includes('能力') && dr.includes('预算'),
       dr.split('\n').filter(Boolean).slice(0, 2).join(' | ').slice(0, 90));
    ok('明细抽屉：流程取自凭证链（或如实说没有）',
       dr.includes('凭证链') || dr.includes('暂无盖章记录'));
    ok('明细抽屉：原始数据可展开', dr.includes('原始数据'));
    ok('明细抽屉：说清我是哪一方', dr.includes('我是使用方') || dr.includes('我是供给方'));
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
    ok('明细抽屉：可关闭', !((await page.locator('#drawer').getAttribute('class')) || '').includes('on'));
  }

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

  // 账户页只留两小页：账户信息 / 流水列表（调用记录已上移到「找 Agent」）
  const subAcct = (await page.locator('#account .subnav .subbtn').allInnerTexts())
    .map(t => t.split('·')[0].trim()).filter(Boolean);
  ok('我的账户两小页：账户信息 / 流水列表', subAcct.length === 2, subAcct.join(' | '));
  ok('账户页：不再有「我的任务」折叠块（已上移为调用记录）',
     !acct.includes('<summary><b>我的任务</b>'));
  ok('账户页：流水只在小页里出现一次', (acct.match(/id="ledger_body"/g) || []).length === 1);

  await page.locator('#account .subbtn[data-sec="flow"]').click();
  await page.waitForTimeout(900);
  const led = await page.locator('#ledger_body').innerText();
  console.log('    [流水预览] ' + led.split('\n').filter(Boolean).slice(0, 4).join(' | ').slice(0, 220));
  ok('账户页：流水渲染出内容（不是报错页）', led.length > 0 && !led.includes('加载失败'));
  const ledRows = await page.locator('#ledger_body tbody tr').count();
  ok('流水列表：行可点（明细入口）', ledRows >= 1, `${ledRows} 行`);
  if (ledRows >= 1) {
    await page.locator('#ledger_body tbody tr').first().click();
    await page.waitForTimeout(1000);
    const ldr = await page.locator('#dr_body').innerText();
    ok('流水明细：金额给读数 + 最小单位原值',
       ldr.includes('最小单位') && ldr.includes('关联凭证'));
    ok('流水明细：说清这笔账是什么', ldr.includes('直付') || ldr.includes('对等账户'));
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  // ---------- 高级功能：分组 + 演示开关隔离 ----------
  const more = await page.locator('#more').innerHTML();
  ok('高级功能：按「运营 / 审计·凭证」分组',
     more.includes('运营') && more.includes('审计 / 凭证'));
  ok('高级功能：默认收起，不打扰主流程', !(await page.locator('#more').isVisible()));
  await page.locator('#moreBtn').click();
  await page.locator('#more button[data-tab="overview"]').click();
  await page.waitForTimeout(1000);
  const ov = await page.locator('#overview').innerHTML();
  ok('全网总览：演示操作收进折叠区', ov.includes('演示操作') && ov.includes('<details'));
  ok('全网总览：不再有裸露的主操作按钮',
     !/<div class="row">\s*<button onclick="quickDeposit\(\)"/.test(ov));

  // ---------- 高级功能：结算与对账（P1 §3.2 的落地页）----------
  // 这页的价值全在**口径**上：三个数（应结/已结/待处理）各自什么定义、
  // 金额按币种分行（跨币种相加得到的数没有解释力）。渲染不出来或退化成
  // "加载失败"都等于这一节没做。
  await page.locator('#more button[data-tab="settle"]').click();
  await page.waitForTimeout(1300);
  // 先断言"真的显示出来了"。这一条不是废话：innerText 在 display:none 时按规范**回退成
  // textContent**，所以下面那些文本断言在"section 被 hide 藏着"时也会全绿。
  // 结算页曾经就漏在 tab() 的显示白名单外 —— 文本断言全过，靠截图那一层才抓到。
  ok('结算与对账：切过去后真的显示出来（不是被 hide 藏着的）',
     await page.locator('#settle').isVisible());
  const st = await page.locator('#settle').innerText();
  ok('结算与对账：渲染出来（不是加载失败）',
     st.length > 0 && !st.includes('加载失败'), st.split('\n').filter(Boolean)[1] || '');
  ok('结算与对账：三个数与各自口径在位',
     st.includes('今日应结') && st.includes('今日已结') && st.includes('待处理')
     && st.includes('通过验收') && st.includes('未结清的笔数'));
  // 口径纪律要按**数据行**判：说明文案里本来就会写"而不是给一个合计"，
  // 全文搜"合计"会把那句解释误判成违规。
  const curRows = await page.locator('#settle table').first().locator('tbody tr').allInnerTexts();
  const mixed = curRows.filter(t => (t.includes('CNY') && t.includes('USDC')) || /合计|总计/.test(t));
  ok('结算与对账：金额按币种各占一行，单行不混币种、无跨币种合计',
     curRows.length >= 1 && mixed.length === 0, `${curRows.length} 个币种行`);
  ok('结算与对账：对账状态可见（平衡/差异/还没对过账）',
     st.includes('平衡') || /差\s*-?\d+/.test(st) || st.includes('还没对过账'));
  const stRows = await page.locator('#settle tbody tr').count();
  ok('结算与对账：有数据行（smoke 造过多笔结算与一笔日切）', stRows >= 1, `${stRows} 行`);

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

  // ---------- 卖 Agent：另外两小页（他人调用记录 / 行情信息） ----------
  // 他人调用记录是**供给方视角**：换成有 agent 被调用过的 bob，
  // 否则永远只测到空态（空态好看不代表功能成立）。
  await page.fill('#principal', 'acct:bob');
  await page.locator('header button:has-text("刷新")').click();
  await page.waitForTimeout(1500);

  const subSell = (await page.locator('#sell .subnav .subbtn').allInnerTexts())
    .map(t => t.split('·')[0].trim()).filter(Boolean);
  ok('卖 Agent 三小页：自售列表 / 他人调用记录 / 行情信息', subSell.length === 3, subSell.join(' | '));
  ok('自售列表：bob 名下的 agent 在位', (await page.locator('#sell tbody tr').count()) >= 1);
  const sellHtml = await page.locator('#sell').innerHTML();
  ok('自售列表：每行带试用状态（试用中 N/10 · 免费 / 已毕业）',
     sellHtml.includes('试用中 ') || sellHtml.includes('已毕业'));

  // Agent 明细抽屉：四段证据分开呈现、绝不合成一个总分（本次改版的重点）
  if ((await page.locator('#sell tbody tr').count()) >= 1) {
    await page.locator('#sell tbody tr').first().click();
    await page.waitForTimeout(1600);
    const adr = await page.locator('#dr_body').innerText();
    ok('Agent 明细：试用/毕业状态挂在头部',
       adr.includes('试用中 ') || adr.includes('已毕业'));
    ok('Agent 明细：四段证据都在位',
       adr.includes('① 客观表现') && adr.includes('② 质量偏差') &&
       adr.includes('③ 使用评价') && adr.includes('④ 案例'));
    ok('Agent 明细：明确说明不合成总分', adr.includes('四段各报各的，不做加总'));
    ok('Agent 明细：客观表现给口径与样本数',
       adr.includes('平台探测') && adr.includes('使用端实测') && adr.includes('计量签名'));
    ok('Agent 明细：案例声明排除自源、不暴露调用方身份',
       adr.includes('已排除自源调用'));
    ok('Agent 明细：样本不够时给原因不给空白',
       adr.includes('/ 100') || adr.includes('还没到能下结论的时候'));
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  await page.locator('#sell .subbtn[data-sec="calls"]').click();
  await page.waitForTimeout(1200);
  const pc = await page.locator('#s_calls').innerText();
  ok('他人调用记录：渲染出来（不是加载失败）', pc.includes('他人调用记录') && !pc.includes('加载失败'));
  const pcRows = await page.locator('#s_calls tbody tr').count();
  ok('他人调用记录：别人的调用列出来了', pcRows >= 1, `${pcRows} 行`);
  if (pcRows >= 1) {
    await page.locator('#s_calls tbody tr').first().click();
    await page.waitForTimeout(1200);
    const pdr = await page.locator('#dr_body').innerText();
    ok('他人调用明细：以供给方视角打开（看得见调用方）',
       pdr.includes('我是供给方') && pdr.includes('调用方'));
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

  await page.locator('#sell .subbtn[data-sec="market"]').click();
  await page.waitForTimeout(1200);
  const mk = await page.locator('#s_market').innerText();
  ok('行情信息：渲染出来且声明只发行情不定价',
     mk.includes('行情信息') && mk.includes('只发行情不定价'));
  const mkRows = await page.locator('#s_market tbody tr').count();
  ok('行情信息：有能力行', mkRows >= 1, `${mkRows} 行`);
  if (mkRows >= 1) {
    await page.locator('#s_market tbody tr').first().click();
    await page.waitForTimeout(1200);
    const mdr = await page.locator('#dr_body').innerText();
    ok('行情明细：挂牌与成交分列（意愿 vs 事实）',
       mdr.includes('挂牌区间') && mdr.includes('成交均价'));
    ok('行情明细：供给节点清单在位', mdr.includes('供给节点'));
    await page.locator('#dr_head button').click();
    await page.waitForTimeout(300);
  }

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
