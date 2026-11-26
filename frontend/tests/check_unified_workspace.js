async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let paid=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))paid++;});
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('已就绪'));
  assert(await page.locator('a[href="/live"]').count()===0,'still sends user to another workbench');
  const candidates=[0,10,20].flatMap(t=>['hull','tow'].map(array=>({candidate_id:array+t,t,platform_id:'A',array,bearing_deg:30+t*.1+(array==='tow'?1:0)})));
  await page.locator('#pointFileImport').setInputFiles({name:'original.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({candidates}))});await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#selectAllPoints').click();await page.locator('#chatPrepare').click();await page.waitForFunction(()=>document.querySelector('#chatInput').textContent.includes('input_snapshot'));await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
  const sid=await page.locator('#sessionId').inputValue(),snapshot=JSON.parse(await page.locator('#chatInput').textContent());assert(snapshot.input_snapshot.protocol==='workspace-ledger-v1','same workbench not using ledger');assert(paid===0,'free preparation calls model');
  await page.locator('#workspaceRawPoints').evaluate(n=>n.closest('details').open=true);
  await page.locator('#workspaceRawPoints').fill(JSON.stringify({candidate_id:'new-30',t:30,platform_id:'A',array:'hull',bearing_deg:33}));
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();await page.waitForFunction(()=>document.querySelector('#chatSend').textContent.includes('正在处理'));
  assert(await page.locator('#appendWorkspacePoints').isEnabled(),'model blocks input append');await page.locator('#appendWorkspacePoints').click();await page.waitForFunction(()=>document.querySelector('#chatPayloadSummary').textContent.includes('全批 7点'));
  await page.waitForFunction(()=>!document.querySelector('#trajectoryReview').hidden);assert(await page.locator('#sessionId').inputValue()===sid,'append replaced current session');assert(paid===1,'append dispatched second model call');
  const latest=await (await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();assert(latest.rounds[0].input.observations.length===6,'new point mixed into old snapshot');assert(latest.input_summary.accepted_count===7,'new point lost after model commit');
  await page.locator('#adoptTrajectory').click();await page.waitForFunction(()=>document.querySelector('#chatState').textContent.includes('等待新增'));
  assert((await page.locator('#runModelPlot').textContent()).includes('空间轨迹'),'original spatial workflow replaced');
  const row=page.getByLabel('整行 A 30秒',{exact:true});await row.check();await page.locator('#chatPrepare').click();await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('完整保留1点'));
  const next=JSON.parse(await page.locator('#chatInput').textContent());assert(next.observations[0].t===30,'new input cannot be analyzed in same workbench');assert(Object.keys(next.tracks).length===2,'adopted history lost');
  await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelector('#workspaceInputStatus').textContent.includes('持久化保留7点'));assert(await page.locator('#sessionId').inputValue()===sid,'reload lost same session');assert(paid===1,'reload repeated paid call');
  await page.locator('#pointImportMode').selectOption('append');await page.locator('#workspaceSource').fill('independent-file');
  await page.locator('#pointFileImport').setInputFiles({name:'append.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({candidates:[{candidate_id:'new-40',t:40,platform_id:'A',array:'tow',bearing_deg:34}]}))});await page.waitForFunction(()=>document.querySelector('#workspaceInputStatus').textContent.includes('持久化保留8点'));assert(await page.locator('#sessionId').inputValue()===sid,'file append changed conversation');assert(paid===1,'file append calls model');
  await page.goto(base+'/live');assert(new URL(page.url()).pathname==='/incremental','old link still opens separate page');await page.waitForFunction(()=>document.querySelector('#workspaceInputStatus').textContent.includes('持久化保留8点'));
  await page.screenshot({path:'output/playwright/unified-workspace.png',fullPage:true});
  return {sameWorkbench:true,sameConversation:true,ledger:true,appendDuringModel:true,frozenSnapshot:true,historyRetained:true,spatialWorkflowRetained:true,fileAppend:true,reload:true,noSeparateEntry:true,fakeModelCalls:1,realModelCalls:0};
}
