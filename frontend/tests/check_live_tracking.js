async page=>{
  const base='http://127.0.0.1:8892',assert=(v,m)=>{if(!v)throw Error(m);};
  const callCount=async()=> (await (await page.request.get(base+'/api/test/live-fake')).json()).calls;
  await page.goto(base+'/live?diagnostic=1');await page.waitForFunction(()=>document.querySelector('#sessions').options.length>=1);
  assert((await (await page.request.get(base+'/api/test/live-fake')).json()).fake_gateway,'server must be fake');const initial=await callCount();
  await page.locator('#create').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效0点'));
  await page.locator('#append').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效1点'));
  await page.locator('#retryIngest').click();assert(await callCount()===initial,'ingest calls model');
  const rows=[2,3,4].map(i=>({source_observation_id:'raw-'+i,event_time:i,platform_id:'A',array:'hull',bearing_deg:30+i}));
  await page.locator('#raw').fill(JSON.stringify(rows));await page.locator('#append').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效4点'));
  await page.locator('#selectAll').click();await page.locator('#prepare').click();await page.waitForFunction(()=>document.querySelector('#preview').textContent.includes('snapshot-'));
  const preview=JSON.parse(await page.locator('#preview').textContent());assert(preview.points===4,'wrong snapshot scope');assert(await callCount()===initial,'free prepare calls model');
  await page.locator('#consent').check();await page.locator('#analyse').click();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('running'));
  await page.locator('#raw').fill(JSON.stringify({source_observation_id:'raw-5',event_time:5,platform_id:'A',array:'tow',bearing_deg:40}));await page.locator('#append').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效5点'));
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('awaiting_review'));
  const full=JSON.parse(await page.locator('#modelBody').textContent());assert(full.stream_groups.flatMap(g=>g.point_indices).length===4,'new point entered old snapshot');
  assert(await callCount()===initial+1,'duplicate model call');await page.locator('#adopt').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('未分析1点'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效5点'));await page.waitForFunction(()=>document.querySelector('#modelBody').textContent.includes('association-compact-v2'));
  assert(await callCount()===initial+1,'reload/read stream called model');assert(JSON.parse(await page.locator('#modelBody').textContent()).snapshot_id===preview.snapshot_id,'body restore not exact');
  await page.locator('#cutoff').fill('4');await page.locator('#origin').fill('3');await page.locator('#preparePrediction').click();await page.waitForFunction(()=>document.querySelector('#predictionPreview').textContent.includes('snapshot-'));
  const pred=JSON.parse(await page.locator('#predictionPreview').textContent());assert(pred.points===3,'causal boundary leaked future');assert(pred.future_projection_excluded,'future derived projection not excluded');
  await page.locator('#predictionConsent').check();await page.locator('#predict').click();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('completed'));
  assert(await callCount()===initial+2,'prediction count not independent');await page.locator('#predictions').selectOption({index:1});assert((await page.locator('#predictionVersion').textContent()).includes('causal_forecast'),'prediction version absent');
  assert(await page.locator('#chart [data-kind="actual"]').count()===5,'actual observations mixed with predictions');
  await page.setViewportSize({width:1280,height:900});await page.screenshot({path:'output/playwright/live-tracking-v2.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
  return {emptySession:true,singleAndBatch:true,idempotency:true,arrivalDuringAnalysis:true,frozenScope:true,exactStreamRestore:true,causalBoundary:true,independentPrediction:true,mobile:true,fakeCalls:2,realModelCalls:0};
}
