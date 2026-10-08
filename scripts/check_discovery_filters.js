/* UI-only verification with real published cards. Never executes or pays an Agent.
   A2N_NODE_BASE defaults to an isolated preview; LIVE_DISCOVERY=1 uses its real search.
   NODE_PATH must contain playwright-core. */
const {chromium}=require('playwright-core'),fs=require('fs'),http=require('http'),assert=require('assert'),path=require('path');
const BASE=process.env.A2N_NODE_BASE||'http://127.0.0.1:18773';
const OUT=process.env.OUT||'artifacts/discovery-filter-ui';
const live=process.env.LIVE_DISCOVERY==='1';
const get=url=>new Promise((resolve,reject)=>http.get(url,r=>{let body='';r.setEncoding('utf8');r.on('data',c=>body+=c);r.on('end',()=>{try{assert.equal(r.statusCode,200);resolve(JSON.parse(body))}catch(e){reject(e)}})}).on('error',reject));
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],checks=[],actions=[];
 const check=(name,passed)=>{assert(passed,name);checks.push(name);console.log('PASS '+name)};
 let tracking=false;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(tracking&&/\/v1\/(coord|discovery|points|trades|payment|projections)|\/a2a\//.test(r.url()))actions.push({method:r.method(),url:r.url()})});
 try{
  await page.goto(BASE+'/console');await page.waitForFunction(()=>window.A2NDiscovery&&snapshot.node_did);
  if(live){
   await page.fill('#skill','inspect');await page.click('#searchform button');
   await page.waitForFunction(()=>pool.length>0&&coordSession&&coordSession.state!=='RUNNING');
   // The real UI stops at its satisfaction target. Explicitly continue discovery
   // to obtain mixed service policies; filtering itself must never resume it.
   for(let round=0;round<12;round++){
    const state=await page.evaluate(()=>({round:coordSession.round,state:coordSession.state,
      mixed:pool.some(i=>i.card['x-a2n']?.points?.enabled===true)&&pool.some(i=>!i.card['x-a2n']?.points?.enabled),
      stages:pool.some(i=>i.card['x-a2n']?.trial?.ended===true)&&pool.some(i=>i.card['x-a2n']?.trial?.ended===false)}));
    if(state.mixed&&state.stages||state.state==='FRONTIER_EXHAUSTED')break;
    await page.click('#coordContinue');
    await page.waitForFunction(round=>coordSession.round>round&&coordSession.state!=='RUNNING',state.round);
   }
  }else{
   const dirs=await Promise.all([18881,18882,18883].map(p=>get(`http://127.0.0.1:${p}/public/v1/agents?skill=inspect&limit=100`)));
   const items=dirs.flatMap(d=>d.cards.map(card=>({card,key:{provider_did:d.node_did,service_id:card['x-a2n'].projection.service_id},source:'public-node',headers:{}})));
   await page.evaluate(items=>{resetDisc();pool=items;applyDisc()},items);
  }
  const before=await page.evaluate(()=>JSON.stringify(pool));
  const facts=await page.evaluate(()=>({total:pool.length,
    points:pool.filter(i=>i.card['x-a2n']?.points?.enabled===true&&i.card['x-a2n'].points.modes.length).length,
    initial:pool.filter(i=>i.card['x-a2n']?.trial?.ended===false).length,
    graduated:pool.filter(i=>i.card['x-a2n']?.trial?.ended===true).length,
    did:pool.find(i=>i.card['x-a2n']?.points?.enabled===true).key.provider_did}));
  check('真实节点卡覆盖积分供给与未声明积分供给',facts.points>0&&facts.points<facts.total);
  check('真实节点卡覆盖免费样品阶段与已完成阶段',facts.initial>0&&facts.graduated>0);
  const count=()=>page.evaluate(()=>found.length);
  const facet=(k,v)=>page.locator(`[data-facet="${k}"][data-val="${v}"]`);
  const clear=()=>page.click('#discReset');
  tracking=true;
  await facet('pay','points').click();check('积分筛选命中数与真实服务声明一致',await count()===facts.points);
  check('界面显示原始候选数与当前命中数',(await page.textContent('#resmeta')).includes(`本次到手 ${facts.total} 个候选`));
  await facet('pointMode','DEBT').click();
  check('积分模式筛选只命中支持欠账的卡',await page.evaluate(()=>found.every(i=>i.card['x-a2n'].points.modes.includes('DEBT'))));
  await facet('pointMode','PAY').focus();await page.keyboard.press('Enter');
  check('键盘操作可多选积分模式',await page.evaluate(()=>disc.pointMode.includes('DEBT')&&disc.pointMode.includes('PAY')));
  await page.locator('[data-clear="pointMode"][data-value="DEBT"]').click();
  check('移除单个条件保留同组其他选择',await page.evaluate(()=>disc.pointMode.length===1&&disc.pointMode[0]==='PAY'));
  await clear();await facet('pay','native').click();
  check('未配置原生币供给时如实显示零命中',await count()===0);
  check('零命中保留已选条件并解释候选仍在',(await page.textContent('#results')).includes('候选仍在')&&await facet('pay','native').getAttribute('aria-pressed')==='true');
  await page.locator('[data-clear="pay"][data-value="native"]').click();check('移除零命中条件恢复全部候选',await count()===facts.total);
  await facet('pay','points').click();await facet('pay','__undeclared__').click();
  check('同组支付多选可同时展示积分与未声明供给',await count()===facts.total);
  await clear();await facet('sample','INITIAL_FREE').click();check('按样品阶段筛选',await count()===facts.initial);
  await page.click('#facets details summary');await facet('provider',facts.did).click();
  check('供给方节点条件与样品阶段组合生效',await page.evaluate(did=>found.length>0&&found.every(i=>i.key.provider_did===did&&i.card['x-a2n'].trial.ended===false),facts.did));
  check('完整供给方身份可在已选条件中查看',(await page.textContent('#resmeta')).includes(facts.did));
  check('更多条件覆盖格式、币种、标签、版本、来源与验签',await page.locator('#facets .facet').count()===17);
  await clear();await facet('pay','points').click();await page.click('#vwList');
  check('列表明确展示支付与积分条件',(await page.textContent('#results')).includes('支付与积分条件')&&(await page.textContent('#results')).includes('支持积分'));
  await page.click('#vwCompare');check('对比视图同时展示支付与历史证据',(await page.textContent('#results')).includes('支付与积分条件')&&(await page.textContent('#results')).includes('历史信誉'));
  await page.click('#vwCards');
  await page.screenshot({path:path.join(OUT,'filters-desktop.png'),fullPage:true});
  await facet('provider',facts.did).click();
  await page.setViewportSize({width:390,height:800});
  check('手机宽度下长身份与支付说明不撑破页面',await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2));
  await page.screenshot({path:path.join(OUT,'filters-mobile.png'),fullPage:true});
  await clear();
  check('筛选和视图切换未改写任何原始签名商品卡',before===await page.evaluate(()=>JSON.stringify(pool)));
  check('筛选操作未发起发现、积分、询价、调用或支付请求',actions.length===0);
  check('页面没有运行错误',errors.length===0);
  fs.writeFileSync(path.join(OUT,'result.json'),JSON.stringify({passed:true,base:BASE,live_discovery:live,facts,checks,filter_network_requests:actions,errors},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
