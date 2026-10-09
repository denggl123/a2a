/* Installed-bundle UI acceptance. Controlled calls, no external network. */
const {chromium}=require('playwright-core'),assert=require('assert'),fs=require('fs');
(async()=>{
 const report=JSON.parse(fs.readFileSync('artifacts/vision-completion-installed-2026-10-09.json','utf8'));
 assert(report.passed,'installed services must pass first');
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[],checks=[];
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto('http://127.0.0.1:8771/console');
  await page.waitForFunction(()=>window.a2nSelection&&document.getElementById('qaIndependentKind')&&document.getElementById('testWalletState').textContent);
  check('natural language and custom task controls are present',await page.locator('#selectionDemand').count()===1&&await page.locator('#selectionTaskClass').count()===1);
  await page.evaluate(()=>document.getElementById('selectionDemand').value='提取中文文档，输出 JSON');
  await page.evaluate(()=>document.getElementById('selectionInterpret').click());
  await page.waitForFunction(()=>document.getElementById('selectionDemandHints').textContent.includes('建议能力'));
  check('demand is interpreted into private conditions',/建议能力/.test(await page.locator('#selectionDemandHints').textContent()));
  await page.evaluate(()=>document.querySelector('[data-page="account"]').click());
  check('test wallet visibly distinguishes local test currency',/测试/.test(await page.locator('#testWalletState').textContent()));
  check('maintenance and workflows expose owner controls',await page.locator('#maintenanceSave').count()===1&&await page.locator('#workflowRetry').count()===1);
  check('installed useful workflows are shown',/wf_/.test(await page.locator('#workflowState').textContent()));
  const ref=report.calls['vision-image-card-'+report.run_id+'-node-b'];
  const doc=report.calls['vision-document-render-'+report.run_id+'-desktop'];
  const detail=await page.evaluate(call=>request('/v1/calls/detail?scope='+call.scope+'&task_id='+call.task_id),doc);
  await page.evaluate(detail=>{
   const host=document.createElement('section');host.id='acceptancePreview';document.getElementById('account').append(host);
   A2NForms.renderOutcome(host,{ok:true,result:{assets:detail.assets},metadata:detail.metadata});
  },detail);
  const fetchSeen=page.waitForRequest(r=>r.url().endsWith('/v1/assets/fetch')&&r.method()==='POST');
  await page.locator('#acceptancePreview button').filter({hasText:'查看成果'}).click();
  const fetchBody=(await fetchSeen).postDataJSON();
  await page.waitForFunction(()=>document.querySelectorAll('#acceptancePreview pre').length>=2);
  check('rendered delivery button fetches remote asset using the real trade',fetchBody.trade_uid===detail.metadata.trade_uid&&fetchBody.asset_id===detail.assets[0].asset_id&&!await page.locator('#acceptancePreview').getByText('成果暂未读取',{exact:false}).count());
  const quota=await page.evaluate(async()=>{const r=await request('/v1/runtime');return r.maintenance.asset_limits.file_bytes});
  check('upload displays the owner configured file capacity',(await page.locator('#assetUploadLimit').textContent()).includes(String(quota/1048576)+' MiB'));
  await page.locator('#assetFile').setInputFiles({name:'controlled-upload.txt',mimeType:'text/plain',buffer:Buffer.from('Controlled private upload through the installed console.')});
  await page.locator('#assetUpload').click();
  await page.waitForFunction(()=>document.getElementById('assetReference').textContent.includes('a2n-asset/1'));
  check('configured private file upload works through the rendered controls',JSON.parse(await page.locator('#assetReference').textContent()).assets[0].size>0);
  if(fs.existsSync('.tmp/release-probe/A2N.exe.release.json')){
   const manifest=JSON.parse(fs.readFileSync('.tmp/release-probe/A2N.exe.release.json','utf8'));
   const release=await page.evaluate(body=>request('/v1/upgrades/preview',body),{manifest,path:require('path').resolve('.tmp/release-probe/A2N.exe')});
   check('signed large release sequence survives browser JSON and signature verification',typeof manifest.release_sequence==='string'&&release.verified===true);
  }
  const proof=await page.evaluate(async(call)=>{
   const detail=await request('/v1/calls/detail?scope='+call.scope+'&task_id='+call.task_id);
   const original=detail.assets[0],asset=await request('/v1/assets/fetch',{trade_uid:detail.metadata.trade_uid,asset_id:original.asset_id});
   const response=await fetch('/v1/assets/'+asset.asset_id+'/preview',{headers:{Range:'bytes=0-31'}});
   return {status:response.status,bytes:(await response.arrayBuffer()).byteLength,csp:response.headers.get('Content-Security-Policy')};
  },doc);
  check('private document preview works with ranges and sandbox policy',proof.status===206&&proof.bytes===32&&proof.csp.includes('sandbox'));
  await page.evaluate(()=>document.querySelector('[data-page="find"]').click());
  await page.evaluate(()=>document.getElementById('taskQualityPanel').open=true);
  check('professional review types are selectable',await page.locator('#qaIndependentKind option').count()===3);
  check('media rubrics are available',await page.locator('#qaRubric option').count()>=7);
  const streams=await page.evaluate(async report=>{
   const card=(await request('/v1/runtime')).projections.find(p=>p.projection_id==='vision-csv-profile-'+report.run_id);
   const task='console-stream-'+report.run_id+'-'+crypto.randomUUID();let partials=0;const observer=()=>partials++;
   document.addEventListener('a2n:progress',observer);
   try{
    const quote=await request('/v1/payment-coordination/quote',{projection_id:'vision-csv-profile-'+report.run_id,request:{task_id:task,skill:'csv-profile',payload:{csv:'name,amount\na,2\nb,3'}}});
    const index=quote.options.findIndex(p=>p.currency==='TETH');
    const row=await request('/v1/payment-coordination/prepare',{offer_id:quote.offer_id,option_index:index,fee_cap_minor:'1000000000000000',command_id:task});
    const id=row.record.plan_id;
    await request('/v1/payment-coordination/plans/'+id+'/pay',{});
    for(let i=0;i<20;i++){const r=await request('/v1/payment-coordination/plans/'+id+'/reconcile',{});if(r.state==='CONFIRMED')break;await new Promise(r=>setTimeout(r,100))}
    const outcome=await request('/v1/payment-coordination/plans/'+id+'/execute',{},{Accept:'text/event-stream'});
    return {partials,ok:outcome.ok,rows:outcome.result.rows,first:outcome.metadata.local_stream_observation};
   }finally{document.removeEventListener('a2n:progress',observer)}
  },report);
  check('paid stream renders actual partial output and final result',streams.ok&&streams.rows===2&&streams.partials>0&&streams.first.events>0);
  check('console has no JavaScript errors',errors.length===0);
  await page.screenshot({path:'artifacts/vision-completion-console-2026-10-09.png',fullPage:true});
  fs.writeFileSync('artifacts/vision-completion-console-2026-10-09.json',JSON.stringify({passed:true,checks,errors,streams},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
