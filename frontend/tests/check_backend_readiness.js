// Local old/new service compatibility check. All model/task POSTs are blocked.
async (page) => {
  let posts=0,badCalls=0;
  await page.route('**/api/**',async route=>{
    const request=route.request();
    if(request.method()==='POST'){posts++;await route.abort();return;}
    if(request.url().includes(':8870/')&&/\/(preview|execution)(\?|$)/.test(request.url()))badCalls++;
    await route.continue();
  });
  await page.goto('http://127.0.0.1:8870/incremental?source_result=20261124_105150_802e63a7');
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='error'&&!document.getElementById('example').disabled);
  if(!await page.locator('#chatSend').isDisabled())throw new Error('old service still allowed prediction');
  if(!await page.locator('#openPredictionService').isVisible())throw new Error('recovery link missing');
  const url=await page.locator('#openPredictionService').getAttribute('href');
  if(!url.includes(':8871/')||!url.includes('source_result='))throw new Error('recovery link lost source batch');
  await page.locator('#openPredictionService').click();
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready'&&!document.getElementById('chatSend').disabled);
  if(!page.url().includes(':8871/'))throw new Error('wrong prediction service');
  if(posts||badCalls)throw new Error('invalid or paid requests were made');
  return {oldServiceBlocked:true,recoveryPort:8871,batchPreserved:true,newServiceReady:true,missingEndpointRequests:0,postRequests:0};
}
