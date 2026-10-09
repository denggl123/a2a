/* Real browser against the Windows node in selection_acceptance.py. */
const {chromium}=require('playwright-core'),assert=require('assert'),fs=require('fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[],checks=[];
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.addInitScript(id=>localStorage.setItem('a2n.coord.search',id),process.argv[3]);
  await page.goto(process.argv[2]+'/console');
  await page.waitForFunction(()=>window.a2nSelection&&document.getElementById('selectionWeights').children.length===8);
  await page.evaluate(()=>document.querySelector('[data-page="find"]')?.click());
  await page.waitForFunction(()=>document.getElementById('selectionStatus').textContent.includes('本次适合度推荐'));
  check('eight preference dimensions load from protected local profile',await page.locator('[data-selection-weight]').count()===8);
  check('quality and credit show local supportedness',/交付质量.*信用/.test(await page.locator('#results').textContent()));
  await page.selectOption('#discSort','credit');
  await page.click('[data-selection-explain]');await page.waitForFunction(()=>document.querySelector('#detail[open]'));
  check('explanation uses real frozen snapshot',/适合度.*资料时间/s.test(await page.locator('#detailBody').textContent()));
  await page.evaluate(()=>document.getElementById('detail').close());
  await page.locator('#selectionWeights').locator('..').locator('summary').click();
  await page.fill('#selectionMinQuality','99');await page.check('#selectionSupported');
  await page.click('#selectionSave');await page.waitForFunction(()=>document.getElementById('selectionStatus').textContent.includes('1 个待确认'));
  check('unknown or limited history is pending rather than falsely qualified',/待确认/.test(await page.locator('#results').textContent()));
  await page.fill('#selectionMinQuality','');await page.uncheck('#selectionSupported');await page.click('#selectionSave');
  await page.waitForFunction(()=>!document.getElementById('selectionSave').disabled);
  await page.selectOption('#experienceRole','provider');await page.waitForTimeout(300);
  await page.click('#experienceRebuild');await page.waitForFunction(()=>document.getElementById('experienceResult').textContent.includes('本机信用'));
  check('provider credit view explains dispute counts without guilt assertion',/争议发生不代表/.test(await page.locator('#experienceResult').textContent()));
  await page.selectOption('#experienceRole','buyer');await page.waitForTimeout(300);
  check('buyer view lists real trade participants',await page.locator('#experienceTarget option').count()>0);
  if(errors.length)console.error('Browser errors: '+JSON.stringify(errors));
  check('no browser runtime errors',errors.length===0);
  await page.locator('#selectionStatus').scrollIntoViewIfNeeded();
  await page.screenshot({path:'artifacts/selection-console-2026-10-08.png'});
  fs.writeFileSync('artifacts/selection-console-2026-10-08.json',JSON.stringify({passed:true,checks,errors},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
