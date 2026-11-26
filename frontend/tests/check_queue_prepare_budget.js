// Actual uploaded packages; free preparation only. Never dispatch a model.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'isolated server required');
  let creates=0,previews=0,dispatches=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='POST')return;const path=new URL(r.url()).pathname;if(path==='/api/incremental/mixed-sessions')creates++;if(path.endsWith('/runs/preview'))previews++;if(/\/(next|start|step|plot-step)$/.test(path))dispatches++;});
  await page.setViewportSize({width:1750,height:1000});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (2).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('25');await page.locator('#samplePoints').click();
  assert((await page.locator('#sequenceBudgetHint').textContent()).includes('最多需要24次'),'budget gap not visible before click');
  await page.locator('#sequenceConsent').check();
  const t=Date.now();await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequencePreparationStatus').textContent.includes('预算不足'));
  const rejectionMs=Date.now()-t;
  assert(creates===0&&previews===0,'invalid budget already uploaded/created session');
  assert(await page.locator('#sequenceBudget').inputValue()==='20','budget silently increased');
  assert(!await page.locator('#sequenceConsent').isChecked(),'previous fee consent retained');
  assert(await page.locator('#sequenceNext').isDisabled(),'invalid preview executable');
  await page.locator('#sequencePanel').screenshot({path:'output/supplied-zip-test/queue-budget-rejected.png'});
  // Deliberately slow the free preview to verify visible progress, not a sleep workaround.
  await page.route('**/runs/preview',async route=>{await new Promise(resolve=>setTimeout(resolve,650));await route.continue();});
  await page.locator('#sequenceBudget').fill('24');await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequencePreparationStatus').textContent.includes('后端校验队列'));
  assert(await page.locator('#prepareSequence').isDisabled(),'duplicate prep not blocked');
  await page.waitForFunction(()=>document.querySelector('#sequencePreparationStatus').textContent.includes('准备完成：25组'));
  assert(creates===1&&previews===1&&dispatches===0,'free preparation issued duplicate/paid requests');
  assert(!await page.locator('#sequenceConsent').isChecked(),'prepared queue already authorized');
  const sid=await page.locator('#sessionId').inputValue(),state=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(state.calls===0,'prepare called model');
  await page.locator('#sequencePanel').screenshot({path:'output/supplied-zip-test/queue-budget-prepared.png'});
  assert(!errors.length,errors.join('\n'));
  return {actualZipFiles:3,groups:25,budgetRejectionMs:rejectionMs,rejectionBeforeUpload:true,creates,previews,visibleProgress:true,dispatches,realModelCalls:0};
}
