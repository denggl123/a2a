/* 控制台**纯逻辑**体检：把 console.html 里的脚本在 vm 里跑起来（DOM 全用桩），
   然后断言价格换算、能力判定、筛选、信誉分档这些不经浏览器的逻辑。
   与 console_js_check.js（只查语法）互补：那个防手滑，这个防改坏。
   用法：node scripts/console_logic_check.js
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'packages', 'a2n-server',
                        'src', 'a2n_server', 'web', 'console.html');
const html = fs.readFileSync(FILE, 'utf8');
const code = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).join('\n');

// ---- 假的 DOM：任何取元素都返回一个"什么都能点、什么都能读"的桩 ----
function fakeEl() {
  const el = {
    value: '', innerHTML: '', textContent: '', checked: false, disabled: false,
    readOnly: false, dataset: {}, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, remove() {}, scrollIntoView() {},
    querySelectorAll() { return []; }, querySelector() { return fakeEl(); },
    addEventListener() {}, focus() {},
  };
  return new Proxy(el, {
    get(t, k) { return k in t ? t[k] : () => {}; },
    set() { return true; },
  });
}
const ctx = {
  console,
  document: {
    getElementById: () => fakeEl(),
    querySelector: () => fakeEl(),          // dataset.tab 为 undefined → loadAll 不派发任何渲染
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    createRange: () => ({ selectNodeContents() {} }),
    execCommand: () => true,
    addEventListener() {},                  // Esc 关抽屉/关表单挂在 document 上
  },
  window: {},
  localStorage: { getItem: () => null, setItem() {} },
  navigator: {}, crypto: {}, performance: { now: () => 0 },
  alert: () => {}, fetch: () => Promise.reject(new Error('no-network-in-check')),
  setTimeout: () => 0, getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
};
ctx.globalThis = ctx;
vm.createContext(ctx);

// 把被测符号挂到 globalThis 上（function 声明本就是全局；const 需要显式导出）
vm.runInContext(code + `
;globalThis.curExp = curExp;
globalThis.minorToInput = minorToInput;
globalThis.minorText = minorText;
globalThis.priceMinorFrom = priceMinorFrom;
globalThis.amountStr = amountStr;
globalThis.discCard = _discCard;
globalThis.discCap = _discCap;
globalThis.discProof = _discProof;
globalThis.discFiltered = _discFiltered;
globalThis.priceText = _priceText;
globalThis.priceList = _priceList;
globalThis.skillCategory = _skillCategory;
globalThis.categoryOrder = CATEGORY_ORDER;
globalThis.categoryRest = CATEGORY_REST;
globalThis.callPrice = _callPrice;
globalThis.repTier = _repTier;
globalThis.shortId = _shortId;
globalThis.discState = _disc;
globalThis.subs = SUBS;
globalThis.subState = _sub;
globalThis.evLabel = EV_LABEL;
globalThis.taskStateBadge = _taskStateBadge;
globalThis.settleView = _settleView;
globalThis.settleError = _settleError;
globalThis.adv = _ADV;
globalThis.netAgentView = _netAgentView;
`, ctx, { filename: 'console.html' });

const g = ctx.globalThis;
let pass = 0;
const fails = [];
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; return; }
  fails.push(`${name}: 得到 ${JSON.stringify(got)}，期望 ${JSON.stringify(want)}`);
}
function ok(name, cond) { cond ? pass++ : fails.push(`${name}: 断言为假`); }

// ① 价格输入按主单位（P1-6）：3 / ¥0.03 / 0.03 都能读
eq('priceMinorFrom 0.03 CNY', g.priceMinorFrom('0.03', 'CNY'), 3);
eq('priceMinorFrom ¥0.03', g.priceMinorFrom('¥0.03', 'CNY'), 3);
eq('priceMinorFrom 3 元', g.priceMinorFrom('3', 'CNY'), 300);
eq('priceMinorFrom 千分位', g.priceMinorFrom('1,234.5', 'CNY'), 123450);
eq('priceMinorFrom 空=免费', g.priceMinorFrom('', 'CNY'), 0);
eq('priceMinorFrom USDC', g.priceMinorFrom('5', 'USDC'), 5000000);
ok('priceMinorFrom 非数字→NaN', Number.isNaN(g.priceMinorFrom('abc', 'CNY')));
ok('priceMinorFrom 负数→NaN', Number.isNaN(g.priceMinorFrom('-1', 'CNY')));
eq('minorToInput 3分→0.03', g.minorToInput('CNY', 3), '0.03');
eq('minorToInput 5USDC', g.minorToInput('USDC', 5000000), '5');
eq('minorText ¥0.03', g.minorText('CNY', 3), '¥0.03');
eq('minorText USDC', g.minorText('USDC', 5000000), '5 USDC');
eq('curMoney 3分', g.curMoney(3, 'CNY'), '¥0.03');
// 尾零：USDC 别显示 0.050000；本位币保底两位小数（¥0 显示 ¥0.00 而不是 ¥0）
eq('curMoney USDC 去尾零', g.curMoney(50000, 'USDC'), '0.05 USDC');
eq('curMoney USDC 整数', g.curMoney(5000000, 'USDC'), '5 USDC');
eq('curMoney CNY 保两位', g.curMoney(300, 'CNY'), '¥3.00');
eq('curMoney CNY 零', g.curMoney(0, 'CNY'), '¥0.00');
eq('curMoney JPY 无小数', g.curMoney(5, 'JPY'), '¥5');

// ② 价目事实只在 card：v2 价目优先于 v1 提示价，都没有=免费
// selfproof 默认 'signed'：这些样本代表"已自证身份"的正常节点；
// 卡片自证闸（未自证 / 验不过）另有针对性断言（见 ⑪）。
const mk = o => ({ agent_id: o.id || 'ag_1', name: o.name || 'n',
                   card_json: JSON.stringify(o.card || {}), status: o.status || 'ACTIVE',
                   reputation: o.rep == null ? 0.5 : o.rep,
                   region: o.region, accepts: (o.card || {}).accepts,
                   selfproof: o.selfproof || 'signed',
                   card_verify_reason: o.selfproof === 'unattested'
                     ? '卡未自证身份（x-a2n.sovereign 为空）'
                     : '已自证（身份=公钥指纹，签名有效）' });
const freeCard = mk({ id: 'ag_free', card: { skills: [{ id: 'ocr-pro' }] } });
const bookCard = mk({ id: 'ag_book', card: { skills: [{ id: 'ocr-pro' }], accepts: ['peer_account'],
  'x-a2n': { price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'call_count', amount: 3, per: 1 }] } } } } } });
const hintCard = mk({ id: 'ag_hint', card: { skills: [{ id: 'ocr-pro' }], accepts: ['direct_pay:alipay'],
  'x-a2n': { price_hint: { 'ocr-pro': { amount: 1 } } } } });
eq('免费卡 callPrice', g.callPrice(g.discCard(freeCard)), { cur: null, minor: 0 });
eq('价目卡 callPrice', g.callPrice(g.discCard(bookCard)), { cur: 'CNY', minor: 3 });
eq('提示价卡 callPrice', g.callPrice(g.discCard(hintCard)), { cur: 'CNY', minor: 1 });
eq('priceText 免费', g.priceText(g.discCard(freeCard)), '免费');
eq('priceText ¥0.03/次', g.priceText(g.discCard(bookCard)), '¥0.03/次');

// ③ 能力判定（与服务端门禁同语义）：免费/x402/已配对/已绑渠道
eq('免费可直调', g.discCap(freeCard, g.discCard(freeCard)).ok, true);
eq('收费未准备不可调', g.discCap(hintCard, g.discCard(hintCard)).ok, false);
g.discState.mine = ['alipay'];
eq('绑了 alipay 就能调', g.discCap(hintCard, g.discCard(hintCard)).ok, true);
g.discState.mine = [];
eq('没绑就调不了', g.discCap(hintCard, g.discCard(hintCard)).ok, false);
// 收费（有价目表）才能验出"配对前不可调"
const peerCard = mk({ id: 'ag_peer', card: { accepts: ['peer_account'], skills: [{ id: 'ocr-pro' }],
  'x-a2n': { price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'call_count', amount: 10 }] } } } } } });
g.discState.peers = [{ agent_id: 'ag_peer', state: 'ACTIVE' }];
eq('配对后可调', g.discCap(peerCard, g.discCard(peerCard)).ok, true);
g.discState.peers = [];
eq('未配对不可调', g.discCap(peerCard, g.discCard(peerCard)).ok, false);

// ④ 追加筛选：属地 / 信誉 / 价格上限 / 仅可直调
const roster = [
  mk({ id: 'ag_a', name: 'east-cheap', region: 'cn-east-2', rep: 0.9, card: { skills: [{ id: 'ocr-pro' }], accepts: ['peer_account'],
    'x-a2n': { price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'call_count', amount: 30 }] } } } } } }),
  mk({ id: 'ag_b', name: 'north-free', region: 'cn-north-1', rep: 0.5, card: { skills: [{ id: 'ocr-pro' }] } }),
  mk({ id: 'ag_c', name: 'west-pricey', region: 'cn-west-1', rep: 0.7, card: { skills: [{ id: 'ocr-pro' }], accepts: ['peer_account'],
    'x-a2n': { price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'call_count', amount: 500 }] } } } } } }),
];
const reset = () => Object.assign(g.discState,
  { agents: roster, q: '', pay: '', region: '', min_rep: 0, max_price: '', callable: false,
    verified: false, mine: [], peers: [], skill: '' });
reset();
eq('无筛选=全部', g.discFiltered().length, 3);
g.discState.region = 'cn-east-2';
eq('按属地筛', g.discFiltered().map(a => a.agent_id), ['ag_a']);
reset(); g.discState.min_rep = 60;
eq('按信誉筛', g.discFiltered().map(a => a.agent_id), ['ag_a', 'ag_c']);
reset(); g.discState.max_price = '0.5';
eq('价格上限 0.5 元=50分', g.discFiltered().map(a => a.agent_id), ['ag_a', 'ag_b']);
reset(); g.discState.callable = true;
eq('仅可直调（只有免费那个通）', g.discFiltered().map(a => a.agent_id), ['ag_b']);
reset(); g.discState.pay = 'peer_account';
eq('付费方式当偏好筛', g.discFiltered().map(a => a.agent_id), ['ag_a', 'ag_c']);
reset(); g.discState.q = 'north';
eq('关键词搜索', g.discFiltered().map(a => a.agent_id), ['ag_b']);
reset(); g.discState.skill = 'ocr-pro';
eq('能力选择按技能标识精确匹配', g.discFiltered().map(a => a.agent_id), ['ag_a','ag_b','ag_c']);
reset(); g.discState.skill = 'ocr';
eq('能力选择不把不同版本的标识模糊合并', g.discFiltered().length, 0);
reset();

// ②e 一格价格里并排多种币种：两条独立挂牌，不换算、不相加
const twoCur = mk({ id: 'ag_2cur', card: { skills: [{ id: 'ocr-pro' }], accepts: ['peer_account'],
  'x-a2n': { price_book: { 'ocr-pro': {
    CNY:  { dimensions: [{ key: 'call_count', amount: 3, per: 1 }] },
    USDC: { dimensions: [{ key: 'call_count', amount: 4500, per: 1 }] } } } } } });
eq('两币种并排（主价在前）', g.priceList(g.discCard(twoCur)), ['¥0.03/次', '0.0045 USDC/次']);
eq('两币种是两条独立挂牌，不合成一个数', g.priceList(g.discCard(twoCur)).length, 2);
eq('priceText 仍只取主价（逻辑判定不受影响）', g.priceText(g.discCard(twoCur)), '¥0.03/次');
eq('免费卡没有价目 → 空列表', g.priceList(g.discCard(freeCard)), []);
eq('该币种没有按次价 → 如实说"计价"', g.priceList(g.discCard(mk({ id: 'ag_nocall',
  card: { skills: [{ id: 'ocr-pro' }],
    'x-a2n': { price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'page_count', amount: 1 }] } } } } } }))),
  ['CNY 计价']);

// ②f 行业分类：技能归到"行业 / 交付物"分类下，未收录的进「其他」而不是消失
eq('分类 video-short', g.skillCategory('video-short'), '影视与视频');
eq('分类 video-script', g.skillCategory('video-script'), '影视与视频');
eq('分类 finance-report', g.skillCategory('finance-report'), '财务与税务');
eq('分类 legal-contract', g.skillCategory('legal-contract'), '法务与合同');
eq('分类 game-design', g.skillCategory('game-design'), '游戏与互动');
eq('分类 ecom-listing', g.skillCategory('ecom-listing'), '电商与营销');
eq('分类 ocr-pro', g.skillCategory('ocr-pro'), '文档与识别');
eq('未收录技能进「其他」而不是消失', g.skillCategory('translate'), g.categoryRest);
eq('分类顺序固定（行业在前、平台自带节点在后）', g.categoryOrder,
  ['影视与视频', '财务与税务', '法务与合同', '游戏与互动', '电商与营销', '文档与识别']);

// ②g 按分类筛选："cat:" 命中该分类下的任一技能；技能精确筛选照旧可用
const catRoster = [
  mk({ id: 'ag_ocr', card: { skills: [{ id: 'ocr-pro' }] } }),
  mk({ id: 'ag_video', card: { skills: [{ id: 'video-short' }] } }),
  mk({ id: 'ag_both', card: { skills: [{ id: 'video-script' }, { id: 'game-design' }] } }),
];
reset(); g.discState.agents = catRoster;
g.discState.skill = 'cat:影视与视频';
eq('分类筛选：影视与视频（两张卡都命中）', g.discFiltered().map(a => a.agent_id),
  ['ag_video', 'ag_both']);
g.discState.skill = 'cat:游戏与互动';
eq('同一张卡可被多个分类命中（ag_both 同时在影视与游戏两类里）',
  g.discFiltered().map(a => a.agent_id), ['ag_both']);
g.discState.skill = 'cat:文档与识别';
eq('分类筛选：文档与识别', g.discFiltered().map(a => a.agent_id), ['ag_ocr']);
g.discState.skill = 'cat:其他';
eq('分类筛选：分类下没有就如实为空', g.discFiltered().length, 0);
g.discState.skill = 'game-design';
eq('下钻到单个技能仍然可用', g.discFiltered().map(a => a.agent_id), ['ag_both']);
g.discState.skill = 'cat:法务与合同';
eq('分类筛选：该分类下没有任何服务时如实为空', g.discFiltered().length, 0);
reset();

// ⑤ 展示层小件
eq('shortId 截断', g.shortId('ag_8fa53af7af3b'), 'ag_8fa53af7af3…');
eq('repTier 高', g.repTier(85).t, '可靠');
eq('repTier 低', g.repTier(20).t, '风险偏高');
eq('curExp 默认 2', g.curExp('ZZZ'), 2);

// ⑥ 筛选条生成的 HTML：onclick 里的引号必须转义
//    （不转义会被浏览器截断属性，按钮点了没反应 —— 曾经踩过）
const payBox = { innerHTML: '' };
const realGet = ctx.document.getElementById;
ctx.document.getElementById = id => (id === 'd_pay' ? payBox : fakeEl());
reset();
vm.runInContext('_discPayRender()', ctx);
ctx.document.getElementById = realGet;
ok('筛选条渲染出 HTML', payBox.innerHTML.length > 50);
ok('onclick 引号已转义',
   payBox.innerHTML.includes('onclick="_disc.pay=&#39;free&#39;;_discRender()"'));
ok('onclick 里没有裸引号', !/onclick="[^"]*_disc\.pay="[^"]*"/.test(payBox.innerHTML));
ok('追加筛选控件在位', payBox.innerHTML.includes('价格上限') && payBox.innerHTML.includes('属地不限'));

// ⑦ 信息架构：三个主入口 + 入口内的小页（数量与名目就是"上手难度"本身）
eq('主入口三个', Object.keys(g.subs).sort(), ['account', 'find', 'sell']);
eq('找 Agent 三小页', g.subs.find.map(x => x[1]), ['发现 Agent', '待使用', '调用记录']);
eq('卖 Agent 三小页', g.subs.sell.map(x => x[1]), ['自售列表', '他人调用记录', '行情信息']);
eq('我的账户两小页', g.subs.account.map(x => x[1]), ['账户信息', '流水列表']);
ok('默认子页都在第一页', Object.values(g.subState).every(v => v === g.subs.find[0][0] || v === 'list' || v === 'info'));

// ⑧ 子页签渲染：每个入口恰好渲染自己的小页，当前页带 on
const navBox = { innerHTML: '' };
ctx.document.getElementById = id => (id === 'cnt_find_discover' ? navBox : fakeEl());
const navHtml = g.subnav('find');
ok('子页签含全部三项', ['发现 Agent', '待使用', '调用记录'].every(s => navHtml.includes(s)));
ok('当前子页标记 on', /class="subbtn on" data-sec="discover"/.test(navHtml));
ok('子页签计数挂点齐全', navHtml.includes('id="cnt_find_discover"') && navHtml.includes('id="cnt_find_calls"'));
ctx.document.getElementById = realGet;

// ⑨ 明细抽屉的小件：状态徽标 + 凭证链时间线（章 → 人话，不改链）
eq('SETTLED 显示绿', g.taskStateBadge('SETTLED').includes('b-ok'), true);
eq('REJECTED 显示红', g.taskStateBadge('REJECTED').includes('b-bad'), true);
eq('未知态不冒充成功', g.taskStateBadge('WEIRD').includes('b-info'), true);
ok('关键事件有中文标签',
   ['task.created', 'task.assigned', 'task.submitted', 'acceptance.passed', 'settlement.ordered']
     .every(k => g.evLabel[k]));
const tl = g._drTimeline([
  { seq: 1, event_type: 'task.created', payload: '{"task_id":"t1","skill":"ocr-pro"}', hash: 'a'.repeat(64), created_at: '2026-09-12T10:00:00' },
  { seq: 2, event_type: 'acceptance.passed', payload: '{"task_id":"t1","node_id":"ag_x","score":1}', hash: 'b'.repeat(64), created_at: '2026-09-12T10:00:03' },
  { seq: 3, event_type: 'task.assigned', payload: '{"task_id":"t1","node_id":"ag_x","card_hash":"deadbeef","unit_prices":[{"key":"call_count","amount":3}]}', hash: 'c'.repeat(64), created_at: '2026-09-12T10:00:04' },
]);
ok('时间线两章两人话', tl.includes('已建任务') && tl.includes('验收通过'));
ok('时间线带哈希前缀', tl.includes('aaaaaaaaaaaa'));
ok('时间线不再重复打印 task_id', !tl.includes('task_id='));
ok('时间线摘得出人话字段', tl.includes('skill=ocr-pro') && tl.includes('score=1'));
ok('时间线不塞合约快照/哈希（那些进原始数据）',
   !tl.includes('unit_prices') && !tl.includes('card_hash'));
ok('空时间线给兜底文案而非空白', g._drTimeline([]).includes('暂无盖章记录'));

// ⑩ 行情行：挂牌（意愿）与成交（事实）分开写，且不跨币种合并
const qline = g._quoteLine({ currency: 'CNY', done_count: 3, avg_minor: 3, min_minor: 3, max_minor: 5,
                            listed_min_minor: 3, listed_max_minor: 5, listed_count: 2 });
ok('行情行标出币种', qline.includes('CNY'));
ok('行情行给挂牌区间', qline.includes('¥0.03') && qline.includes('¥0.05'));
ok('行情行给成交均价与笔数', qline.includes('¥0.03') && qline.includes('3 笔'));
const qfree = g._quoteLine({ currency: 'USDC', done_count: 0, avg_minor: 0, min_minor: 0, max_minor: 0,
                            listed_min_minor: null, listed_max_minor: null, listed_count: 0 });
ok('无标价显示"免费"', qfree.includes('免费'));
ok('无成交显示"暂无成交"', qfree.includes('暂无成交'));
ok('免费不等于 0 元', !qfree.includes('0.00'));

// ⑪ 卡片自证闸（P2）：未自证 ≠ 验过。"可直接调用"必须同时满足"能付费"与"已自证"
//    —— 否则"在你列表里"会被读成"平台验过了"。结论来自服务端（selfproof 字段）。
const signCard = mk({ id: 'ag_signed', name: 'signed', card: { skills: [{ id: 'ocr-pro' }] } });
const anonCard = mk({ id: 'ag_anon', name: 'anon', selfproof: 'unattested',
                      card: { skills: [{ id: 'ocr-pro' }] } });
eq('signed → proof.signed', g.discProof(signCard).signed, true);
eq('unattested → 不算已自证', g.discProof(anonCard).signed, false);
eq('unattested 仍是 unattested（不冒充 invalid）', g.discProof(anonCard).sp, 'unattested');
eq('字段缺失按未知处理（不默认"验过"）', g.discProof({ agent_id: 'x' }).signed, false);
ok('reason 随行带出，界面能把"没验"说清楚', g.discProof(anonCard).reason.length > 0);

reset(); g.discState.agents = [signCard, anonCard];
eq('未筛=两张都在（未自证不隐藏，只是不给直调徽标）', g.discFiltered().length, 2);
reset(); g.discState.agents = [signCard, anonCard]; g.discState.verified = true;
eq('仅已自证身份 → 只剩 signed', g.discFiltered().map(a => a.agent_id), ['ag_signed']);
reset(); g.discState.agents = [signCard, anonCard]; g.discState.callable = true;
eq('仅可直调 → 未自证的免费卡也不合格',
   g.discFiltered().map(a => a.agent_id), ['ag_signed']);
reset();

// ⑫ 结算与对账页（运维 · P1 §3.2）：三个数带口径、金额按币种分行（不跨币种相加）、
//    待处理可点开追单、**取数失败不许翻成空态**（把 500 说成"还没有结算"是最坏的一种容错）
const sv = g.settleView({
  summary: { due_count: 4, settled_count: 3, pending_count: 1,
             settled_by_currency: [
               { currency: 'CNY', n: 2, amount_minor: 325 },
               { currency: 'USDC', n: 1, amount_minor: 5000000 }] },
  alert: { alert: true, reasons: ['待处理 1 笔'] },
  last_cut: { day: '2026-09-14', points_total: 2500, escrow_balance_fen: 2500, diff: 0, balanced: true },
  recent_pending: [{ task_id: 't_pend_1', mode: 'prepaid_points', state: 'PENDING',
                     amount_minor: 5, currency: 'CNY', reason: '托管方超时', attempts: 2,
                     updated_at: '2026-09-14T16:00:00' }],
  recent_settled: [{ task_id: 't_ok_1', mode: 'free', amount_minor: 0, currency: 'CNY',
                     ref: 'so_1', updated_at: '2026-09-14T15:00:00' }],
  history: [{ day: '2026-09-14', points_total: 2500, escrow_balance_fen: 2500, diff: 0,
              balanced: 1, due_count: 4, settled_count: 3, pending_count: 1, note: '对账平、无待处理' }],
});
ok('三个数都在（应结/已结/待处理）',
   sv.includes('今日应结') && sv.includes('今日已结') && sv.includes('待处理'));
ok('三个数各自标明口径（数字没有口径就是噪声）',
   sv.includes('通过验收') && sv.includes('写入 SETTLED') && sv.includes('未结清的笔数'));
const svRows = sv.split('<tr');
ok('金额按币种各占一行（CNY 与 USDC 不合并到同一行）',
   svRows.some(r => r.includes('CNY') && r.includes('¥3.25')) &&
   svRows.some(r => r.includes('USDC') && r.includes('5 USDC')) &&
   !svRows.some(r => r.includes('CNY') && r.includes('USDC')));
ok('告警位把原因写出来', sv.includes('告警') && sv.includes('待处理 1 笔'));
ok('对账平显"平衡"与两边数字', sv.includes('平衡') && sv.includes('2500'));
ok('待处理可点开追到具体任务', /onclick="drawerOpen\('call','t_pend_1'\)"/.test(sv));
ok('待处理带原因（能看出为什么没结上）', sv.includes('托管方超时'));
ok('日切历史落一行', sv.includes('2026-09-14') && sv.includes('对账平'));
// 凭据号是"这一笔能追回分账单/回执"的唯一线索，不能渲染成"—"就完事。
// （它一度被运维白名单挡掉：数据里明明有，界面上永远只有"—"。）
ok('最近已结显示凭据号（能追回分账单/回执）', sv.includes('so_1'));
// 空数据 ≠ 取数失败：两回事，不许长得一样
const svEmpty = g.settleView({ summary: { due_count: 0, settled_count: 0, pending_count: 0,
                                          settled_by_currency: [] }, alert: {}, last_cut: null,
                              recent_pending: [], recent_settled: [], history: [] });
ok('真·没有数据才说空', svEmpty.includes('今天还没有已结的账') && svEmpty.includes('没有待处理的结算'));
ok('还没对过账有专门说法（不冒充"平衡"）', svEmpty.includes('还没对过账'));
const svErr = g.settleError(new Error('502 upstream connect failed'));
ok('取数失败说"加载失败"并把原因带上', svErr.includes('加载失败') && svErr.includes('502 upstream'));
ok('失败态绝不含空态文案（否则 500 会被读成"你还没结算"）',
   !svErr.includes('还没有已结') && !svErr.includes('没有待处理'));

// ⑬ 页签的三张名单必须一致：tab() 的显示白名单、_ADV（高级区）、<main> 里的 <section id>。
//    漏一个的症状最阴：页签点得动、内容也渲染了，但目标 section 永远带着 hide ——
//    而 innerText 对 display:none 会**回退成 textContent**，于是所有文本断言**假绿**。
//    结算页就这么漏过一次，靠截图那一层才抓到。这条断言把它提前到了毫秒级。
const mainSections = [...((html.match(/<main>([\s\S]*?)<\/main>/) || ['', ''])[1])
  .matchAll(/<section id="([^"]+)"/g)].map(m => m[1]);
const tabList = ((code.match(/\[('find'[^\]]*)\]/) || ['', ''])[1])
  .match(/'[a-z0-9_]+'/g).map(s => s.replace(/'/g, ''));
const moreBtns = [...((html.match(/<div id="more"[\s\S]*?<\/div>/) || [''])[0])
  .matchAll(/data-tab="([^"]+)"/g)].map(m => m[1]);
const sortJoin = a => JSON.stringify([...a].sort());
ok('tab() 的显示白名单与 <main> 的 section 一一对应（漏一个就是"点了没反应"）',
   sortJoin(tabList) === sortJoin(mainSections),
   `tab=${[...tabList].sort()} main=${[...mainSections].sort()}`);
ok('_ADV（高级区）与 #more 里的页签按钮一一对应',
   sortJoin(g.adv) === sortJoin(moreBtns),
   `adv=${[...g.adv].sort()} more=${[...moreBtns].sort()}`);
ok('高级区名单不含三个主入口（否则主区会被当高级区收起）',
   g.adv.every(x => !['find', 'sell', 'account'].includes(x)));

// ⑭ 免费期与"自源 vs 独立"的差值参数：
//    · 重连补出来的额度**不是**质量证据，徽标必须自己说出来（不说就会被当成证据）；
//    · 自调用被允许，但界面要给**差异**、不给结论 —— 不合成"真实分"，
//      样本不足时不给差值（给个 0 比不给更误导）。
const badgeInit = g._trialBadge({ trial: { trial: true, used: 3, cap: 10,
                                           grant_kind: 'INITIAL', stability: false } });
ok('首装试用徽标说"免费"', badgeInit.includes('试用中 3/10') && badgeInit.includes('免费'));
ok('首装徽标不冒充"重连采样"', !badgeInit.includes('重连采样'));
const badgeStab = g._trialBadge({ trial: { trial: true, used: 1, cap: 5,
                                           grant_kind: 'RECONNECT', stability: true } });
ok('重连额度单独标出来', badgeStab.includes('重连采样'));
ok('重连额度写明"不进质量模板"（否则用户会把它当质量证据）',
   badgeStab.includes('质量模板'));
ok('毕业态只标毕业（免费服务也可以毕业，不暗示一律收费）',
   g._trialBadge({ trial: { trial: false, state: 'GRADUATED' } }).includes('已毕业'));
ok('毕业徽标解释仍免费或按价目收费，不挂无条件收费标签',
   g._trialBadge({ trial: { trial: false, state: 'GRADUATED' } }).includes('仍免费') &&
   !g._trialBadge({ trial: { trial: false, state: 'GRADUATED' } }).includes('已毕业 · 收费'));

const ssFull = g._selfSrcBlock({ self_cases: 3, self_mean_raw: 95,
                                 independent_cases: 3, independent_mean_raw: 60,
                                 delta_raw: 35, comparable: true,
                                 note: '自源与独立使用者的原始分均值之差（绝不合成总分）' });
ok('差值参数把两个均值并排给出', ssFull.includes('95') && ssFull.includes('60'));
ok('差值以大字号徽标呈现', ssFull.includes('差值') && ssFull.includes('35'));
ok('注明"绝不合成总分"（这是口径，不是装饰）', ssFull.includes('绝不合成总分'));
const ssThin = g._selfSrcBlock({ self_cases: 3, self_mean_raw: 95,
                                 independent_cases: 0, independent_mean_raw: null,
                                 delta_raw: null, comparable: false, note: '样本不足' });
ok('样本不足时标"样本不足"', ssThin.includes('样本不足'));
ok('样本不足时**不给差值数字**（不给比给 0 好）', !ssThin.includes('差值 0'));
ok('没有评分就整块不渲染', g._selfSrcBlock({ self_cases: 0, independent_cases: 0 }) === '');
ok('差值块里没有"真实分/综合分"这类合成字段',
   !/真实分|综合|可信度分/.test(ssFull));

// ⑮ 上架名额（"允许被发现的数量"）：它是**分发策略**（平台执行、平台记），
//    所以三件事都得钉住：① 表单能填、② 保存写对接口、③ 徽标按 seats 判据渲染。
//    最容易漏的是 ②：编辑已有 agent 时名额必须走 /listing —— 它**不是**卡的内容，
//    写回 /card 等于让平台改写自签卡，签名会当场失效，而那种坏法在界面上看不出来。
ok('上架表单有「允许被发现的数量」输入', /id="sh_seat"/.test(html));
ok('新建上架时把名额一起提交', /discover_limit\s*:\s*seat/.test(code));
ok('编辑已有 agent 时名额走 /listing（不动卡、不重签）',
   /\/listing['"`]?\s*,\s*\{[^}]*discover_limit/.test(code));
ok('不限名额不挂徽标（列表别被"不限"塞满）',
   g._seatBadge(null) === '' &&
   g._seatBadge({ limit: 0, unlimited: true, used: 0 }) === '');
ok('有名额时报「名额 已占/上限」',
   /名额 3\/5/.test(g._seatBadge({ limit: 5, unlimited: false, used: 3, full: false })));
ok('满员时在徽标上标出来',
   /已满/.test(g._seatBadge({ limit: 2, unlimited: false, used: 2, full: true })));
ok('明细里"不限"要明说（什么都不显示 ≠ 不限）',
   /不限/.test(g._seatDetail({ seats: { limit: 0, unlimited: true } })));
ok('明细里能看到谁在占名额',
   /正在用/.test(g._seatDetail({ seats: { limit: 2, unlimited: false, used: 1,
                                          full: false,
                                          holders: [{ principal_id: 'acct:x' }] } })));
ok('占用者名单只进明细，不进公开徽标（名单是使用者的身份）',
   !/acct:x/.test(g._seatBadge({ limit: 2, unlimited: false, used: 1, full: false,
                                 holders: [{ principal_id: 'acct:x' }] })));

// Network telemetry must not turn unavailable/stale samples into green zeroes.
eq('网络没有样本显示未测', g.netAgentView({ agent_id:'n', network:{reachable:true} }).text, '未测');
eq('离线不沿用旧延迟', g.netAgentView({ agent_id:'n', network:{reachable:false,rtt_ms:8} }).text, '离线');
eq('有效的零毫秒没有被当作缺失', g.netAgentView({ agent_id:'n', network:{reachable:true,rtt_ms:0} }).text, '0 ms');
eq('正常通道显示真实毫秒', g.netAgentView({ agent_id:'n', network:{reachable:true,rtt_ms:18.5} }).text, '19 ms');
eq('超时不是零延迟', g.netAgentView({ agent_id:'n', network:{reachable:true,state:'timeout',rtt_ms:null} }).text, '测速超时');
eq('过期采样要求重测', g.netAgentView({ agent_id:'n', network:{reachable:true,rtt_ms:8,checked_at:'2020-01-01T00:00:00Z'} }).text, '待重测');
eq('延迟偏高用警示色', g.netAgentView({ agent_id:'n', network:{reachable:true,rtt_ms:400} }).tone, 'slow');

// Discovery sorts published facts, never synthesizes a quality recommendation.
const offer = (id, cur, minor, rtt, extra = {}) => ({
  agent_id:id, name:id, status:'ACTIVE', reputation:50,
  selfproof:'signed', network:{reachable:true,rtt_ms:rtt},
  card_json:JSON.stringify({skills:[{id:'ocr',name:'OCR'}],
    'x-a2n':{price_book:cur?{ocr:{[cur]:{dimensions:[{key:'call_count',amount:minor}]}}}:{}}}),
  ...extra,
});
const choices = [offer('usd','USDC',1,9), offer('expensive','CNY',50,30),
  offer('free',null,0,20), offer('cheap','CNY',5,60),
  offer('offline',null,0,1,{network:{reachable:false,rtt_ms:1}}),
  offer('stale',null,0,2,{network:{reachable:true,rtt_ms:2,checked_at:'2020-01-01T00:00:00Z'}})];
const originalOrder = choices.map(a=>a.agent_id).join(',');
g.discState.sort='price';
const byPrice = g._discSort(choices).map(a=>a.agent_id);
ok('免费与人民币价格升序，其他币种不冒充低价',
  byPrice.indexOf('free') < byPrice.indexOf('cheap') && byPrice.indexOf('cheap') < byPrice.indexOf('expensive')
  && byPrice.indexOf('expensive') < byPrice.indexOf('usd'));
g.discState.sort='latency';
eq('实测延迟排序，离线与过期样本置后', g._discSort(choices).map(a=>a.agent_id),
  ['usd','free','expensive','cheap','offline','stale']);
eq('排序不改写原始发现快照', choices.map(a=>a.agent_id).join(','), originalOrder);
g.discState.sort='ready';
ok('可用优先不把离线免费服务放前面',
  g._discSort(choices).findIndex(a=>a.agent_id==='free') < g._discSort(choices).findIndex(a=>a.agent_id==='offline'));
g.discState.sort='reputation';
eq('历史信誉排序独立于延迟', g._discSort([offer('low',null,0,1,{reputation:.3}),
  offer('high',null,0,100,{reputation:.8})]).map(a=>a.agent_id), ['high','low']);
g.discState.sort='ready';
eq('同名服务用标识稳定排序，不随接口顺序跳动',
  g._discSort([offer('b',null,0,10,{name:'同名'}),offer('a',null,0,10,{name:'同名'})]).map(a=>a.agent_id), ['a','b']);

if (fails.length) {
  console.error(`✗ 控制台逻辑体检失败 ${fails.length} 项：`);
  fails.forEach(f => console.error('   - ' + f));
  process.exit(1);
}
console.log(`✓ 控制台逻辑体检通过（${pass} 项）`);
