async page=>{
  const base='http://127.0.0.1:8892',assert=(v,m)=>{if(!v)throw Error(m);};
  const count=async()=> (await (await page.request.get(base+'/api/test/live-fake')).json()).calls;
  await page.goto(base+'/live?diagnostic=1');await page.locator('#create').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效0点'));
  const initial=await count();await page.locator('#raw').fill(JSON.stringify([1,2,3].map(i=>({source_observation_id:'p'+i,event_time:i,platform_id:'A',array:'tow',bearing_deg:20+i}))));await page.locator('#append').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效3点'));await page.locator('#selectAll').click();await page.locator('#prepare').click();await page.waitForFunction(()=>document.querySelector('#preview').textContent.includes('snapshot-'));
  await page.request.post(base+'/api/test/live-mode/disconnect');await page.locator('#consent').check();await page.locator('#analyse').click();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('unknown'));
  assert((await page.locator('#modelBody').textContent())==='partial-body','partial output lost');assert(await count()===initial+1,'failed call count');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('unknown'));assert(await count()===initial+1,'reload retried unknown call');
  assert(await page.locator('#rerun').isDisabled(),'unknown rerun lacks fee fence');await page.request.post(base+'/api/test/live-mode/valid');await page.locator('#feeAck').check();await page.locator('#rerun').click();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent.includes('awaiting_review'));assert(await count()===initial+2,'manual rerun count');
  await page.locator('#adopt').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('未分析0点'));
  const projection=JSON.parse(await page.locator('#projection').textContent());await page.locator('#retractIds').fill(projection.stream_groups[0].stable_id);await page.locator('#reason').fill('测试显式撤回');await page.locator('#retract').click();await page.waitForFunction(()=>JSON.parse(document.querySelector('#projection').textContent).stream_groups.length===0);assert(await page.locator('#chart [data-kind="actual"]').count()===3,'retraction deleted observations');
  assert(await count()===initial+2,'review/retract called model');
  return {unknownPreserved:true,noAutoRetry:true,feeAckRequired:true,explicitRerun:true,retractionVersions:true,actualObservationsPreserved:true,realModelCalls:0};
}
