// Synthetic geometry and delayed fake gateway, never a real provider.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))calls++;});
  await page.setViewportSize({width:1600,height:1100});
  await page.goto(origin+'/incremental?empty=1');await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#datasetFile').setInputFiles('work/spatial-chat-fixture.json');
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('.spatial-plan-figure').length===1&&document.getElementById('chatState').textContent==='单次预测已完成');
  const sid=await page.locator('#sessionId').inputValue(),state=await(await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  const plan=state.rounds[0].spatial_plan;
  assert(plan.status==='spatial_prediction_candidates'&&plan.branches.length>=2,'spatial forecast or mirrors missing');
  assert(await page.locator('.spatial-plan-figure polyline[data-spatial-kind="prediction"]').count()>=2,'combined forecast lines missing');
  assert((await page.locator('.spatial-plan-figure').textContent()).includes('综合轨迹图'),'combined caption missing');
  assert(await page.locator('#executionNodes .completed').count()===3,'three core stages did not execute');
  assert(await page.locator('#executionSupportNodes .completed').count()===4,'validation and commit stages missing');
  assert(!await page.locator('.platform-motion-figure').isVisible(),'platform still displayed as independent main chart');
  assert(!await page.locator('.chat-prediction-figure').isVisible(),'bearing basis not folded below combined chart');
  await page.locator('.platform-analysis-details').evaluate(el=>el.open=true);assert(await page.locator('.platform-motion-fields').isVisible(),'platform reasoning details unavailable');
  await page.locator('.platform-analysis-details').evaluate(el=>el.open=false);
  const first=await page.locator('.spatial-plan-figure').innerHTML();await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.spatial-plan-figure').length===1);
  assert(await page.locator('.spatial-plan-figure').innerHTML()===first,'saved spatial plan changed after refresh');
  assert(calls===1,'more than one model call for three-stage pipeline');
  await page.locator('#chatMessages').evaluate(el=>{el.style.scrollBehavior='auto';const f=el.querySelector('.spatial-plan-figure');el.scrollTop+=f.getBoundingClientRect().top-el.getBoundingClientRect().top;});
  await page.screenshot({path:'work/fused-trajectory-chat.png'});
  return {coreStages:3,oneModelCall:true,combinedPlatformAndTarget:true,mirrorBranches:plan.branches.length,bearingBasisRetained:true,refreshRestored:true,realModelCalls:0};
}
