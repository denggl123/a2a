/* 本机节点控制台（runtime.html）**纯逻辑**体检：把页面脚本在 vm 里跑起来
   （DOM 全用桩），断言发现页筛选的口径 —— 价格三态、分类归组、维度过滤、
   排序不换算、去重键、浏览清单。与 scripts/console_logic_check.js（旧平台）
   同一层级：那个防平台手滑，这个防节点手滑。
   用法：node scripts/runtime_logic_check.js
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'packages', 'a2n-sdk', 'src', 'a2n_sdk', 'web', 'runtime.html');
const html = fs.readFileSync(FILE, 'utf8');
const code = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).join('\n');

// ---- 假的 DOM：任何取元素都返回一个"什么都能点、什么都能读"的桩 ----
function fakeEl() {
  const el = {
    value: '', innerHTML: '', textContent: '', checked: false, disabled: false,
    open: false, className: '', dataset: {}, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, remove() {}, scrollIntoView() {},
    querySelectorAll() { return []; }, querySelector() { return fakeEl(); },
    addEventListener() {}, onclick: null, focus() {}, showModal() {}, close() {},
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
    querySelector: () => fakeEl(),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    hidden: false,
  },
  window: {},
  localStorage: { getItem: () => null, setItem() {} },
  navigator: {}, crypto: { randomUUID: () => 'test-uuid' },
  performance: { now: () => 0 },
  alert: () => {}, confirm: () => false, prompt: () => null,
  fetch: () => Promise.reject(new Error('no-network-in-check')),
  setTimeout: () => 0, setInterval: () => 0, clearTimeout() {}, clearInterval() {},
  AbortSignal: { timeout: () => undefined },
};
ctx.globalThis = ctx;
vm.createContext(ctx);

vm.runInContext(code + `
;globalThis.setPool = v => { pool = v; };
globalThis.getFound = () => found;
globalThis.state = disc;
globalThis.tax = { SKILL_CATEGORY, CATEGORY_ORDER, CATEGORY_REST, BROWSE_SEED };
globalThis.fns = { skillCat, skillIds, skillNames, tagsOf, regionOf, acceptsOf,
  priceState, priceList, priceText, attestedOf, srcLabel, payLabel, cardKey,
  discFiltered, discSorted, cnyPrice, browseSkills, money, pricesOf, price,
  statusLabel, unknownState, openDisputeFor };
`, ctx, { filename: 'runtime.html' });

const { tax, fns, state } = ctx;
let fails = 0, total = 0;
function eq(name, got, want) {
  total++;
  const good = JSON.stringify(got) === JSON.stringify(want);
  console.log((good ? '  ✓ ' : '  ✗ ') + name + (good ? '' : `  期望 ${JSON.stringify(want)}，实际 ${JSON.stringify(got)}`));
  if (!good) fails++;
}
function ok(name, cond, extra = '') {
  total++;
  console.log((cond ? '  ✓ ' : '  ✗ ') + name + (cond ? '' : '  ' + extra));
  if (!cond) fails++;
}

// ---- 样本：一张多币种收费卡 / 一张未标价卡 / 一张声明零价卡 / 一张未收录能力卡 ----
const priced = {
  name: '短视频成片包', description: '交付一份短视频成片包', url: 'http://n1/a2a/svc_a',
  skills: [{ id: 'video-short', name: '短视频成片包', tags: ['视频', '分镜'] }],
  accepts: ['peer_account', 'direct_pay:alipay'],
  'x-a2n': {
    deployment: { region: 'local-demo' },
    price_book: { 'video-short': { CNY: { dimensions: [{ key: 'call_count', amount: 30, per: 1 }] },
                                   USDC: { dimensions: [{ key: 'call_count', amount: 40000, per: 1 }] } } },
    projection: { node_did: 'did:a2n:ag_n1', service_id: 'svc_a', attested: true },
  },
};
const unpriced = {
  name: '经营报表', description: '交付一份经营报表', url: 'http://n2/a2a/svc_b',
  skills: [{ id: 'finance-report', name: '经营报表' }],
  accepts: ['x402'],
  'x-a2n': { deployment: { region: 'cn-east' },
             projection: { node_did: 'did:a2n:ag_n2', service_id: 'svc_b', attested: false } },
};
const declaredFree = {
  name: '文档识别', description: 'OCR', url: 'http://n3/a2a/svc_c',
  skills: [{ id: 'ocr-pro', name: '文档识别' }], accepts: ['peer_account'],
  'x-a2n': { deployment: { region: 'local-demo' },
             price_book: { 'ocr-pro': { CNY: { dimensions: [{ key: 'call_count', amount: 0, per: 1 }] } } },
             projection: { node_did: 'did:a2n:ag_n3', service_id: 'svc_c', attested: false } },
};
const unknownSkill = {
  name: '神秘服务', description: '未收录的能力', url: 'http://n4/a2a/svc_d',
  skills: [{ id: 'weird-thing', name: '神秘服务' }], accepts: [],
  'x-a2n': { projection: { node_did: 'did:a2n:ag_n4', service_id: 'svc_d', attested: false } },
};
const ITEM = c => ({ card: c, source: c === unpriced ? 'p2p' : c === declaredFree ? 'platform' : 'public-node', headers: {} });
const POOL = [priced, unpriced, declaredFree, unknownSkill].map(ITEM);

console.log('\n== 分类（只决定筛选器怎么分组，不改事实） ==');
eq('video-short → 影视与视频', fns.skillCat('video-short'), '影视与视频');
eq('未收录能力 → 其他', fns.skillCat('weird-thing'), tax.CATEGORY_REST);
ok('CATEGORY_ORDER 覆盖全部已登记行业',
   tax.CATEGORY_ORDER.length === new Set(Object.values(tax.SKILL_CATEGORY)).size,
   '有行业只登记了名字没给能力，或反过来');

console.log('\n== 价格三态（未标价不自动等于免费） ==');
eq('有价目表 → priced', fns.priceState(priced), 'priced');
eq('无价目表 → unpriced', fns.priceState(unpriced), 'unpriced');
eq('金额全 0 → free', fns.priceState(declaredFree), 'free');
eq('未标价文案是「未标价」不是「免费」', fns.priceText(unpriced), '未标价');
eq('零价文案明确说已声明', fns.priceText(declaredFree), '免费（已声明零价）');
eq('多币种并列两条（不换算不相加）', fns.priceList(priced).length, 2);
ok('两币种各说各的（¥ 与 USDC 都在）',
   fns.priceList(priced).some(t => t.startsWith('¥0.3')) &&
   fns.priceList(priced).some(t => t.includes('USDC')),
   fns.priceList(priced).join(' | '));

console.log('\n== 维度过滤 ==');
ctx.setPool(POOL.map(x => ({ ...x })));
state.cat = '影视与视频';
eq('按行业筛', fns.discFiltered().map(i => i.card.name), ['短视频成片包']);
state.cat = ''; state.skill = 'finance-report';
eq('按能力筛', fns.discFiltered().map(i => i.card.name), ['经营报表']);
state.skill = ''; state.price = 'unpriced';
eq('按「未标价」筛（两张无价目表的都算）', fns.discFiltered().map(i => i.card.name), ['经营报表', '神秘服务']);
state.price = 'free';
eq('按「已声明免费」筛（未标价混不进来）', fns.discFiltered().map(i => i.card.name), ['文档识别']);
state.price = ''; state.verified = true;
eq('仅已自证身份', fns.discFiltered().map(i => i.card.name), ['短视频成片包']);
state.verified = false; state.source = 'p2p';
eq('按来源筛', fns.discFiltered().map(i => i.card.name), ['经营报表']);
state.source = ''; state.q = '报表';
eq('关键词命中描述', fns.discFiltered().map(i => i.card.name), ['经营报表']);
state.q = ''; state.pay = 'x402';
eq('按支付方式筛', fns.discFiltered().map(i => i.card.name), ['经营报表']);
state.pay = ''; state.region = 'local-demo';
eq('按属地筛', fns.discFiltered().map(i => i.card.name), ['短视频成片包', '文档识别']);
state.region = '';

console.log('\n== 排序（绝不跨币种换算） ==');
state.sort = 'price';
eq('价格升序：零价 → 有价 → 未标价垫底',
   fns.discSorted(POOL.map(x => ({ ...x }))).map(i => i.card.name),
   ['文档识别', '短视频成片包', '经营报表', '神秘服务']);
eq('USDC 标价不参与 CNY 排序比较（只有 CNY 才有值）',
   fns.cnyPrice(unpriced), Infinity);
state.sort = 'default';

console.log('\n== 去重键（与后端同一套语义） ==');
eq('同节点+同供给+同地址 → 同键',
   fns.cardKey({ card: priced }), fns.cardKey({ card: { ...priced, skills: [priced.skills[0]], name: '改名' } }));
ok('不同供给 → 不同键', fns.cardKey({ card: priced }) !== fns.cardKey({ card: unpriced }));

console.log('\n== 浏览清单（已知能力，不是全网目录） ==');
ctx.state2 = null;
const seeded = vm.runInContext(`
;snapshot.bindings = [{ skills: ['my-own-skill'] }];
snapshot.projections = [{ skills: ['imported-skill'] }];
browseSkills();
`, ctx);
ok('含本节点自己的供给', seeded.includes('my-own-skill'));
ok('含待使用里的能力', seeded.includes('imported-skill'));
ok('含种子清单（裸节点也能浏览）', seeded.includes('video-short'));
ok('种子在，未知能力不伪造', !seeded.includes('weird-thing'));

console.log('\n== 失败/未知状态：结果未知不许读成成功 ==');
eq('已结算仍是已结算', fns.statusLabel('SETTLED'), '已结算');
ok('结果未知明说「未确认」', /未确认/.test(fns.statusLabel('DELIVERY_UNKNOWN')), fns.statusLabel('DELIVERY_UNKNOWN'));
ok('中断明说「结果未知」', /未知/.test(fns.statusLabel('INTERRUPTED')), fns.statusLabel('INTERRUPTED'));
ok('取消中≠已取消（前者待确认）',
   /待确认/.test(fns.statusLabel('CANCEL_REQUESTED')) && fns.statusLabel('CANCELED') === '已取消',
   fns.statusLabel('CANCEL_REQUESTED') + ' / ' + fns.statusLabel('CANCELED'));
ok('unknownState 只认「已发出未确认」',
   fns.unknownState('DELIVERY_UNKNOWN') && fns.unknownState('INTERRUPTED') &&
   fns.unknownState('CANCEL_REQUESTED') && !fns.unknownState('SETTLED') &&
   !fns.unknownState('FAILED') && !fns.unknownState('COMPLETED'));
eq('未填状态 → 未知', fns.statusLabel(''), '未知');
eq('没登记的状态如实回显，不猜', fns.statusLabel('SOMETHING_NEW'), 'SOMETHING_NEW');

console.log('\n== 我不认（本机记录，只有留痕与撤回） ==');
vm.runInContext(`snapshot.disputes = [
  { id: 'ds_open', state: 'OPEN', scope: 'svc_a', task_id: 't1' },
  { id: 'ds_done', state: 'WITHDRAWN', scope: 'svc_b', task_id: 't2' }];`, ctx);
eq('找到未撤回的那条', (fns.openDisputeFor('svc_a', 't1') || {}).id, 'ds_open');
ok('已撤回的不算「未撤回」', fns.openDisputeFor('svc_b', 't2') === undefined);
ok('没记录的任务不伪造', fns.openDisputeFor('svc_z', 't9') === undefined);

console.log(`\n共 ${total} 项断言，${fails ? '失败 ' + fails + ' 项 ✗' : '全部通过 ✓'}`);
process.exit(fails ? 1 : 0);
