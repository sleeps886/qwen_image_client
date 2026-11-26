async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const dataset=await(await page.request.get(base+'/api/test-sequential-spatial-dataset')).json();
  await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles({name:'batch-smoke.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(dataset))});
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('10个原始观测'));
  await page.locator('#sequenceRetryEnabled').uncheck();await page.locator('#samplePointCount').fill('4');await page.locator('#samplePoints').click();
  await page.locator('#sequenceMode').selectOption('batch-snapshot-v1');await page.locator('#sequenceBatchSize').fill('2');await page.locator('#sequenceBatchSize').dispatchEvent('change');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备4组'));
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:60000});
  await page.locator('#sequenceAdopt').click();await page.waitForFunction(()=>document.querySelector('#sequenceAdopt').hidden);
  await page.locator('#sequenceBudget').fill('100');await page.locator('#sequenceBudget').dispatchEvent('change');
  await page.evaluate(()=>{window.confirm=()=>true;});await page.locator('#sequenceApplyBudget').click();await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('预算已明确更新'));
  const sid=await page.locator('#sessionId').inputValue(),root=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(root.calls===6&&root.max_calls===106&&Object.keys(root.targets).length===1,'adoption/budget integration failed');
  const snapshots=await(await page.request.get(base+new URL(await page.locator('#sequenceSnapshots').getAttribute('href'),base).pathname)).json();assert(snapshots.snapshots.length===2,'snapshot download missing');
  assert(errors.length===0,'runtime errors '+errors.join(';'));
  return {batches:2,calls:6,adoption:true,budgetChangeFree:true,snapshotDownload:true,runtimeErrors:0};
}
