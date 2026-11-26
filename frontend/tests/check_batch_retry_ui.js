// Deterministic provider failures for UI control paths; never a real model call.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const data=await(await page.request.get(base+'/api/test-sequential-spatial-dataset')).json();
  await page.setViewportSize({width:1750,height:1100});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  async function setup(question,retries=true){
    await page.locator('#pointFileImport').setInputFiles({name:question+'.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
    await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('10个原始观测'));
    await page.locator('#samplePointCount').fill('2');await page.locator('#samplePoints').click();
    if(!retries)await page.locator('#sequenceRetryEnabled').uncheck();
    else{await page.locator('#sequenceRetryBudget').fill('2');await page.locator('#sequenceRetryBudget').dispatchEvent('change');}
    await page.locator('#chatQuestion').fill(question);await page.locator('#chatQuestion').dispatchEvent('input');
    await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备2组'));
    await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();
  }
  async function get(){const sid=await page.locator('#sessionId').inputValue(),url=base+'/api/incremental/mixed-sessions/'+sid;const root=await(await page.request.get(url)).json();return {url,root,run:await(await page.request.get(url+'/runs/'+root.active_sequential_run)).json()};}
  async function finish(){await page.evaluate(()=>{window.confirm=()=>true;});await page.locator('#sequenceReject').click();await page.waitForFunction(()=>document.querySelector('#sequenceReject').hidden);await page.locator('#openNewConversation').click();}
  await setup('TEST_RETRY_BAD_WORKER');await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:30000});
  let result=await get(),attempts=result.run.steps[0].attempts;
  assert(result.run.calls===4&&result.run.retry_calls===1,'worker retry accounting wrong');
  assert(attempts.filter(a=>a.kind==='worker'&&a.unit_id==='U000001').length===2&&attempts.filter(a=>a.kind==='worker'&&a.unit_id==='U000002').length===1,'reissued successful worker');
  assert((await page.locator('#sequenceRetryStatus').textContent()).includes('已用1/2'),'retry usage not shown');
  await finish();
  await setup('TEST_RETRY_BAD_MERGE');await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:30000});
  result=await get();attempts=result.run.steps[0].attempts;
  assert(result.run.calls===4&&attempts.filter(a=>a.kind==='merge').length===2&&attempts.filter(a=>a.kind==='worker').length===2,'merge retried workers');
  await finish();
  await setup('TEST_RETRY_BAD_MERGE',false);await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('（failed）'),{},{timeout:30000});
  result=await get();assert(result.run.calls===3,'retry disabled ignored');
  await page.evaluate(()=>{window.prompt=message=>message.includes('额外重试预算')?'2':'5';window.confirm=()=>true;});
  await page.locator('#sequencePrepareRetry').click();await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('失败环节重试已免费准备'));
  assert(!await page.locator('#sequenceConsent').isChecked(),'old fee consent retained');
  assert((await get()).run.calls===3,'free retry preparation called model');
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:30000});
  result=await get();assert(result.run.calls===4&&result.run.steps[0].attempts.filter(a=>a.kind==='worker').length===2,'saved successes not reused');
  assert(errors.length===0,'runtime errors '+errors.join(';'));
  await page.screenshot({path:'output/supplied-zip-test/batch-retry-controls.png'});
  return {workerOnly:true,mergeOnly:true,retryCounts:true,explicitReauthorization:true,realModelCalls:0,runtimeErrors:0};
}
