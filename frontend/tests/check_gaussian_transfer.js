// Run against a local test server using playwright-cli run-code --filename.
// Uses an existing saved batch; no recognition or prediction POST is allowed.
async (page) => {
  const origin=new URL(page.url()).origin;
  const resultId='20261124_102104_35b4863d';
  const assert=(condition,message)=>{if(!condition)throw new Error(message);};
  let postRequests=0,batchReads=0;
  await page.context().route('**/api/**',async route=>{
    if(route.request().method()==='POST'){postRequests++;await route.abort();return;}
    if(route.request().url().includes('/api/incremental/batches/'))batchReads++;
    await route.continue();
  });
  await page.goto(origin+'/');await page.waitForFunction(()=>workspaceReady);
  await page.evaluate(async id=>{
    // Reproduce the reported situation: cached P0 sample plus a real main-page result.
    await WorkspaceCache.set('prediction-workspace',{version:1,dataset:{candidates:[{candidate_id:'P0',t:0,bearing_deg:20,array:'hull',platform_id:'A'}]},seeds:'P0,P2,P4'});
    showEvidence({result_id:id,downloads:{'inverse_fields.json':'/api/results/'+id+'/inverse_fields.json'}});
    saveWorkspace();await WorkspaceCache.get('main-state');
  },resultId);
  assert((await page.locator('#predictionIntro').getAttribute('href')).includes('source_result='+resultId),'general entry omitted current result');
  const generic=await page.context().newPage();
  await generic.goto(origin+'/incremental');
  await generic.waitForFunction(()=>document.getElementById('batchReceipt').dataset.state==='success');
  assert((await generic.locator('#batchReceiptTitle').textContent()).includes('427'),'generic entry restored sample instead of batch');
  assert(!(await generic.locator('#seeds').inputValue()).split(',').some(id=>/^P\d+$/.test(id)),'sample seed IDs leaked into real batch');
  assert(await generic.locator('#batchPointRows tr').count()===12,'actual preview missing');
  await generic.close();
  const popupPromise=page.waitForEvent('popup');
  await page.locator('#sendGaussianToPrediction').click();
  const target=await popupPromise;
  await target.waitForFunction(()=>document.getElementById('batchReceipt')?.dataset.state==='success');
  await page.waitForFunction(()=>document.getElementById('gaussianTransferStatus').textContent.includes('传递成功'));
  const before=batchReads;
  await target.reload();
  await target.waitForFunction(()=>document.getElementById('batchReceipt')?.dataset.state==='success');
  assert(batchReads>before,'refresh trusted cached success without checking server');
  await target.goto(origin+'/incremental?source_result=20260101_000000_00000000');
  await target.waitForFunction(()=>document.getElementById('batchReceipt')?.dataset.state==='error');
  assert(await target.locator('#create').isDisabled(),'failed transfer allowed creating from old data');
  assert(!await target.locator('#batchPointPreview').isVisible(),'failure retained stale successful preview');
  await target.locator('#predictionAdvanced').evaluate(el=>el.hidden=false); await target.locator('#predictionDebug').evaluate(el=>el.open=true);
  await target.locator('#batchResultId').fill(resultId);
  await target.locator('#loadGaussianBatch').click();
  await target.waitForFunction(()=>document.getElementById('batchReceipt').dataset.state==='success');
  assert((await target.locator('#batchReceiptTitle').textContent()).includes('427'),'retry did not actually load batch');
  await target.route('**/api/incremental/batches/*',route=>route.fulfill({json:{version:'gaussian-prediction-batch-v1',source_result_id:resultId,point_count:999,candidates:[],batch_id:'invalid'}}));
  await target.reload();
  await target.waitForFunction(()=>document.getElementById('batchReceipt')?.dataset.state==='error');
  assert(await target.locator('#create').isDisabled(),'malformed batch was shown as success');
  assert(postRequests===0,'transfer triggered a paid POST');
  await target.close();
  return {realBatchPoints:427,oldSampleReplaced:true,sourceReceiptVerified:true,refreshRechecked:true,failureAndRetryVerified:true,malformedBatchRejected:true,postRequests};
}
