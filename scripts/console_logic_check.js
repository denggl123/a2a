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
globalThis.discFiltered = _discFiltered;
globalThis.priceText = _priceText;
globalThis.callPrice = _callPrice;
globalThis.repTier = _repTier;
globalThis.shortId = _shortId;
globalThis.discState = _disc;
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
const mk = o => ({ agent_id: o.id || 'ag_1', name: o.name || 'n',
                   card_json: JSON.stringify(o.card || {}), status: o.status || 'ACTIVE',
                   reputation: o.rep == null ? 0.5 : o.rep,
                   region: o.region, accepts: (o.card || {}).accepts });
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
  { agents: roster, q: '', pay: '', region: '', min_rep: 0, max_price: '', callable: false, mine: [], peers: [] });
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

if (fails.length) {
  console.error(`✗ 控制台逻辑体检失败 ${fails.length} 项：`);
  fails.forEach(f => console.error('   - ' + f));
  process.exit(1);
}
console.log(`✓ 控制台逻辑体检通过（${pass} 项）`);
