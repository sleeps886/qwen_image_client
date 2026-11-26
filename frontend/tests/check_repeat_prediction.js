// Reproduce empty/stale next-round selection. Fake gateway only, two model POSTs.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))calls++;});
  await page.goto(origin+'/incremental?empty=1');await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);await page.locator('#predictionDebug').evaluate(el=>el.open=true);await page.locator('#example').click();
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  await page.locator('#chatSend').click();await page.waitForFunction(()=>document.getElementById('chatState').textContent.includes('等待发送'));
  {const label=await page.locator('#candidateGroupList input:checked:not(:disabled)').getAttribute('aria-label');await page.getByRole('checkbox',{name:label,exact:true}).first().uncheck();}
  assert((await page.locator('#chatAttachmentSummary').textContent()).includes('0个时间组'),'empty selection not reproduced');
  await page.locator('#chatPrepare').click();
  await page.waitForFunction(()=>document.querySelectorAll('#candidateGroupList input:checked:not(:disabled)').length===1);
  assert(await page.locator('#candidateGroupList input:checked:not(:disabled)').count()===1,'manual prepare did not reset empty selection');
  {const label=await page.locator('#candidateGroupList input:checked:not(:disabled)').getAttribute('aria-label');await page.getByRole('checkbox',{name:label,exact:true}).first().uncheck();}
  await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('#candidateGroupList input:checked:not(:disabled)').length===1);
  assert(calls===1,'empty-selection repair invoked model');
  assert(await page.locator('#candidateGroupList input:checked:not(:disabled)').count()===1,'send did not restore empty selection');
  await page.evaluate(async()=>{const saved=await WorkspaceCache.get('prediction-workspace');saved.selectedGroupKeys=[JSON.stringify(['A',0])];await WorkspaceCache.set('prediction-workspace',saved);});
  await page.reload();await page.waitForFunction(()=>document.getElementById('chatState').textContent.includes('等待发送'));
  assert(await page.locator('#candidateGroupList input:checked:not(:disabled)').count()===1,'stale saved selection erased next defaults');
  assert(!await page.locator('#chatConsent').isChecked(),'consent restored');
  await page.locator('#chatSend').click();assert(calls===1,'missing consent invoked model');
  assert((await page.locator('#chatActionHint').textContent()).includes('授权'),'missing consent not explained');
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('#chatMessages .assistant').length===2&&document.getElementById('chatState').textContent==='单次预测已完成');
  assert(calls===2,'second prediction did not run');
  return {modelPosts:2,emptySelectionRepaired:true,staleSelectionRestored:true,consentRequired:true,twoRoundsCompleted:true,realModelCalls:0};
}
