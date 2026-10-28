/* 本机节点控制台（runtime.html）**纯逻辑**体检：把页面脚本在 vm 里跑起来
   （DOM 全用桩），断言发现页筛选的口径 —— 价格三态、分类归组、维度过滤、
   排序不换算、去重键、浏览清单。唯一节点业务体系的前端纯逻辑验证。
   用法：node scripts/runtime_logic_check.js
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'packages', 'a2n-sdk', 'src', 'a2n_sdk', 'web', 'runtime.html');
const html = fs.readFileSync(FILE, 'utf8');
// 页面**整体**源码（含标签与内联脚本）：有些口径写在标记里而不是 JS 里，
// 只看抽出来的脚本会漏。断言要能落在"界面上真实存在的东西"上。
const FILE_BODY = html;
const code = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).join('\n');

// ---- 假的 DOM：取元素按选择器缓存，写入真的存下来 ----
// 之所以要"缓存 + 存值"：协调层要读回自己刚写进的文案（如 #coordProgress），
// 一次性桩会把写入丢掉，断言就只能靠 grep 源码，而不是真跑出来的行为。
const els = new Map();
const events = [];
function fakeEl() {
  const el = {
    value: '', innerHTML: '', textContent: '', checked: false, disabled: false,
    hidden: false, open: false, className: '', dataset: {}, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, remove() {}, scrollIntoView() {},
    insertAdjacentHTML() {},
    querySelectorAll() { return []; }, querySelector() { return fakeEl(); },
    addEventListener() {}, onclick: null, focus() {}, showModal() {}, close() {},
  };
  return new Proxy(el, {
    get(t, k) { return k in t ? t[k] : () => {}; },
    set(t, k, v) { t[k] = v; return true; },
  });
}
const elFor = sel => { if (!els.has(sel)) els.set(sel, fakeEl()); return els.get(sel); };
const ctx = {
  console,
  document: {
    getElementById: id => elFor('#' + id),
    querySelector: sel => elFor(sel),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    dispatchEvent(event) { events.push(event); return true; },
    hidden: false,
  },
  window: {},
  CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
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
// 国际化骨架先于任何界面代码加载：映射表查表（I18N.text）在内联块里被调用。
vm.runInContext(fs.readFileSync(path.join(path.dirname(FILE),'i18n.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(path.dirname(FILE),'discovery.js'),'utf8'),ctx);

// 本地外链脚本也要真跑起来：协调层在 web/coordination.js，内联块里没有它。
const COORD = fs.readFileSync(path.join(path.dirname(FILE), 'coordination.js'), 'utf8');

vm.runInContext(code + '\n' + COORD + `
;globalThis.setPool = v => { pool = v; };
globalThis.getPool = () => pool;
globalThis.getFound = () => found;
globalThis.state = disc;
globalThis.tax = { SKILL_CATEGORY, CATEGORY_ORDER, CATEGORY_REST, BROWSE_SEED };
globalThis.fns = { skillCat, skillIds, skillNames, tagsOf, regionOf, acceptsOf,
  priceState, priceList, priceText, attestedOf, srcLabel, payLabel, cardKey,
  discFiltered, discSorted, cnyPrice, browseSkills, money, pricesOf, price,
  statusLabel, unknownState, openDisputeFor,
  trialOf, trialBlock, sampleBlock, projTrial, fmtTime,
  evidenceOf, samplesUrlOf, renderSamples,
  fbMine, fbTheirs, fbDims, fbScoreText, fbBadge, feedbackTable,
  FB_DIMS, FB_DELIVERED, fbDirLabel, fbPanel, catLabel, facetTitle, localize,
  langChoices, renderLangSwitch, switchLocale };
globalThis.coord = { states: coordStates, render: renderCoordSession, load: loadCoordSession,
  search: searchCoordination, get: () => coordSession, set: v => { coordSession = v; },
  setRequest: fn => { request = fn; }, setApplyDisc: fn => { applyDisc = fn; } };
`, ctx, { filename: 'runtime.html' });

const HTML = html;

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
// 分类的**值**是稳定 id（筛选状态里存 id），显示名走消息表 rt.cat.* —— 所以这里
// 两头都要断言：id 稳定，且 id 能翻出人名（不然界面会直接把 id 甩给用户）。
eq('video-short → 分类 id 为 video', fns.skillCat('video-short'), 'video');
eq('video 的显示名是「影视与视频」', fns.catLabel('video'), '影视与视频');
eq('未收录能力 → 其他', fns.skillCat('weird-thing'), tax.CATEGORY_REST);
eq('未收录分类如实回显 id（不编显示名）', fns.catLabel('weird-thing'), 'weird-thing');
ok('CATEGORY_ORDER 覆盖全部已登记行业',
   tax.CATEGORY_ORDER.length === new Set(Object.values(tax.SKILL_CATEGORY)).size,
   '有行业只登记了名字没给能力，或反过来');
ok('每个分类 id 都有显示名（不漏翻）',
   tax.CATEGORY_ORDER.concat([tax.CATEGORY_REST]).every(id => fns.catLabel(id) !== id),
   tax.CATEGORY_ORDER.filter(id => fns.catLabel(id) === id).join(', '));

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
state.cat = 'video';
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

console.log('\n== 国际化骨架：查表不猜，key 是协议语义不是文案 ==');
eq('无语言偏好时默认中文', ctx.I18N.locale(), 'zh-CN');
eq('中文表取到逐字原值', ctx.I18N.text('state.SETTLED'), '已结算');
eq('找不到 key → text 返回 null（调用方决定回退）', ctx.I18N.text('state.NO_SUCH_STATE'), null);
eq('找不到 key → t 返回 key 本身', ctx.I18N.t('state.NO_SUCH_STATE'), 'state.NO_SUCH_STATE');
eq('vars 不污染无占位符的值', ctx.I18N.t('state.SETTLED', { x: '1' }), '已结算');
ok('消息表 key 一个中文都没有（协议枚举进 key，文案只进值）',
   Object.keys(ctx.I18N.messages['zh-CN']).every(k => !/[\u4e00-\u9fa5]/.test(k)));
ok('消息表值都不是「错误码: 说明」格式（错误码走协议层，不在此表）',
   Object.values(ctx.I18N.messages['zh-CN']).every(v => !/^[A-Z][A-Z0-9_]+: /.test(v)));
ok('消息表值不带 HTML 标签（HTML 结构留在代码里，文案只进值）',
   Object.values(ctx.I18N.messages['zh-CN']).every(v => !/[<>]/.test(v)));
eq('未知来源如实回显，不猜', fns.srcLabel('weird-source'), 'weird-source');
eq('空来源 → 未知', fns.srcLabel(''), '未知');
eq('公共节点仍是公共节点', fns.srcLabel('public-node'), '公共节点');
eq('协调发现来源仍是协调发现', fns.srcLabel('coordination'), '逐级协调发现');
eq('时间缺失仍是「时间未知」', fns.fmtTime(0), '时间未知');

console.log('\n== 语言切换（有译文才出现，切了就重渲染） ==');
const locales = ctx.I18N.locales();
ok('语言清单带自称与「是否就绪」', locales.length >= 2
   && locales.every(l => l.id && l.label && typeof l.ready === 'boolean' && typeof l.keys === 'number'));
eq('中文自称「中文」', locales.find(l => l.id === 'zh-CN').label, '中文');
eq('英文自称「English」', locales.find(l => l.id === 'en').label, 'English');
ok('空表不算就绪（点了没反应的选择项比没有更糟）', (() => {
  ctx.I18N.messages['xx-XX'] = {};
  const probe = ctx.I18N.locales().find(l => l.id === 'xx-XX');
  delete ctx.I18N.messages['xx-XX'];
  return probe && probe.ready === false && probe.keys === 0;
})());
eq('就绪的语言：中文 + 英文草稿', fns.langChoices().map(l => l.id), ['zh-CN', 'en']);
// 把英文表临时清空：验证「只剩一份就绪 → 选择器隐藏」这条规则与当前译文多少无关。
const savedEn = Object.assign({}, ctx.I18N.messages.en);
Object.keys(ctx.I18N.messages.en).forEach(k => { delete ctx.I18N.messages.en[k]; });
fns.renderLangSwitch();
const hiddenWhenSingle = elFor('#langWrap').hidden === true;
Object.assign(ctx.I18N.messages.en, savedEn);
fns.renderLangSwitch();
ok('只剩一份就绪时选择器隐藏（不渲染只有一个选项的下拉）', hiddenWhenSingle);
ok('语言选择器在骨架里默认 hidden（首屏不闪一个空下拉）',
   /<span id="langWrap" class="langwrap" hidden>/.test(FILE_BODY));
// 英文草稿已覆盖骨架与任务状态：选择器该出现，切过去该看到真译文。
ctx.I18N.messages.en['rt.render.online'] = 'Node online · settings saved';  // 测试夹具：这条不在草稿里
fns.renderLangSwitch();
const langWrap = elFor('#langWrap'), langSel = elFor('#lang');
ok('两份就绪 → 选择器出现（不用改界面代码）', langWrap.hidden === false);
ok('选项用语言自称，不按当前语言翻译',
   /value="zh-CN"[^>]*>中文<\/option>/.test(langSel.innerHTML)
   && /value="en"[^>]*>English<\/option>/.test(langSel.innerHTML));
ok('默认选中当前语言', /value="zh-CN" selected/.test(langSel.innerHTML));
// 切语言 = 落盘 + 重载整页。十几个模块各自在加载时按当时的语言建 DOM，
// 逐个补「语言变更就重渲染」必然漏；重载是唯一能保证不半中半英的做法。
// 桩环境里没有 location，补一个只记次数的 —— 断言跑的是和线上**同一条**代码路径，
// 而不是「桩环境专用分支」（那种分支正是上一轮那个 root bug 藏身的地方）。
let reloads = 0;
ctx.location = { reload() { reloads += 1; } };
const switchedEn = fns.switchLocale('en');
ok('切语言：先落盘再重载整页（不是原地换几块 DOM）',
   switchedEn === true && reloads === 1 && ctx.I18N.locale() === 'en',
   `返回 ${switchedEn}、重载 ${reloads} 次、locale ${ctx.I18N.locale()}`);
// 重载之后浏览器里发生的事：模块按新语言重建，骨架由 localize() 重填。
// 这里模拟重载后的首屏，验「重建出来的是英文」，而不是「内存里的 current 变了」。
// 桩环境的 document.querySelectorAll 返回空数组，localize() 没有节点可填 ——
// 这里验「它要填的值已经是英文」；「真的填进 DOM」由 node_console_check 在浏览器里验。
eq('重载后首屏：骨架按钮的键查出来是英文', ctx.I18N.text('rt.head.refresh'), 'Refresh');
eq('重载后首屏：任务状态标签也是英文', fns.statusLabel('COMPLETED'), 'Completed');
ok('未知语言不切（返回 false，不落盘、不重载）',
   fns.switchLocale('xx-YY') === false && reloads === 1 && ctx.I18N.locale() === 'en');
fns.switchLocale('zh-CN');
eq('切回中文：同一个键回到中文', ctx.I18N.text('rt.head.refresh'), '刷新');
// 「英文表缺这条 → 回退中文」：临时删掉一条，验完放回。
// 不锚定「哪条还没翻」—— 那样每补一个域就要改一次断言，改着改着就会被删掉。
const FALLBACK_KEY = 'rt.acct.connect_btn';
const fallbackSaved = ctx.I18N.messages.en[FALLBACK_KEY];
delete ctx.I18N.messages.en[FALLBACK_KEY];
ctx.I18N.setLocale('en');
const fellBack = ctx.I18N.t(FALLBACK_KEY);
ctx.I18N.messages.en[FALLBACK_KEY] = fallbackSaved;
ctx.I18N.setLocale('zh-CN');
eq('英文表缺这条 → 回退中文，不露出键名', fellBack, ctx.I18N.messages['zh-CN'][FALLBACK_KEY]);
// 浏览器语言只自动选「过半已翻译」的语言。用「临时清空 en」造一份草稿来验，
// 这样断言与真实覆盖率无关：补到 100% 那天也不会红。
const enBackup = Object.assign({}, ctx.I18N.messages.en);
Object.keys(ctx.I18N.messages.en).forEach(k => { delete ctx.I18N.messages.en[k]; });
ctx.window.navigator = { language: 'en-US' };
ctx.I18N.start();
const draftPick = ctx.I18N.locale();
Object.assign(ctx.I18N.messages.en, enBackup);
ctx.I18N.start();
const fullPick = ctx.I18N.locale();
delete ctx.window.navigator;
ctx.I18N.setLocale('zh-CN');
eq('英文浏览器不会自动切到几乎空的草稿', draftPick, 'zh-CN');
const enRatio = Object.keys(ctx.I18N.messages.en).length / Object.keys(ctx.I18N.messages['zh-CN']).length;
ok('过半覆盖之后英文浏览器才自动切英文',
   (enRatio >= 0.5) === (fullPick === 'en'),
   `覆盖率 ${(enRatio * 100).toFixed(1)}%，自动选中 ${fullPick}`);
ok('切换实现是「落盘 + 重载」，并且先记住当前页签再重载',
   /function switchLocale\(name\)\{\s*if\(!I18N\.setLocale\(name\)\)return false;/.test(HTML)
   && /sessionStorage\.setItem\('a2n\.page'/.test(HTML)
   && /location\.reload\(\)/.test(HTML));
ok('重载后按 sessionStorage 切回原页签（且只认三个合法页名）',
   /sessionStorage\.getItem\('a2n\.page'\)/.test(HTML)
   && /\/\^\(find\|sell\|account\)\$\//.test(HTML));

// 语言选择的持久化：切过语言之后刷新页面（= 重新 start()）必须还是那个语言。
// 这一条曾经是红的 —— UMD 工厂收不到 root，detectLocale/setLocale 里的 root.localStorage
// 抛 ReferenceError 被 try 吞掉，于是「记住选择」和「按浏览器语言自动选」两条路径
// 全都静默失效，界面永远停在中文。断言直接钉住往返，不靠覆盖率。
const store = {};
ctx.window.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
delete ctx.window.navigator;
ctx.I18N.setLocale('en');
const persisted = ctx.window.localStorage.getItem('a2n.lang');
ctx.I18N.start();
const afterReload = ctx.I18N.locale();
ctx.I18N.setLocale('zh-CN');
eq('切过语言会写进 localStorage', persisted, 'en');
eq('刷新后（重新 start）仍是上次选的语言', afterReload, 'en');
// 显式选择优先于浏览器语言：本机选了中文，英文浏览器也不该改回去。
store['a2n.lang'] = 'zh-CN';
ctx.window.navigator = { language: 'en-US' };
ctx.I18N.start();
const explicitWins = ctx.I18N.locale();
delete ctx.window.navigator;
delete ctx.window.localStorage;
ctx.I18N.setLocale('zh-CN');
eq('显式选择优先于浏览器语言', explicitWins, 'zh-CN');

console.log('\n== 服务端错误码按码取译（默认语言一个字节都不改） ==');
const RAW_ERR = 'BACKUP_CORRUPT: 备份文件损坏';
eq('默认语言：原样返回，不重排不换词', ctx.I18N.errorText(RAW_ERR), RAW_ERR);
eq('没有码的普通消息原样返回', ctx.I18N.errorText('先发现并加入一个 Agent。'), '先发现并加入一个 Agent。');
eq('空消息返回空串（不显示 undefined）', ctx.I18N.errorText(null), '');
// 按码取译这条路真的通：直接用表里的真译文，不再现场造一条假译文 ——
// 造假的版本在表补齐之后就成了「验一个不存在的场景」。
const WANT_EN = ctx.I18N.messages.en['err.BACKUP_CORRUPT'];
ok('英文表里有这条码的译文', typeof WANT_EN === 'string' && WANT_EN.length > 0, String(WANT_EN));
ctx.I18N.setLocale('en');
eq('非默认语言：按码取到译文，码仍留在前面（便于排查）',
   ctx.I18N.errorText(RAW_ERR), 'BACKUP_CORRUPT: ' + WANT_EN);
eq('非默认语言查不到码 → 回退服务端原文（宁可露出中文，不给空话）',
   ctx.I18N.errorText('NO_SUCH_CODE: 说明'), 'NO_SUCH_CODE: 说明');
eq('非默认语言也不碰没有码的消息',
   ctx.I18N.errorText('先发现并加入一个 Agent。'), '先发现并加入一个 Agent。');
ctx.I18N.setLocale('zh-CN');
eq('切回默认语言后恢复原样返回', ctx.I18N.errorText(RAW_ERR), RAW_ERR);
ok('服务端错误码出现在源码里（码是协议的一部分，不是前端编的）',
   /BACKUP_CORRUPT: /.test(fs.readFileSync(path.join(__dirname, '..', 'packages', 'a2n-node', 'src', 'a2n_node', 'backups.py'), 'utf8')));

// 发现页候选标签：全部经 I18N 查表，逐字值必须与历史版本一致。
const disc = ctx.window.A2NDiscovery;
eq('「支持积分」仍是原话', disc.label('pay', 'points'), '支持积分');
eq('直付标签带出对手方', disc.label('pay', 'direct_pay:did:a2n:ag_x'), '直付 · did:a2n:ag_x');
eq('未登记支付方式如实回显，不猜', disc.label('pay', 'weird'), 'weird');
eq('积分模式标签逐字保留', disc.label('pointMode', 'EARN'), '免费服务，供给方获积分');
eq('样品阶段标签逐字保留', disc.label('sample', 'INITIAL_FREE'), '前十次免费样品阶段');
eq('已验签标签逐字保留', disc.label('verification', 'CARD_VERIFIED'), '发现记录已验签');
eq('未声明的字段 → 未声明', disc.label('tags', '__undeclared__'), '未声明');
eq('验签的未知态有专门文案', disc.label('verification', '__undeclared__'), '未提供验签标记');
eq('积分货币保持「各发行方独立」口径', disc.label('currency', 'points'), '积分（各发行方独立）');
eq('节点积分网络口径保留', disc.label('chain', 'a2n'), '节点积分网络');

console.log('\n== 我不认（本机记录，只有留痕与撤回） ==');
vm.runInContext(`snapshot.disputes = [
  { id: 'ds_open', state: 'OPEN', scope: 'svc_a', task_id: 't1' },
  { id: 'ds_done', state: 'WITHDRAWN', scope: 'svc_b', task_id: 't2' }];`, ctx);
eq('找到未撤回的那条', (fns.openDisputeFor('svc_a', 't1') || {}).id, 'ds_open');
ok('已撤回的不算「未撤回」', fns.openDisputeFor('svc_b', 't2') === undefined);
ok('没记录的任务不伪造', fns.openDisputeFor('svc_z', 't9') === undefined);

console.log('\n== 试用期与样品：真实履历，不是宣传稿（VISION §1.2 #12） ==');
// 断言渲染口径，不验后端计数（后端幂等在 tests/test_trials.py / test_trial_delivery.py）。
ok('没有这个供给的试用数据 → 不凭空造进度', fns.trialBlock('svc_absent') === '');
ok('没有样品数据 → 不凭空造样品', fns.sampleBlock('svc_absent') === '');
vm.runInContext(`snapshot.trials = {
  svc_trial: { status: { service_id:'svc_trial', cap:10, completed:3, used:3, remaining:7, ended:false },
               samples: [ { id:'sp_1', task_id:'t1', version:'1.0.0', at:'2026-09-29T00:00:00Z', free:true,
                            summary:'做一条 30 秒短视频', preview:'成片包：分镜 3 段 / 字幕 / 配乐',
                            redactions:['email'], hidden_reason:'', kind:'real_delivery_sample_not_promotion',
                            digest:'abcdef0123456789' } ] },
  svc_done:  { status: { service_id:'svc_done', cap:10, completed:10, used:10, remaining:0, ended:true },
               samples: [ { id:'sp_2', task_id:'t2', version:'2.0.0', at:'2026-09-29T01:00:00Z', free:true,
                            summary:'', preview:'', redactions:[], hidden_reason:'这次交付没有可公开的内容',
                            kind:'real_delivery_sample_not_promotion', digest:'ffff0000' } ] },
  svc_fresh: { status: { service_id:'svc_fresh', cap:10, completed:0, used:0, remaining:10, ended:false },
               samples: [] } };`, ctx);
ok('试用中如实报 x/10 与剩余次数',
   /3\/10/.test(fns.trialBlock('svc_trial')) && /剩 7 次/.test(fns.trialBlock('svc_trial')),
   fns.trialBlock('svc_trial').slice(0, 120));
ok('毕业态明说「已满」且进度条置灰',
   /已满/.test(fns.trialBlock('svc_done')) && /tprog full/.test(fns.trialBlock('svc_done')),
   fns.trialBlock('svc_done').slice(0, 120));
ok('新供给 0/10 起步（未完成不虚报）',
   /0\/10/.test(fns.trialBlock('svc_fresh')) && !/full/.test(fns.trialBlock('svc_fresh')));
ok('无样品时给出下一步说明，不留空白', /首次完成调用后/.test(fns.sampleBlock('svc_fresh')));
const sb = fns.sampleBlock('svc_trial');
ok('样品带需求摘要', sb.includes('做一条 30 秒短视频'));
ok('样品带交付预览', sb.includes('成片包：分镜 3 段'));
ok('样品标明版本与时间', sb.includes('v1.0.0') && /2026/.test(sb));
ok('样品标出内容指纹（可核验，不可替换）', /abcdef0123/.test(sb));
ok('被隐去的键如实列出（不借隐私挑好评）', /已隐去：email/.test(sb));
ok('无公开内容的样品保留位置并说明原因',
   /没有可公开的内容/.test(fns.sampleBlock('svc_done')));
ok('样品块集中收纳在折叠区（不占主信息）', /<details class="samples">/.test(sb));

console.log('\n== 买方侧：调用前的试用声明 + 公开样例入口（VISION §6.4） ==');
ok('没有试用声明 → 不凭空造提示', fns.projTrial({}) === '');
const pj = fns.projTrial({ trial: { cap: 10, ended: false, notice: '初始采样的前 10 次技术交付不计费，且**一定公开为样品**（公开的是脱敏后的可公开投影；原始文件不进公开预览）。' } });
ok('试用中明说「免费试用期」与「一定公开为样品」',
   /免费试用期/.test(pj) && /一定公开为样品/.test(pj), pj.slice(0, 120));
ok('试用结束的供给明说「已结束」',
   /已结束/.test(fns.projTrial({ trial: { cap: 10, ended: true, notice: 'x' } })));
const PAY_UI = fs.readFileSync(path.join(path.dirname(FILE), 'payment.js'), 'utf8');
const QA_UI = fs.readFileSync(path.join(path.dirname(FILE), 'task-quality.js'), 'utf8');
const FORM_UI = fs.readFileSync(path.join(path.dirname(FILE), 'forms.js'), 'utf8');
const POINTS_UI = fs.readFileSync(path.join(path.dirname(FILE), 'points.js'), 'utf8');
const SEL_UI = fs.readFileSync(path.join(path.dirname(FILE), 'selection.js'), 'utf8');
const BIZ_UI = fs.readFileSync(path.join(path.dirname(FILE), 'business.js'), 'utf8');
ok('商品调用入口先打开条件和输入面板', /await window\.A2NTradeUI\.open\(b\.dataset\.try\)/.test(HTML));
ok('初始免费样品执行前按真实报价确认一定公开',
   /FREE_INITIAL/.test(PAY_UI) && /confirm\(I18N\.t\('pay\.confirm_/.test(PAY_UI)
   && /一定公开.*样品/.test(ctx.I18N.text('pay.confirm_free'))
   && /一定公开.*样品/.test(ctx.I18N.text('pay.confirm_automatic')));
ok('输入编辑期间不会直接调用 Agent',
   /I18N\.t\('rt\.render\.try'\)/.test(HTML) && /查看条件/.test(ctx.I18N.text('rt.render.try')));
// 样品口径已改为"一定公开"（用户裁决 2026-10-06）：旧的 consent 二次询问与
// 隐私占位说法都不该再出现在页面上 —— 留着就是给用户一个不存在的选项。
ok('页面上不再有「是否可用于公开样品」的二次询问',
   !/publicSample/.test(HTML) && !/a2nSampleConsent/.test(HTML));
ok('页面上不再出现「隐私占位」旧口径', !/隐私占位/.test(HTML));
ok('页面上不再出现「默认形成公开样品」旧口径（应为"一定公开"）',
   !/默认形成公开样品/.test(HTML) && /一定公开为样品/.test(HTML));
ok('样例入口取自卖方公开面 URL（快照样本 samples_url）', /samples_url/.test(HTML) && /data-samples/.test(HTML));
ok('推不出样例入口时如实说「入口未知」，不编地址',
   /I18N\.t\('rt\.render\.samples_unknown'\)/.test(HTML)
   && ctx.I18N.text('rt.render.samples_unknown') === '样例入口未知'
   && !/samples_url\s*\|\|\s*['"`]http/.test(HTML));

console.log('\n== 找 Agent：浏览进度 + 用词统一（待使用，不用旧词「收藏」） ==');
ok('「按已知能力清单浏览」给逐条进度，不再是静态一句',
   /I18N\.t\('rt\.act\.browse_progress',\{count:ids\.length,done:_done\}\)/.test(HTML)
   && /已回 \{done\}\/\{count\}/.test(ctx.I18N.text('rt.act.browse_progress')));
ok('浏览点下去先报 0/N（不是空白等待）',
   /I18N\.t\('rt\.act\.browse_start'/.test(HTML)
   && /已回 0\/\{count\}/.test(ctx.I18N.text('rt.act.browse_start')));
ok('浏览结果明说「已知能力清单，不是全网目录」',
   /I18N\.t\('rt\.act\.browse_done'/.test(HTML)
   && /已知能力清单，不是全网目录/.test(ctx.I18N.text('rt.act.browse_done')));
ok('页面字符串里不再出现旧词「收藏」（统一成「待使用」）', !/收藏/.test(HTML));

console.log('\n== 找 Agent：对比视图 + 详情里的真实样品（搬演示；无数据如实「暂无」） ==');
ok('视图切换有三个（卡片 / 紧凑 / 对比）',
   /id="vwCards"/.test(HTML) && /id="vwList"/.test(HTML) && /id="vwCompare"/.test(HTML));
ok('「对比」点了会切到 compare（不是死按钮）',
   /\$\('#vwCompare'\)\.onclick=\(\)=>\{disc\.view='compare'/.test(HTML));
ok('对比视图 = 同一张表 + cmp-tbl（多一列证据，不是另一套渲染）', /cmp-tbl/.test(HTML));
ok('对比列表头写明「历史信誉 / 质量证据」', /历史信誉 \/ 质量证据/.test(HTML));
// evidenceOf：卡上没证据 → 三条全部如实「暂无」，绝不替供给方编一个分数。
const evNone = fns.evidenceOf(priced);
ok('卡上没有证据 → 信誉如实「暂无」（不编分数）', /信誉 暂无/.test(evNone), evNone);
ok('卡上没有证据 → 质量如实「暂无质量实测」', /暂无质量实测/.test(evNone), evNone);
ok('卡上没有证据 → 评价如实「评价样本不足」', /评价样本不足/.test(evNone), evNone);
const evReal = fns.evidenceOf({ evidence: { reputation: 0.93,
  quality: { measured: true, quality: 0.02, samples: 12 },
  ratings: { published: true, score: 4.7, raters: 9 } } });
ok('卡上真有证据 → 照实渲染信誉分', /信誉 93\/100/.test(evReal), evReal);
ok('卡上真有证据 → 照实渲染质量样本数', /12 样本/.test(evReal), evReal);
ok('卡上真有证据 → 照实渲染评价人数', /9 人/.test(evReal), evReal);
// samplesUrlOf：与后端 _public_samples_url 同一口径；推不出就空串（不编地址）。
eq('从卡 url 推出公开样品面地址', fns.samplesUrlOf(priced),
   'http://n1/public/v1/samples?service_id=svc_a');
eq('没有 url 的裸卡 → 不编地址', fns.samplesUrlOf({ name: 'x' }), '');
eq('不是 A2N 供给路径 → 不编地址', fns.samplesUrlOf({ url: 'http://x/other/1' }), '');
ok('详情里有「真实交付样品」区块', /真实交付样品/.test(HTML));
ok('推不出样品入口时如实说「入口未知」，不编地址', /入口未知/.test(HTML));
ok('跨源读取被同源策略挡住时如实说明（不假装读到）', /同源策略挡住是正常的/.test(HTML));
ok('样品区给用户可见的新窗口入口', /data-open-samples/.test(HTML));
const rs = fns.renderSamples({ count: 1, samples_total: 3, completed: 4, samples: [
  { id: 'sp_x', version: '1.0.0', at: '2026-09-29T00:00:00Z', summary: '做一条 30 秒短视频',
    preview: '成片包：分镜 3 段', redactions: ['email'], digest: 'abcdef0123456789' }] });
ok('样品渲染带着「已完成 N 次调用」的事实计数', /已完成 4 次调用/.test(rs), rs.slice(0, 120));
ok('样品渲染带需求摘要与交付预览', rs.includes('做一条 30 秒短视频') && rs.includes('成片包：分镜 3 段'));
ok('样品渲染标出内容指纹与已隐去项', /abcdef0123/.test(rs) && /已隐去：email/.test(rs));
ok('空样品不造数据，如实说「首次完成调用后才会沉淀」',
   /首次完成调用后才会沉淀/.test(fns.renderSamples({ samples: [] })));

console.log('\n== 双方反馈：原始评价与聚合信誉分开（兼容 v1 / v2） ==');
// 新版增加遵守约定与争议处理体验；旧版维度仍可展示。
ok('买方 → 供给：质量 / 按时 / 沟通 / 遵守约定 / 争议处理',
   fns.FB_DIMS.buyer_to_seller.map(d => d[0]).join() === 'quality,punctual,communication,honoring,dispute_handling');
ok('供给 → 买方：需求按约 / 沟通配合 / 遵守约定 / 争议处理',
   fns.FB_DIMS.seller_to_buyer.map(d => d[0]).join() === 'on_spec,cooperative,honoring,dispute_handling');
// 状态硬闸：只有已交付才认交付质量（ACCEPTED=已验收 也算交付）。
ok('交付质量只对 COMPLETED/ACCEPTED/SETTLED 开放',
   fns.FB_DELIVERED.has('COMPLETED') && fns.FB_DELIVERED.has('ACCEPTED') &&
   fns.FB_DELIVERED.has('SETTLED') &&
   !fns.FB_DELIVERED.has('FAILED') && !fns.FB_DELIVERED.has('CANCELED') &&
   !fns.FB_DELIVERED.has('DELIVERY_UNKNOWN'));
// 维度分文本：只列真打了的分；没打分时如实说没打分。
eq('维度分如实拼出（不打的不编）',
   fns.fbScoreText({ direction: 'buyer_to_seller', dimensions: { quality: 4, punctual: 5 } }),
   '交付质量 4/5 · 按时 5/5');
ok('只留原因没打分 → 如实说「未打分」，不虚构成 0 分',
   /未打分（只留了一句原因）/.test(fns.fbScoreText({ direction: 'buyer_to_seller', dimensions: {}, note: '还行' })));
ok('维度全空且无原因 → 只回「未打分」',
   fns.fbScoreText({ direction: 'buyer_to_seller', dimensions: {} }) === '未打分');
// 验签状态如实标注（伪造留痕不采信）。
ok('验签未过如实标「未采信」', /验签未过 · 未采信/.test(fns.fbBadge({ verified: false })), fns.fbBadge({ verified: false }));
ok('自源反馈如实标「自源」', /自源/.test(fns.fbBadge({ verified: true, self_source: true })));
ok('正常采信不硬贴标签', fns.fbBadge({ verified: true }) === '');
// 清单表：谁写的、版本、对手方计数、身份口径。
const ft = fns.feedbackTable([
  { task_id: 't1', direction: 'buyer_to_seller', source: 'self', service_id: 'svc_a',
    counterparty_did: 'did:a2n:ag_x', dimensions: { quality: 4, punctual: 5 }, note: '交付快',
    revision: 2, versions: 2, verified: true },
  { task_id: 't1', direction: 'seller_to_buyer', source: 'counterparty', service_id: 'svc_a',
    author_did: 'did:a2n:ag_x', dimensions: { on_spec: 5 }, note: '', revision: 1, versions: 1, verified: false }]);
ok('清单表标出方向（买方 → 供给 / 供给 → 买方）',
   /买方 → 供给/.test(ft) && /供给 → 买方/.test(ft));
ok('清单表标出谁写的（我写的 / 对方写的）', /我写的/.test(ft) && /对方写的/.test(ft));
ok('清单表照实列维度分', /交付质量 4\/5 · 按时 5\/5/.test(ft));
ok('清单表标版本与版本数', /v2/.test(ft) && /共 2 版/.test(ft));
ok('清单表对伪造项标「未采信」', /验签未过 · 未采信/.test(ft));
ok('清单表如实数交易对手 did 个数', /已出现 1 个交易对手 did/.test(ft));
ok('清单表写明「不同 did 不证明独立个人」（一个节点一个身份）',
   /不同 did 只代表不同身份标识，不证明背后是独立个人/.test(ft));
ok('清单表区分原始评价与聚合信誉，标明资料支持量',
   /这里列原始评价/.test(ft) && /聚合信誉和本机推荐另外展示/.test(ft) && /资料支持量/.test(ft));
ok('R2 没有「评分 / 综合分」这类聚合列（只列维度分事实）',
   !/<th>(评分|综合分|总分)<\/th>/.test(ft));
// 未评价不阻塞、不算差评。
ok('调用记录里未评价如实写「未评价」，不逼人评',
   /I18N\.t\('rt\.call\.not_rated'\)/.test(HTML) && /I18N\.t\('rt\.call\.write'\)/.test(HTML)
   && ctx.I18N.text('rt.call.not_rated') === '未评价');
ok('账户页空态写明「未评价不代表差评，也不妨碍任何事」',
   /I18N\.t\('rt\.fb\.empty'\)/.test(HTML)
   && /未评价不代表差评，也不妨碍任何事/.test(ctx.I18N.text('rt.fb.empty')));
// 端点：写=open、改=revise；同一方向已存在时前端走 revise。
ok('写反馈走 /v1/feedback/open、改反馈走 /v1/feedback/revise',
   /'\/v1\/feedback\/open'/.test(HTML) && /'\/v1\/feedback\/revise'/.test(HTML));
ok('同一方向已有反馈时前端自动改走 revise（不重复开单）',
   /existing\?'\/v1\/feedback\/revise':'\/v1\/feedback\/open'/.test(HTML));
// 快照暴露。
ok('快照暴露 feedback 与 feedback_counts',
   /feedback:\[\]/.test(HTML) && /feedback_counts:\{\}/.test(HTML));
// 补交（F3）：买方反馈写完会尝试随回执再捎一次，并如实说送到没送到。
ok('写完买方反馈后会尝试补交（走 /v1/feedback/deliver）',
   /'\/v1\/feedback\/deliver'/.test(HTML) && /d\.delivered/.test(HTML));
ok('补交失败如实报「未送达」并说明仍留着这一份（不假装送到）',
   /I18N\.t\('rt\.act\.fb_undelivered',\{reason:/.test(HTML)
   && /未送达：\{reason\}/.test(ctx.I18N.text('rt.act.fb_undelivered'))
   && /仍存着这一份/.test(ctx.I18N.text('rt.act.fb_undelivered')));
ok('供给方反馈如实说「R2 不主动推送，等对方重捎回执」',
   /I18N\.t\('rt\.act\.fb_provider_note'\)/.test(HTML)
   && /R2 不主动推送/.test(ctx.I18N.text('rt.act.fb_provider_note')));
// 只读接口（F3 补）：控制台真的去读 GET /v1/feedback/{id}/versions 与 summary（不是摆设）。
ok('控制台「版本」按钮读 GET /v1/feedback/{id}/versions',
   /data-fb-versions=/.test(HTML) && /\/versions'/.test(HTML));
ok('控制台「摘要」按钮读 GET /v1/feedback/summary',
   /data-fb-summary/.test(HTML) && /'\/v1\/feedback\/summary'/.test(HTML));
ok('摘要弹窗写明「不是信誉分」（只列事实计数与各维平均）',
   /I18N\.t\('rt\.act\.fb_summary_title'\)/.test(HTML)
   && /不是信誉分/.test(ctx.I18N.text('rt.act.fb_summary_title')));

// Candidate display must never change discovery, query balances or settle a call.
console.log('\n== 发现后筛选：支付声明、多选与隔离 ==');
{
  const d = ctx.window.A2NDiscovery, previousPool = ctx.getPool(), previousState = {...state};
  const pts = {name:'积分服务',version:'2.0',defaultInputModes:['application/json'],
    skills:[{id:'inspect',tags:['文本'],outputModes:['application/json']}],
    'x-a2n':{points:{enabled:true,modes:['EARN','DEBT']},trial:{cap:10,ended:false},
      projection:{node_did:'did:a2n:supplier',attested:true}}};
  const cash = {name:'货币服务',version:'1.0',skills:[{id:'inspect',tags:['文本']}],
    'x-a2n':{payments:{methods:[{method:'evm-native/1',network:'eip155:1',currency:'ETH'},
      {method:'x402/2',network:'eip155:1',currency:'USDC'}]},trial:{cap:10,ended:true}}};
  const moduleOnly = {name:'只安装积分模块',skills:[{id:'translate'}],
    'x-a2n':{payments:{points_method:'a2n-points/1'},projection:{node_did:'did:a2n:module'}}};
  const items = [{card:pts,key:{provider_did:'did:a2n:supplier',service_id:'pts'},source:'coordination',verification:'CARD_VERIFIED'},
    {card:cash,key:{provider_did:'did:a2n:cash',service_id:'cash'},source:'coordination'},
    {card:moduleOnly,source:'public-node'}];
  const original = JSON.stringify(items);
  eq('Agent 服务策略声明的积分能力可识别',d.payments(pts),['points']);
  eq('仅安装 points_method 不冒充 Agent 支持积分',d.payments(moduleOnly),[]);
  eq('新支付声明归一为 native 与 x402',d.payments(cash),['native','x402']);
  eq('禁用积分策略优先于旧 accepts 和通用 methods',d.payments({accepts:['points'],
    'x-a2n':{points:{enabled:false,modes:['PAY']},payments:{methods:[{method:'points'}]}}}),[]);
  eq('仍兼容旧卡明确的积分接受声明',d.payments({accepts:['a2n-points/1']}),['points']);
  eq('未知支付协议名称照实保留',d.payments({accepts:['vendor/pay','__proto__','toString']}),['vendor/pay','__proto__','toString']);
  eq('未知协议没有属性继承造成的错误标签',d.label('pay','toString'),'toString');
  eq('输入格式与技能输出格式均来自卡声明',d.facts(items[0]).input,['application/json']);
  eq('技能输出格式可筛选',d.facts(items[0]).output,['application/json']);
  eq('多币种与网络独立保留',d.facts(items[1]).currency,['ETH','USDC']);
  eq('积分按独立发行方类型显示，不折算货币',d.facts(items[0]).currency,['points']);
  eq('没有声明时不推断样品阶段',d.facts(items[2]).sample,[d.UNKNOWN]);
  eq('身份自证不等于发现过程验签',d.facts({card:pts}).verification,[d.UNKNOWN]);
  eq('只有候选的验签标记才算已验签',d.facts(items[0]).verification,['CARD_VERIFIED']);
  ctx.setPool(items);
  const reset = () => {Object.keys(state).forEach(k=>state[k]=k==='sort'?'default':k==='view'?'cards':k==='verified'?false:'');};
  reset();state.pay=['points'];
  eq('支持积分只显示真正声明积分的供给',fns.discFiltered().map(i=>i.card.name),['积分服务']);
  state.pay=d.toggle(state.pay,'native');
  eq('同组多选取并集',fns.discFiltered().map(i=>i.card.name),['积分服务','货币服务']);
  state.pointMode=['DEBT'];state.skill=['inspect'];
  eq('支付、积分模式与能力跨组取交集',fns.discFiltered().map(i=>i.card.name),['积分服务']);
  state.pointMode=['PAY'];
  eq('不支持已有积分兑换的供给不会被误选',fns.discFiltered().length,0);
  eq('分面计数忽略自身条件，保留其他条件',fns.discFiltered('pointMode').length,2);
  vm.runInContext('applyDisc()',ctx);
  ok('零命中时原始候选仍完整保存',ctx.getPool().length===3&&JSON.stringify(ctx.getPool())===original);
  ok('零命中的已选模式仍可见、可清除',ctx.document.querySelector('#facets').innerHTML.includes('data-val="PAY"'));
  reset();state.provider=['did:a2n:supplier'];state.version=['2.0'];state.input=['application/json'];state.tags=['文本'];
  eq('供给方、版本、格式及标签可组合',fns.discFiltered().map(i=>i.card.name),['积分服务']);
  reset();state.version=[d.UNKNOWN];
  eq('未声明条件可显式选择',fns.discFiltered().map(i=>i.card.name),['只安装积分模块']);
  reset();state.pay=['vendor/pay'];
  eq('未知支付条件仍准确筛选，不能放过所有商品',fns.discFiltered().length,0);
  let calls=0;const fetchBefore=ctx.fetch;ctx.fetch=()=>{calls++;throw Error('unexpected I/O')};
  reset();vm.runInContext('applyDisc()',ctx);ctx.fetch=fetchBefore;
  eq('清除筛选恢复全部候选',ctx.getFound().length,3);
  eq('筛选和渲染不触发任何网络请求',calls,0);
  ok('筛选和切换视图不改写签名卡',JSON.stringify(items)===original);
  Object.assign(state,previousState);ctx.setPool(previousPool);
}

// ---- 协调层（web/coordination.js）：跑真函数、断真行为，不是 grep 源码 ----
console.log('\n== 协调层（公共发现会话） ==');
const el = sel => ctx.document.querySelector(sel);
const coord = ctx.coord;

ok('协调层脚本能加载并暴露契约 §2 的全部 8 个会话状态',
   ['RUNNING', 'SATISFIED', 'PAUSED', 'BUDGET_REACHED', 'FRONTIER_EXHAUSTED',
    'ISOLATED', 'CANCELLED', 'EXPIRED'].every(s => coord.states[s]),
   '状态枚举与 COORDINATION-API §2 不全一致');

const session = extra => Object.assign({ search_id: 'sid1', state: 'RUNNING', round: 1,
  candidate_count: 0, frontier_count: 0, budget_used: {}, budget_remaining: {} }, extra);

coord.set(session({ state: 'RUNNING', round: 2, candidate_count: 5, frontier_count: 3,
  budget_used: { remote_operations: 7 }, budget_remaining: { remote_operations: 13 } }));
coord.render();
ok('进度条照实写：状态 · 第几轮 · 几个商品 · 待查几项 · 本轮请求几次、剩余几次',
   el('#coordProgress').textContent
     === '正在发现 · 第 2 轮 · 5 个商品 · 待查 3 项 · 本轮请求 7 次，剩余 13 次',
   el('#coordProgress').textContent);
ok('RUNNING：可暂停、但不可再「继续发现更多」（不重复开搜）',
   el('#coordPause').disabled === false && el('#coordContinue').disabled === true);

coord.set(session({ state: 'PAUSED' }));
coord.render();
ok('PAUSED：不可暂停、可继续；文案是「已暂停」',
   el('#coordPause').disabled === true && el('#coordContinue').disabled === false
   && /^已暂停/.test(el('#coordProgress').textContent));

coord.set(session({ state: 'EXPIRED' }));
coord.render();
ok('EXPIRED 终止态：暂停与继续都不可点',
   el('#coordPause').disabled === true && el('#coordContinue').disabled === true);

coord.set(null);
coord.render();
ok('没有会话时整条进度条隐藏（不摆空壳）', el('#coordSession').hidden === true);

// 找回上次的会话，而不是每次重开。刷新后从 localStorage 接着看。
ok('搜索会话 id 存本机 localStorage，刷新后接着看（不重开）',
   /localStorage\.setItem\('a2n\.coord\.search'/.test(COORD)
   && /localStorage\.getItem\('a2n\.coord\.search'/.test(COORD));
// 契约：创建带 Idempotency-Key；changed-state 操作带 If-Match。
ok('创建/续查/选路都带 Idempotency-Key，暂停续查再带 If-Match',
   (COORD.match(/Idempotency-Key/g) || []).length >= 4 && (COORD.match(/If-Match/g) || []).length >= 3);
ok('搜索只把 skill 与本地偏好交给本机 /v1/coord，不上网',
   /request\('\/v1\/coord\/searches',\{skill,preferences:/.test(COORD));
// 通道检查不冒充调用。文案已迁入 I18N 消息表，逐字值必须保留。
ok('通道检查写明「只读取签名商品卡，不执行 Agent」，且可达性三态如实',
   ctx.I18N.text('coord.route_note') === '此检查只读取签名商品卡，不执行 Agent。实际调用仍可能失败。'
   && ctx.I18N.text('coord.reach.verified') === '入口已核验'
   && ctx.I18N.text('coord.reach.unreachable') === '入口暂不可达'
   && ctx.I18N.text('coord.reach.unchecked') === '尚未检查');
// 基础发现与免费样品不可关，另外三项分别选择。
// 文案已迁入 I18N 消息表：断言「三项走 I18N 取文案 + 无 samples 键 + 公平面文案逐字保留」。
ok('公平面写明发现与样品始终公开，三个可选服务分别声明',
   ['witness', 'task_relay', 'blob_cache'].every(k => COORD.includes("['" + k + "','coord.service." + k + "']"))
   && !COORD.includes("['samples'")
   && ctx.I18N.text('coord.public_state') === '基础协调发现和免费交付样品始终公开，不能关闭。哈希见证、任务中继和加密文件转送由你分别选择。');
ok('面板写明「基础发现与邻居引荐不能关闭」（不给"关掉参与"的错觉）',
   /data-i18n="rt\.acct\.public_note"/.test(FILE_BODY)
   && /基础发现与邻居引荐不能关闭/.test(ctx.I18N.text('rt.acct.public_note')));
ok('没有一键总开关残留：页面与内联脚本都不含 publicToggle',
   !/publicToggle/.test(FILE_BODY));
// 「没找到」有两种原因，界面不许混成同一句话。
// 文案已迁入 I18N 消息表：断言「代码引用了对应 key + 消息值逐字保留」。
ok('ISOLATED（一个通道都没配）如实说"没有向任何节点发问"',
   /coord\.no_channel/.test(COORD) && /'ISOLATED'/.test(COORD)
   && ctx.I18N.text('coord.no_channel').includes('没有向任何节点发问'));
ok('有通道但取不回商品时，把失败原因原样带出来，不装成 0 个候选',
   /coord\.found_nothing/.test(COORD)
   && ctx.I18N.text('coord.found_nothing').includes('已向外发出查询，但没能取回可核验的商品'));
// 任务评审面板：文案已迁入 I18N 消息表（qa.*），值逐字保留；未知状态如实回显原值。
ok('任务评审面板只按合法键名取表，正文不持有展示文字',
   /I18N\.text\('qa\.state\.'\+/.test(QA_UI) && /I18N\.text\('qa\.dim\.'\+/.test(QA_UI)
   && !/stateNames|names\[k\]/.test(QA_UI));
ok('任务评审逐字文案保留（标题 / 重新检查按钮 / 64 条上限）',
   ctx.I18N.text('qa.heading') === '任务评审与实际体验校准'
   && ctx.I18N.text('qa.recheck_btn') === '重新检查原成果'
   && ctx.I18N.text('qa.check_limit') === '一份任务最多 64 条检查要求');
// 这两个句子被 scripts/task_quality_console_check.js 按渲染文本锚定，插值后必须逐字不变。
ok('校准进度按占位符插值，逐字句子不变（含「合格记录 0 笔」「已同意的实际记录 0 份」）',
   ctx.I18N.t('qa.fit_result', { state: '真实使用记录不足', samples: 0, providers: 0, days: 0 })
     === '真实使用记录不足；合格记录 0 笔，0 个供应节点，0 天。'
   && ctx.I18N.t('qa.calibration_status', { reviews: 0, consented: 0, controlled: 0, providers: 0,
        days: 0, human: 0, assistant: 0, state: '使用默认参数' })
     === '本口径有 0 份评审，已同意的实际记录 0 份，测试记录 0 份。覆盖 0 个供应节点、0 天；本人评价 0 份，助手代评 0 份。使用默认参数。');
ok('查不到的任务评审状态返回 null，界面回退原值而不是编一个中文',
   ctx.I18N.text('qa.state.NO_SUCH_STATE') === null);
// 输入表单与结果呈现：文案已迁入 I18N 消息表（form.*），插值后逐字不变。
ok('输入表单正文不持有展示文字，结果字段名只按合法键名取表',
   /I18N\.text\('form\.field\.'\+/.test(FORM_UI) && !/names\[/.test(FORM_UI));
ok('表单逐字文案保留（必填/可选、数字提示、结算前缀）',
   ctx.I18N.t('form.field_required', { label: '标题' }) === '标题（必填）'
   && ctx.I18N.t('form.field_optional', { label: '类型' }) === '类型（可选）'
   && ctx.I18N.t('form.number_integer', { label: '数量' }) === '数量需要填写有效整数；大整数需要服务提供字符串输入字段。'
   && ctx.I18N.t('form.settlement', { state: '已确认' }) === '结算：已确认');
ok('查不到的结果字段名返回 null，界面回退原字段名而不是编一个中文',
   ctx.I18N.text('form.field.no_such_field') === null);
// 积分面板：文案已迁入 I18N 消息表（points.*），协议值（EARN/DEBT/PAY 等）不翻译。
ok('积分面板正文不持有展示文字，结算状态只按合法键名取表',
   /I18N\.text\('points\.state\.'\+/.test(POINTS_UI) && !/const labels=\{/.test(POINTS_UI));
ok('积分逐字文案保留（余额行/接受行/结算状态）',
   ctx.I18N.t('points.balance_line', { who: '我持有的自家积分', amount: '12.5', reserved: '2' }) === '我持有的自家积分：12.5；预留 2'
   && ctx.I18N.t('points.acceptance_line', { issuer: 'ag_peer', state: '接受', denominator: '10', numerator: '8' }) === 'ag_peer：接受，10 对方积分 → 8 自家积分价值'
   && ctx.I18N.text('points.state.CONFIRMED') === '各方已完成结算'
   && ctx.I18N.text('points.state.ABORTING') === '整体中止已决定，等待释放预留');
ok('查不到的积分结算状态返回 null，界面回退原协议值而不是编一个中文',
   ctx.I18N.text('points.state.WEIRD_STATE') === null);
// 本机评估面板：文案已迁入 I18N 消息表（sel.*），维度/状态/原因只留键名清单。
ok('评估面板正文不持有展示文字，维度表只留合法键名',
   /const DIMS=\['fit'/.test(SEL_UI) && !/const labels=\{/.test(SEL_UI) && !/const states=\{/.test(SEL_UI) && !/const reasons=\{/.test(SEL_UI));
ok('评估逐字文案保留（标题 / 无资料 / 维度行 / 适合度头）',
   ctx.I18N.text('sel.heading') === '按我的需求推荐'
   && ctx.I18N.text('sel.no_evidence') === '暂无本机评估资料'
   && ctx.I18N.t('sel.dim_line', { dimension: '交付质量', value: '90.0', state: '资料充分' }) === '交付质量 90.0（资料充分）'
   && ctx.I18N.t('sel.summary_head', { score: '87.4', state: '满足已知要求', stale: '' }) + '<br>' === '适合度 87.4 / 100 · 满足已知要求<br>');
ok('查不到的评估状态与原因返回 null，界面回退原值而不是编一个中文',
   ctx.I18N.text('sel.state.NO_SUCH_STATE') === null && ctx.I18N.text('sel.reason.CUSTOM_UNKNOWN') === null);
// 业主面板：协商 / 私有文件 / 备份 / 签名包 / 升级五块文案已迁入 biz.*。
ok('业主面板正文不持有展示文字，升级状态与协商消息类型只留前缀查表',
   !/upgradeLabels=\{/.test(BIZ_UI) && !/const labels=\{REPLY/.test(BIZ_UI)
   && /I18N\.text\('biz\.upgrade\.state\.'/.test(BIZ_UI) && /I18N\.text\('biz\.resolve\.view\.'/.test(BIZ_UI));
ok('业主逐字文案保留（升级状态 / 安装行 / 送达行）',
   ctx.I18N.text('biz.upgrade.state.ROLLED_BACK_BINARY') === '已回到原程序'
   && ctx.I18N.t('biz.pkg.row', { name: '文本检查', version: '1.2.3', state: '已安装', did: 'did:a2n:ag_x' }) === '文本检查 · 版本 1.2.3 · 已安装 · 发布者 did:a2n:ag_x'
   && ctx.I18N.t('biz.resolve.delivery_item', { id: '00000002', state: '尚未送达' }) === '00000002：尚未送达'
   && ctx.I18N.t('biz.asset.limit', { mib: 1 }) === '当前单文件上限 1 MiB。');
ok('查不到的升级状态与消息类型返回 null，界面回退原协议值而不是编一个中文',
   ctx.I18N.text('biz.upgrade.state.NO_SUCH_STATE') === null && ctx.I18N.text('biz.resolve.view.WEIRD_KIND') === null);
// 支付面板：结算状态与三种付款方式的文案已迁入 pay.*，正文只留前缀查表。
ok('支付面板正文不持有展示文字，结算状态只留前缀查表，付款方式按模式分支取键',
   !/const labels=\{READY/.test(PAY_UI) && /I18N\.text\('pay\.state\.'/.test(PAY_UI)
   && /case 'PAY':return I18N\.t\('pay\.plan_mode\.PAY'/.test(PAY_UI)
   && /I18N\.t\('pay\.option_mode\.PAY'\)/.test(PAY_UI) && !/modes\[p\.terms\.mode\]/.test(PAY_UI));
ok('支付逐字文案保留（状态 / 金额行 / 退款行 / 通道行）',
   ctx.I18N.text('pay.state.UNKNOWN') === '结果未知 · 原订单已锁定'
   && ctx.I18N.t('pay.amount_minor', { amount: '1.5', currency: 'USDT' }) === '1.5 USDT（最小单位）'
   && ctx.I18N.t('pay.refund_state', { state: '付款已确认' }) === '退款：付款已确认'
   && ctx.I18N.t('pay.configured_line', { native: 2, x402: '可付款', provider: '未配置' }) === '直接转账通道 2 个；x402 可付款；x402 收款 未配置。旧交易保留原通道，新设置用于新交易。');
ok('查不到的支付状态返回 null，界面回退原协议值而不是编一个中文',
   ctx.I18N.text('pay.state.WEIRD_STATE') === null);

(async () => {
  // 真跑表单：未交付不可评质量；无争议不可评处理体验。
  coord.setRequest(async () => ({metadata:{technical_delivery:false}}));
  vm.runInContext('snapshot.disputes = [];', ctx);
  await fns.fbPanel('gated-scope', 'gated-task', 'buyer_to_seller', 'FAILED');
  const noDelivery=el('#fbBody').innerHTML;
  ok('未交付任务禁用质量维度并说明原因',
     /data-fb-dim="quality"\s+disabled/.test(noDelivery) && /未交付/.test(noDelivery));
  await fns.fbPanel('gated-scope', 'gated-task', 'buyer_to_seller', 'COMPLETED');
  const delivered=el('#fbBody').innerHTML;
  ok('已交付允许质量评价，但未记录争议时仍禁用争议体验',
     !/data-fb-dim="quality"\s+disabled/.test(delivered) && /data-fb-dim="dispute_handling"\s+disabled/.test(delivered));
  vm.runInContext("snapshot.disputes = [{scope:'gated-scope',task_id:'gated-task'}];", ctx);
  await fns.fbPanel('gated-scope', 'gated-task', 'seller_to_buyer', 'COMPLETED');
  const disputed=el('#fbBody').innerHTML;
  ok('供给方仅在同笔有争议时可评争议处理，始终没有质量维度',
     !/data-fb-dim="dispute_handling"\s+disabled/.test(disputed) && !/data-fb-dim="quality"/.test(disputed));
  vm.runInContext('snapshot.disputes = [];', ctx);
  // loadCoordSession 的真行为：只收「有已验签卡」的候选，来源标 coordination，错误如实留痕。
  coord.set({ search_id: 'sid2' });
  coord.setApplyDisc(() => {});                 // 隔离：只验协调层的归并与留痕，渲染另有断言
  coord.setRequest(async p => p.includes('/candidates')
    ? { items: [
        { key: { provider_did: 'did:a2n:ag_p', service_id: 'svc_x' },
          cards: [{ card: priced, card_hash: 'h' }], routes: [{ route_id: 'r1' }],
          sources: [{ node_did: 'did:a2n:ag_p' }], verification: 'CARD_VERIFIED' },
        { key: { provider_did: 'did:a2n:ag_q', service_id: 'svc_y' },
          cards: [], routes: [], sources: [], verification: 'HINT' }],
      errors: [{ error: '一个来源超时' }] }
    : { search_id: 'sid2', state: 'SATISFIED', round: 1, candidate_count: 1, frontier_count: 0,
        budget_used: { remote_operations: 1 }, budget_remaining: { remote_operations: 9 } });
  await coord.load('sid2');
  ok('候选刷新事件携带当前搜索编号和状态',
     events.some(event=>event.type==='a2n:candidates'&&event.detail.search_id==='sid2'&&event.detail.state==='SATISFIED'));
  ok('发现结果只收「有已验签卡」的候选：无卡的来源不算商品',
     coord.get().search_id === 'sid2' && ctx.getPool().length === 1, String(ctx.getPool().length));
  ok('发现来的候选 source 标成 coordination（与目录/p2p 来源区分开）',
     ctx.getPool()[0] && ctx.getPool()[0].source === 'coordination');
  ok('候选带上「哪次搜索来的」，选通道时才能回查同一次会话',
     ctx.getPool()[0] && ctx.getPool()[0].search_id === 'sid2');
  ok('单来源失败如实写进搜索错误条，不抹掉已验证候选',
     /一个来源超时/.test(el('#searchErrors').textContent), el('#searchErrors').textContent);

  // 错误条走 errorText()：服务端给的是「CODE: 中文说明」，说明按码取译、码留在前面。
  // 这条要钉住「真的用了 errorText」而不是「errorText 这函数是好的」——
  // 以前那处直接取 e.error 的原文，英文界面整条露中文，而 errorText() 本身没毛病。
  coord.set({ search_id: 'sid3' });
  coord.setApplyDisc(() => {});
  coord.setRequest(async p => p.includes('/candidates')
    ? { items: [], errors: [{ error: 'COORD_CARD_HINT_MISMATCH: 商品卡与发现线索不一致' }] }
    : { search_id: 'sid3', state: 'SATISFIED', round: 1, candidate_count: 0, frontier_count: 0,
        budget_used: {}, budget_remaining: {} });
  await coord.load('sid3');
  const errBar = el('#searchErrors').textContent;
  ok('搜索错误条按码取译（默认语言原样返回，一个字节都不改）',
     errBar === 'COORD_CARD_HINT_MISMATCH: 商品卡与发现线索不一致', errBar);
  // 英文下同一处必须没有中文：中文界面逐字不变，英文界面换成译文。
  ctx.I18N.setLocale('en');
  coord.set({ search_id: 'sid4' });
  coord.setRequest(async p => p.includes('/candidates')
    ? { items: [], errors: [{ error: 'COORD_CARD_HINT_MISMATCH: 商品卡与发现线索不一致' }] }
    : { search_id: 'sid4', state: 'SATISFIED', round: 1, candidate_count: 0, frontier_count: 0,
        budget_used: {}, budget_remaining: {} });
  await coord.load('sid4');
  const errBarEn = el('#searchErrors').textContent;
  ok('英文下搜索错误条不再露中文',
     errBarEn === 'COORD_CARD_HINT_MISMATCH: The listing card does not match the discovery hint'
     && !/[一-龥]/.test(errBarEn), errBarEn);
  ctx.I18N.setLocale('zh-CN');

  vm.runInContext('notice = message => { globalThis.lastCoordNotice = message; };', ctx);
  coord.set(null);
  coord.setRequest(async p => p.includes('/candidates')
    ? { items: [{ key: { provider_did: 'did:a2n:ag_p', service_id: 'svc_x' },
        cards: [{ card: priced, card_hash: 'h' }], routes: [], sources: [],
        verification: 'CARD_VERIFIED' }], errors: [{ error: '一个来源超时' }] }
    : { search_id: 'partial', state: 'SATISFIED', revision: 1, round: 1,
        candidate_count: 1, frontier_count: 0, budget_used: {}, budget_remaining: {} });
  await coord.search('add');
  ok('部分来源失败仍明确显示已经取得的商品数量', ctx.getPool().length === 1
     && /已发现 1 个商品/.test(ctx.lastCoordNotice)
     && /部分来源查询失败/.test(ctx.lastCoordNotice)
     && !/没能取回/.test(ctx.lastCoordNotice), ctx.lastCoordNotice);

  // ---- runtime.html 自身：标记只留键、脚本只留代码，文案全在消息表 ----
  console.log('\n== 控制台骨架：标记只留 i18n 键（文案全在消息表） ==');
  {
    const markup = HTML.slice(0, HTML.indexOf('<script>'))
      .replace(/<style>[\s\S]*?<\/style>/g, '').replace(/<!--[\s\S]*?-->/g, '');
    const script = HTML.slice(HTML.indexOf('<script>'), HTML.lastIndexOf('</script>'))
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n').map(l => l.replace(/(^|[^:'"`])\/\/.*$/, '$1')).join('\n');
    ok('页面骨架里不残留中文（文案全走 data-i18n）', !/[\u4e00-\u9fa5]/.test(markup),
       (markup.match(/[\u4e00-\u9fa5]+/g) || []).slice(0, 5).join(' / '));
    ok('内联脚本不残留展示文字（中文只允许出现在注释里）', !/[\u4e00-\u9fa5]/.test(script),
       (script.match(/[\u4e00-\u9fa5]+/g) || []).slice(0, 5).join(' / '));
    ok('骨架的文本节点用 data-i18n 填', /data-i18n="rt\.title"/.test(markup)
       && ctx.I18N.text('rt.title') === 'A2N · Agent 网络控制台');
    ok('属性文案用 data-i18n-<attr>（placeholder / title / aria-label / alt）',
       /data-i18n-placeholder=/.test(markup) && /data-i18n-title=/.test(markup)
       && /data-i18n-aria-label=/.test(markup));
    // localize 的两条语义：查得到才写，查不到保持原样（不把键名甩给用户）。
    ok('localize() 对文本节点与属性都只在查到译文时才写入',
       /function localize\(root\)/.test(HTML)
       && /const v=I18N\.text\(el\.getAttribute\('data-i18n'\)\);if\(v!==null\)el\.textContent=v/.test(HTML)
       && /const v=I18N\.text\(el\.getAttribute\(pair\[1\]\)\);if\(v!==null\)el\.setAttribute\(pair\[0\],v\)/.test(HTML));
    ok('维度标题查 rt.facet.*，查不到回显维度键（不编标题）',
       ctx.I18N.text('rt.facet.cat') === '行业分类' && fns.facetTitle('no-such-facet') === 'no-such-facet');
    ok('反馈方向查 rt.fb.dir.*，查不到回显原值',
       fns.fbDirLabel('buyer_to_seller') === '买方 → 供给' && fns.fbDirLabel('zzz') === 'zzz');
  }

  // ---- 各模块 JS：不留展示文字（文案全在消息表）----
  //
  // 为什么单独有一段：上面那两条只查 runtime.html 的骨架与内联脚本，
  // **不查 JS 动态生成的 HTML**。模块里 `panel.innerHTML='…'` 这种写法，
  // 文案混在代码里，骨架检查完全看不见它。
  //
  // 真发生过：`completion.js` 里 `placeholder="例如 a2n-agent:latest"` 一直硬编码着，
  // 英文界面那个输入框就露中文 —— 而整套体检一路绿灯。
  // 所以这里补一条：web/ 下每个模块文件的**非注释字符串字面量**里不许有中文。
  console.log('\n== 各模块：不留展示文字（中文只允许出现在注释里） ==');
  {
    const WEB_DIR = path.dirname(FILE);
    const TABLE = 'i18n.js';
    // 单遍状态机：注释、单/双引号串、模板串、正则字面量都按语法状态走。
    //
    // 为什么不用「先正则去注释、再用正则抠字符串」：那套猜法在两种常见写法上会失配，
    // 一旦失配，引号配错对，**后面的中文就被当成别人的字符串内容跳过**。
    //   ① 正则字面量里带引号：`/[&<>"']/g`（business.js、experience.js、payment.js 都有）
    //   ② 字符串里带 `//`：`"see http://x // …"`
    // 实测（13 个模块）：旧写法在 4 个文件里对「文件末尾新加一条中文」完全看不见，
    // 而它照样报绿 —— 正是这条守卫要防的那种「检查绿着、东西坏着」。
    // 所以宁可多写四十行，让判断跟着语法走：认不出的写法会红，不会静默放过。
    const CJK = /[\u4e00-\u9fa5]/;
    // `/` 能开正则的位置：表达式开头，或前一个记号是运算符/分隔符。
    // 反例（除号）：标识符、`)`、`]`、数字、字符串收尾之后。
    const REGEX_OK = new Set(['', '(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}',
      ';', '+', '-', '*', '%', '<', '>', '~', '^']);
    const literals = src => {
      const out = [];
      let i = 0;
      const emit = buf => {
        const text = buf.replace(/\s+/g, ' ').trim();
        if (CJK.test(text)) out.push(text.slice(0, 60));
      };
      // 读一段代码，直到文件结束或（模板插值里）配平的 `}`。
      function code(stopAtBrace) {
        let depth = 0, prev = '';
        while (i < src.length) {
          const c = src[i], d = src[i + 1];
          if (c === '/' && d === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
          if (c === '/' && d === '*') {
            i += 2;
            while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
            i += 2; continue;
          }
          if (c === '"' || c === "'") { emit(str(c)); prev = 'v'; continue; }
          if (c === '`') { tpl(); prev = 'v'; continue; }
          if (c === '/' && REGEX_OK.has(prev)) { regex(); prev = 'v'; continue; }
          if (stopAtBrace && c === '{') depth++;
          if (stopAtBrace && c === '}') { if (depth === 0) { i++; return; } depth--; }
          if (!/\s/.test(c)) prev = c;
          i++;
        }
      }
      function str(q) {
        let buf = ''; i++;
        while (i < src.length) {
          const c = src[i];
          if (c === '\\') { buf += src[i + 1] || ''; i += 2; continue; }
          if (c === q) { i++; break; }
          buf += c; i++;
        }
        return buf;
      }
      // 模板串：`${…}` 里是代码（插值本身不是文案），插值之外的部分是文案。
      // 整条模板串算一处，插值两侧的文字拼起来再判。
      function tpl() {
        const parts = []; let buf = ''; i++;
        while (i < src.length) {
          const c = src[i];
          if (c === '\\') { buf += src[i + 1] || ''; i += 2; continue; }
          if (c === '`') { i++; break; }
          if (c === '$' && src[i + 1] === '{') { i += 2; parts.push(buf); buf = ''; code(true); continue; }
          buf += c; i++;
        }
        parts.push(buf);
        emit(parts.join(''));
      }
      function regex() {
        i++; let inClass = false;
        while (i < src.length) {
          const c = src[i];
          if (c === '\\') { i += 2; continue; }
          if (c === '\n') break;                      // 没配平的正则：当行结束，别吞掉整个文件
          if (c === '[') inClass = true;
          else if (c === ']') inClass = false;
          else if (c === '/' && !inClass) { i++; break; }
          i++;
        }
        while (i < src.length && /[a-z]/.test(src[i])) i++;   // 标志位
      }
      code(false);
      return out;
    };
    const offenders = [];
    for (const name of fs.readdirSync(WEB_DIR).filter(f => f.endsWith('.js'))) {
      if (name === TABLE) continue;   // 消息表本身当然全是中文
      for (const text of literals(fs.readFileSync(path.join(WEB_DIR, name), 'utf8'))) {
        offenders.push(`${name}: ${text}`);
      }
    }
    ok('各模块的字符串字面量里不残留中文（文案全走 I18N）', offenders.length === 0,
       offenders.slice(0, 5).join(' / '));
    // 守卫本身要会响：拿几个已知样例试一次，认得出才算这条检查活着。
    // 样例的口径与上面一致：模板串里插值之外的部分仍是文案（要报），
    // 插值本身是变量（不当文案）。
    ok('中文残留检查真的会响（拿一段样例文字试）',
       literals('const x = "这是一段中文";').length === 1
       && literals('const y = "plain ascii";').length === 0
       && literals('const z = `前缀 ${name} 后缀`;').length === 1
       && literals('const w = `${name}`;').length === 0);
    // 这两条是上一版的失明处，必须钉住 —— 它们是「正则猜字符串」的固有失配，
    // 不是边角料：`/[&<>"']/g` 这种转义函数在三个模块里都有。
    ok('中文残留检查不会被「正则里的引号」骗过去（引号配错对就会瞎掉）',
       literals('const s = v.replace(/[&<>"\']/g, c => c); const d = "中文";').length === 1
       && literals("const n = /'/g; const o = '中文';").length === 1);
    ok('中文残留检查不会被「字符串里的 //」当成注释剪掉',
       literals('const e = "see http://x // 中文";').length === 1
       && literals('const k = `a // 中文`;').length === 1);
  }

  console.log(`\n共 ${total} 项断言，${fails ? '失败 ' + fails + ' 项 ✗' : '全部通过 ✓'}`);
  process.exit(fails ? 1 : 0);
})();
