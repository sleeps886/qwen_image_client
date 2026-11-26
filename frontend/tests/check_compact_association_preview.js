async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let paid=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))paid++;});
  await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const candidates=Array.from({length:100},(_,i)=>({candidate_id:'C'+i,platform_id:'P',array:i%2?'tow':'hull',t:Math.floor(i/2)*10,bearing_deg:30+i*.02,quality:'fitted',sigma_deg:1,mixed:false}));
  await page.locator('#pointFileImport').setInputFiles({name:'compact-preview.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({candidates}))});
  await page.waitForFunction(()=>!document.querySelector('#selectAllPoints').disabled);await page.locator('#selectAllPoints').click();
  const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/preview'));
  await page.locator('#chatPrepare').click();const data=await(await response).json();
  const stats=data.transport_stats;assert(stats.observations===100,'point count wrong');assert(stats.wire_chars<stats.canonical_chars,'input not compacted');
  assert(data.context.observations.length===100,'preview dropped observations');assert(data.model_calls===0,'preview made model call');
  await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
  assert((await page.locator('#chatNotice').textContent()).includes(`完整保留100点，传输输入${stats.wire_chars}字符`),'compact metrics not displayed');
  const sid=await page.locator('#sessionId').inputValue();assert((await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json()).calls===0,'model counter changed');
  await page.route('**/api/health',async route=>{const response=await route.fetch(),health=await response.json();delete health.mixed_association_transport;await route.fulfill({json:health});});
  await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('关联输入优化版'));
  assert(await page.locator('#chatPrepare').isDisabled(),'stale backend still enabled');assert(await page.locator('#chatSend').isDisabled(),'stale backend paid send enabled');
  await page.unroute('**/api/health');assert(paid===0,'unexpected paid request');
  return {all100Retained:true,wireSmaller:true,metricsVisible:true,staleBackendBlocked:true,modelCalls:0,canonicalChars:stats.canonical_chars,wireChars:stats.wire_chars};
}
