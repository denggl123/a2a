/* Browser acceptance against the installed Windows bundle; controlled records only. */
const {chromium}=require('playwright-core'),assert=require('assert'),fs=require('fs');
(async()=>{
 const accepted=JSON.parse(fs.readFileSync('artifacts/task-quality-installed-2026-10-09.json','utf8'));
 assert(accepted.passed,'installed-node acceptance must pass first');
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),checks=[],errors=[];
 let restoreReview=null;
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.addInitScript(()=>localStorage.removeItem('a2n.coord.search'));
  await page.goto('http://127.0.0.1:8771/console');
  const rubricRefs=await page.evaluate(async()=>(await request('/v1/task-quality/rubrics')).items.map(r=>r.reference));
  await page.waitForFunction(refs=>window.a2nTaskReview&&document.querySelectorAll('#qaRubric option').length===refs.length,rubricRefs);
  await page.evaluate(()=>{document.querySelector('[data-page="find"]').click();document.getElementById('taskQualityPanel').open=true});
  const displayed=await page.locator('#qaRubric option').evaluateAll(options=>options.map(o=>o.value));
  check('installed console exposes current task rubrics, media types and optional general quality',
   JSON.stringify(displayed)===JSON.stringify(rubricRefs)&&['image-output@1','audio-output@1','video-output@1','document-output@1'].every(ref=>displayed.includes(ref))&&await page.locator('#selectionRubric option').count()===rubricRefs.length+1);
  await page.selectOption('#qaPlan',accepted.plans.desktop);
  await page.waitForFunction(()=>document.querySelectorAll('[data-qa-score]').length===3);
  const original=await page.evaluate(async id=>request('/v1/task-quality/plans/'+id),accepted.plans.desktop);
  assert(original.plan.origin==='CONTROLLED','review mutation is only allowed on the controlled acceptance fixture');
  restoreReview={id:accepted.plans.desktop,review:original.review};
  const preference=await page.evaluate(async()=>JSON.stringify(await request('/v1/selection/profiles/balanced')));
  check('task detail separates objective checks from three personal judgments',await page.locator('[data-qa-score]').count()===3&&original.review.objective.accuracy.value===1);
  await page.selectOption('[data-qa-score="usefulness"]','5');await page.check('#qaConsent');
  await page.waitForTimeout(5600);
  check('periodic refresh preserves unsaved ratings and consent',await page.locator('[data-qa-score="usefulness"]').inputValue()==='5'&&await page.locator('#qaConsent').isChecked());
  await page.click('#qaSaveReview');
  await page.waitForFunction(()=>document.getElementById('qaMessage').textContent.includes('本人细项体验已保存'));
  const changed=await page.evaluate(async id=>request('/v1/task-quality/plans/'+id),accepted.plans.desktop);
  check('browser saves a new review revision without altering objective evidence',changed.review.revision===original.review.revision+1&&changed.review.human.usefulness===5&&JSON.stringify(changed.review.objective)===JSON.stringify(original.review.objective));
  await page.click('#qaFit');
  await page.waitForFunction(()=>document.getElementById('qaMessage').textContent.includes('真实使用记录不足'));
  check('controlled calls never unlock calibration activation',await page.locator('#qaActivate').isDisabled()&&/合格记录 0 笔/.test(await page.locator('#qaMessage').textContent()));
  await page.waitForTimeout(5300);
  check('calibration status retains the real-data qualification message',/已同意的实际记录 0 份/.test(await page.locator('#qaCalibrationStatus').textContent())&&await page.locator('#qaActivate').isDisabled());
  await page.selectOption('#qaRubric','structured-output@1');
  await page.selectOption('#qaOrigin','CONTROLLED');await page.check('#qaEnable');
  await page.evaluate(()=>document.getElementById('qaPath').closest('details').open=true);
  await page.fill('#qaPath','characters');await page.selectOption('#qaOperation','equals');await page.selectOption('#qaValueKind','number');await page.fill('#qaExpected','7');await page.click('#qaAddCheck');
  const browserTask='browser-quality-'+Date.now();
  await page.evaluate(async ({scope,task})=>window.a2nTaskReview.prepare(scope,{task_id:task,skill:'inspect',payload:{text:'example'}}),{scope:accepted.projection_id,task:browserTask});
  const plans=await page.evaluate(async()=>request('/v1/task-quality/plans'));
  const pending=plans.items.find(p=>p.plan.task_id===browserTask);
  check('call-page composition fixes check rules before any execution',pending.plan.planned&&pending.plan.origin==='CONTROLLED'&&pending.review===null&&pending.plan.checks.accuracy?.[0]?.expected===7);
  await page.click('#qaCancelPlan');
  await page.waitForFunction(id=>document.querySelector('#qaPlan option[value="'+id+'"]').textContent.includes('已取消'),pending.plan.plan_id);
  check('an unused review plan can be cancelled through the console',true);
  await page.evaluate(async ({id,review,revision})=>request('/v1/task-quality/plans/'+id+'/review',{scores:review.human,consent:review.consent,withdrawn:review.withdrawn,label_source:review.label_source||'HUMAN'},{'If-Match':'"'+revision+'"'}),{id:accepted.plans.desktop,review:original.review,revision:changed.review.revision});
  restoreReview=null;
  const preferenceAfter=await page.evaluate(async()=>JSON.stringify(await request('/v1/selection/profiles/balanced')));
  check('task review controls preserve existing personal ranking preferences',preference===preferenceAfter);
  check('installed console has no JavaScript runtime errors',errors.length===0);
  fs.writeFileSync('artifacts/task-quality-console-2026-10-09.json',JSON.stringify({passed:true,environment:'INSTALLED_WINDOWS_BUNDLE',origin:'CONTROLLED',checks,errors},null,2));
 }finally{
  try{if(restoreReview)await page.evaluate(async({id,review})=>{const current=await request('/v1/task-quality/plans/'+id);if(JSON.stringify(current.review.human)!==JSON.stringify(review.human)||current.review.consent!==review.consent||current.review.withdrawn!==review.withdrawn||current.review.label_source!==review.label_source)await request('/v1/task-quality/plans/'+id+'/review',{scores:review.human,consent:review.consent,withdrawn:review.withdrawn,label_source:review.label_source||'HUMAN'},{'If-Match':'"'+current.review.revision+'"'})},restoreReview)}
  finally{await browser.close()}
 }
})().catch(e=>{console.error(e.stack);process.exitCode=1});
