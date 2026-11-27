// Real running server, user's saved task, separate browser profile. GET only.
async page=>{
  const base='http://127.0.0.1:8870',sid='20261126_193818_d538fbe8',assert=(v,m)=>{if(!v)throw Error(m);};let posts=0;const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
    if(route.request().method()!=='GET'){posts++;await route.abort();return;}
    await route.continue();
  });
  await page.setViewportSize({width:1750,height:1150});await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const fixture=await(await page.request.get('http://127.0.0.1:8891/api/test-saved-display/mirror100')).json();
  const state=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  await page.evaluate(async({state,dataset})=>{
    await WorkspaceCache.set('mixed-workspace',{state,dataset,conversationId:'live-read-only-target-mirror',selected:[],groups:[''],messages:[],executionHistory:[],executionMode:'batch-snapshot-v1',useSequential:true});
  },{state,dataset:fixture.dataset});
  await page.goto(base+'/incremental');
  await page.waitForFunction(()=>document.querySelectorAll('#sequenceTargetMirrorList select').length===3);
  const pos=()=>page.locator('#sequenceSpatialChart circle[data-target-id="B000003"][data-mirror-preview="false"]').evaluateAll(ns=>ns.map(n=>[n.dataset.unitId,n.dataset.worldX,n.dataset.worldY]));
  const before=await pos();
  await page.locator('#sequenceTargetMirrorList select[data-target-id="B000003"]').selectOption('mirror');
  const after=await pos();assert(JSON.stringify(before)!==JSON.stringify(after),'live blue unchanged');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceTargetMirrorList select[data-target-id="B000003"]')?.value==='mirror');
  assert(JSON.stringify(after)===JSON.stringify(await pos()),'live mirror lost on refresh');
  const final=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(final.calls===state.calls&&final.version===state.version,'production result changed');
  assert(await page.locator('#sequenceSpatialGroupList>details').count()===100,'live group list incomplete');
  assert(posts===0&&errors.length===0,'POST or browser error: '+errors.join(';'));
  await page.screenshot({path:'output/supplied-zip-test/target-mirror-live-8870.png'});
  return {server:base,session:sid,bluePoints:after.length,liveGetOnly:true,refreshStable:true,postRequests:posts,newModelCalls:final.calls-state.calls,runtimeErrors:errors};
}
