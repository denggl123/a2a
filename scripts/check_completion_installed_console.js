/* Existing installed Windows SDK calls the newly published real Docker Agent. */
const {chromium}=require('playwright-core'),fs=require('fs'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],checks=[];
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto('http://127.0.0.1:8771/console');await page.waitForFunction(()=>window.A2NTradeUI&&snapshot);
  const before=await page.evaluate(()=>request('/v1/points'));
  const count=async()=>(await(await page.request.get('http://127.0.0.1:18910/counts')).json()).counts.successful_inspections;
  const start=await count();
  await page.evaluate(()=>window.A2NTradeUI.open('guided-use-node-a'));
  check('installed SDK loads supplier-declared text field',await page.locator('[data-input-field="text"]').count()===1);
  await page.fill('[data-input-field="text"]','安装后的真实界面验收，可公开。\n\nUseful services connect agents.');
  await page.click('#paymentQuote');await page.waitForFunction(()=>!document.getElementById('paymentFree').disabled);
  check('real Docker supplier returns free initial-sample terms',/前 10 次免费/.test(await page.textContent('#paymentOffer')));
  for(let i=0;i<2;i++){
   const confirm=page.waitForEvent('dialog').then(async d=>{assert(/一定公开.*样品/.test(d.message()));await d.accept()});
   await Promise.all([confirm,page.click('#paymentFree')]);
   await page.waitForFunction(()=>!document.getElementById('paymentFree').disabled&&document.getElementById('paymentCallResult').textContent.includes('服务已交付'));
  }
  check('actual Agent executes once and original UI retry is idempotent',await count()===start+1);
  const after=await page.evaluate(()=>request('/v1/points'));
  check('public sample bypasses every points settlement',after.orders.length===before.orders.length);
  check('installed result is readable',/字符数/.test(await page.textContent('#paymentCallResult'))&&/摘要/.test(await page.textContent('#paymentCallResult')));
  const samples=await(await page.request.get('http://127.0.0.1:18881/public/v1/samples?service_id=text-inspector-guided')).json();
  check('supplier publishes actual sample',samples.count===2);
  await page.locator('#paymentCallResult').screenshot({path:'artifacts/completion-ui/installed-agent-result.png'});
  check('installed console has no runtime errors',errors.length===0);
  fs.writeFileSync('artifacts/completion-installed-ui.json',JSON.stringify({passed:true,checks,actual_executions:1,original_retry:1},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
