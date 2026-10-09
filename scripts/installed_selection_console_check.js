/* Installed Chrome checks: bounded discovery only, no Agent calls or profile writes. */
const {chromium}=require('playwright-core'),assert=require('assert'),fs=require('fs');
(async()=>{
 const acceptance=JSON.parse(fs.readFileSync('artifacts/selection-installed-2026-10-09.json','utf8'));
 assert(acceptance.passed,'installed-node acceptance must pass first');
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[],checks=[],remoteRefresh=[];
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/v1/selection/refresh'))remoteRefresh.push(r.url())});
 try{
  await page.addInitScript(()=>localStorage.removeItem('a2n.coord.search'));
  await page.goto('http://127.0.0.1:8771/console');
  await page.waitForFunction(()=>window.a2nSelection&&document.getElementById('selectionWeights').children.length===8);
  await page.evaluate(()=>document.querySelector('[data-page="find"]')?.click());
  // Old acceptance search IDs can be expired or pruned. Start a bounded current
  // discovery round without mounting a replacement service or invoking one.
  const session=await page.evaluate(async()=>{
   let row=await request('/v1/coord/searches',{skill:'inspect',preferences:{min_candidates:1},
    round_budget:{remote_operations:32,received_bytes:2097152,duration_ms:8000,introduction_depth:4,candidate_limit:64,probe_operations:1,max_concurrency:2}},
    {'Idempotency-Key':'console-selection-'+crypto.randomUUID()});
   for(let i=0;i<100&&row.state==='RUNNING';i++){await new Promise(r=>setTimeout(r,100));row=await request('/v1/coord/searches/'+row.search_id)}
   if(row.state==='RUNNING')throw Error('当前发现未在验收预算内结束');
   coordSession=row;++coordWatch;rememberCoordSession(row.search_id);
   await loadCoordSession(row.search_id);return {search_id:row.search_id,state:row.state};
  });
  await page.waitForFunction(()=>document.getElementById('selectionStatus').textContent.includes('本次适合度推荐'));
  check('installed bundle serves all eight personal preference controls',await page.locator('[data-selection-weight]').count()===8);
  check('candidate cards explain quality and credit support',/交付质量.*信用/.test(await page.locator('#results').textContent()));
  const initial=await page.evaluate(async()=>JSON.stringify(await (await fetch('/v1/selection/profiles/balanced')).json()));
  await page.selectOption('#discSort','credit');
  const button=page.locator('[data-selection-explain]').first();await button.click();
  await page.waitForFunction(()=>document.querySelector('#detail[open]'));
  check('frozen recommendation explanation opens in installed bundle',/适合度.*资料时间/s.test(await page.locator('#detailBody').textContent()));
  check('explanation exposes support and units without asserting dispute guilt',/原始单位、支持量.*争议数量单独展示/s.test(await page.locator('#detailExtra').textContent()));
  await page.evaluate(()=>document.getElementById('detail').close());
  const after=await page.evaluate(async()=>JSON.stringify(await (await fetch('/v1/selection/profiles/balanced')).json()));
  check('browsing preserves default personal preferences',initial===after);
  check('automatic recommendation never starts a remote metadata query',remoteRefresh.length===0);
  check('installed console has no JavaScript runtime errors',errors.length===0);
  fs.writeFileSync('artifacts/selection-installed-console-2026-10-09.json',JSON.stringify({passed:true,environment:'INSTALLED_WINDOWS_BUNDLE',checks,errors,session},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
