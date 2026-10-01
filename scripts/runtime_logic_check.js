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
  statusLabel, unknownState, openDisputeFor,
  trialOf, trialBlock, sampleBlock, projTrial, fmtTime,
  evidenceOf, samplesUrlOf, renderSamples,
  fbMine, fbTheirs, fbDims, fbScoreText, fbBadge, feedbackTable,
  FB_DIMS, FB_DELIVERED, FB_DIR_LABEL };
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
const pj = fns.projTrial({ trial: { cap: 10, ended: false, notice: '前 10 次完成调用免费，其交付内容默认形成公开样品（可公开投影，隐去密钥与隐私）。' } });
ok('试用中明说「免费试用期」与「默认形成公开样品」',
   /免费试用期/.test(pj) && /默认形成公开样品/.test(pj), pj.slice(0, 120));
ok('试用结束的供给明说「已结束」',
   /已结束/.test(fns.projTrial({ trial: { cap: 10, ended: true, notice: 'x' } })));
ok('试调用前会先弹确认（不是点了就发）',
   /confirm\(warn/.test(HTML) && /继续这次试调用/.test(HTML));
ok('确认文案含「默认形成公开样品」', /本次交付内容默认形成公开样品/.test(HTML));
ok('样例入口取自卖方公开面 URL（快照样本 samples_url）', /samples_url/.test(HTML) && /data-samples/.test(HTML));
ok('推不出样例入口时如实说「入口未知」，不编地址', /样例入口未知/.test(HTML));

console.log('\n== 找 Agent：浏览进度 + 用词统一（待使用，不用旧词「收藏」） ==');
ok('「按已知能力清单浏览」给逐条进度，不再是静态一句',
   /已回 \$\{_done\}\/\$\{ids\.length\}/.test(HTML));
ok('浏览点下去先报 0/N（不是空白等待）', /已回 0\/\$\{ids\.length\}/.test(HTML));
ok('浏览结果明说「已知能力清单，不是全网目录」',
   /已知能力清单，不是全网目录/.test(HTML));
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

console.log('\n== 双方反馈（R2）：只记录事实，不算分、不阻塞（FEEDBACK-RULES v0.1） ==');
// 方向与维度白名单：买方评供给 3 维、供给评买方 2 维。
ok('买方 → 供给：交付质量 / 按时 / 沟通',
   fns.FB_DIMS.buyer_to_seller.map(d => d[0]).join() === 'quality,punctual,communication');
ok('供给 → 买方：需求按约 / 沟通配合',
   fns.FB_DIMS.seller_to_buyer.map(d => d[0]).join() === 'on_spec,cooperative');
// 状态硬闸：只有已交付才认交付质量（ACCEPTED=已验收 也算交付）。
ok('交付质量只对 COMPLETED/ACCEPTED/SETTLED 开放',
   fns.FB_DELIVERED.has('COMPLETED') && fns.FB_DELIVERED.has('ACCEPTED') &&
   fns.FB_DELIVERED.has('SETTLED') &&
   !fns.FB_DELIVERED.has('FAILED') && !fns.FB_DELIVERED.has('CANCELED') &&
   !fns.FB_DELIVERED.has('DELIVERY_UNKNOWN'));
ok('前端表单会对未交付任务禁用「交付质量」维度（硬闸在服务端，前端只是不误导）',
   /k==='quality'&&!delivered/.test(HTML) && /未交付 · 不可评/.test(HTML));
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
ok('清单表写明「R2 不算分」', /R2 不算分/.test(ft));
ok('R2 没有「评分 / 综合分」这类聚合列（只列维度分事实）',
   !/<th>(评分|综合分|总分)<\/th>/.test(ft));
// 未评价不阻塞、不算差评。
ok('调用记录里未评价如实写「未评价」，不逼人评',
   /未评价 <button/.test(HTML) && /写反馈/.test(HTML));
ok('账户页空态写明「未评价不代表差评，也不妨碍任何事」',
   /未评价不代表差评，也不妨碍任何事/.test(HTML));
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
   /未送达：/.test(HTML) && /仍存着这一份/.test(HTML));
ok('供给方反馈如实说「R2 不主动推送，等对方重捎回执」',
   /R2 不主动推送/.test(HTML));
// 只读接口（F3 补）：控制台真的去读 GET /v1/feedback/{id}/versions 与 summary（不是摆设）。
ok('控制台「版本」按钮读 GET /v1/feedback/{id}/versions',
   /data-fb-versions=/.test(HTML) && /\/versions'/.test(HTML));
ok('控制台「摘要」按钮读 GET /v1/feedback/summary',
   /data-fb-summary/.test(HTML) && /'\/v1\/feedback\/summary'/.test(HTML));
ok('摘要弹窗写明「不是信誉分」（只列事实计数与各维平均）',
   /不是信誉分/.test(HTML));

console.log(`\n共 ${total} 项断言，${fails ? '失败 ' + fails + ' 项 ✗' : '全部通过 ✓'}`);
process.exit(fails ? 1 : 0);
