async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  await page.unrouteAll({behavior:'wait'});
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'isolated fake gateway required');
  await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const data={candidates:Array.from({length:5000},(_,i)=>({candidate_id:`L${i}`,platform_id:'P',array:i%2?'tow':'hull',t:Math.floor(i/2)%170,bearing_deg:30+(Math.floor(i/2)%170)*.1+(Math.floor(i/340)%4)*15}))};
  await page.locator('#pointFileImport').setInputFiles({name:'large-selection.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>!document.querySelector('#selectAllPoints').disabled);
  await page.locator('#selectAllPoints').click();
  assert((await page.locator('#chatAttachmentSummary').textContent()).includes('5000'),'selection not complete');
  const prepared=page.waitForResponse(r=>r.url().endsWith('/preview'));
  await page.locator('#chatPrepare').click();
  const response=await prepared;assert(response.status()===200,'large preview rejected');
  const result=await response.json();assert(result.context.observations.length===5000,'large preview truncated');
  await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
  assert((await page.locator('#chatNotice').textContent()).includes('尚未调用模型'),'preparation feedback missing');assert((await page.locator('#backendReadiness').textContent()).includes('不设观测点数量上限'),'unlimited schema not recognised');
  let sent=0;
  await page.route('**/mixed-sessions/*/step*',async route=>{
    const body=route.request().postDataJSON();assert(body.selected_ids.length===5000,'send truncated');assert(body.consent===true&&body.context_digest,'digest/consent missing');sent++;
    // Stop before the server: this test must never make an upstream model call.
    await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({detail:'隔离测试：已收到5000个观测，未调用模型。'})});
  });
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled&&document.querySelector('#chatNotice').textContent.includes('隔离测试：已收到5000'));
  assert(sent===1,'send not reached exactly once');
  assert(!await page.locator('#chatConsent').isChecked(),'consent not cleared');
  await page.unroute('**/mixed-sessions/*/step*');
  // Simulate an old backend schema and ensure a useful local error, not 422.
  await page.route('**/openapi.json',async route=>{const r=await route.fetch(),schema=await r.json();schema.components.schemas.MixedSelection.properties.selected_ids.maxItems=256;await route.fulfill({json:schema});});
  await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
  let posts=0;await page.route('**/mixed-sessions/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();}else await route.continue();});
  await page.locator('#chatPrepare').click();
  await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('当前后端最多支持256'));
  assert((await page.locator('#chatNotice').textContent()).includes('请重启后端'),'missing upgrade guidance');assert(posts===0,'old backend guard still posted');
  await page.unroute('**/openapi.json');await page.unroute('**/mixed-sessions/**');
  return {full5000Preview:true,noTruncation:true,sendHandlerReached:true,consentRetainedOnPrepare:true,oldBackendGuidance:true,modelCalls:0};
}
