/* Read-only presentation checks for the explicitly delegated assistant dataset. */
const {chromium}=require('playwright-core'),assert=require('assert'),fs=require('fs');
(async()=>{
 const report=JSON.parse(fs.readFileSync('artifacts/project-agent-usage-2026-10-09.json','utf8'));
 assert(report.passed);
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage(),errors=[],checks=[];
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto('http://127.0.0.1:8771/console');
  await page.waitForFunction(()=>document.querySelectorAll('#qaPlan option').length>0);
  await page.evaluate(()=>{document.querySelector('[data-page="find"]').click();document.getElementById('taskQualityPanel').open=true});
  await page.selectOption('#qaPlan',report.cases[41].plan_id);
  await page.waitForFunction(()=>/已同意的实际记录 42 份/.test(document.getElementById('qaCalibrationStatus').textContent));
  check('console labels the selected opinion as delegated assistant judgment',/助手受托评审/.test(await page.locator('#qaReviewSummary').textContent()));
  const status=await page.locator('#qaCalibrationStatus').textContent();
  check('qualified records, suppliers and actual dates are visible',/实际记录 42 份.*3 个供应节点、1 天/s.test(status));
  check('personal opinions and assistant judgments remain distinguishable',/本人评价 0 份，助手代评 42 份/.test(status));
  check('ineligible calibration does not expose an enabled application button',await page.locator('#qaActivate').isDisabled()&&/使用默认参数/.test(status));
  check('installed console has no JavaScript runtime errors',errors.length===0);
  fs.writeFileSync('artifacts/delegated-use-console-2026-10-09.json',JSON.stringify({passed:true,checks,errors,source:'ASSISTANT_DELEGATED_USE'},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
