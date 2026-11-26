// Real frozen batch + fake model only: choose a hypothesis, then its time-group rows.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))calls++;});
  await page.goto(origin+'/incremental?source_result=20261124_130544_79f1a01c');
  await page.waitForFunction(()=>document.querySelectorAll('#targetTrackSelect option').length>2);
  assert(await page.locator('#candidateGroupList tbody tr').count()===0,'all candidates shown before target selection');
  assert(await page.locator('#pointDetailSelect option').count()===0,'all candidate dropdown still shown');
  const batch=await(await page.request.get(origin+'/api/incremental/batches/20261124_130544_79f1a01c')).json();
  const target=batch.target_catalog[0];await page.locator('#targetTrackSelect').selectOption(target.target_id);
  await page.waitForFunction(()=>document.getElementById('conversationNotice').textContent.includes('新建会话')&&!document.getElementById('targetTrackSelect').disabled);
  let saved=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  assert(saved.selectedTargetId===target.target_id,'target selection not saved');
  assert(saved.dataset.candidates.length===target.candidate_ids.length,'wrong candidate scope');
  assert(saved.dataset.candidates.every(p=>target.candidate_ids.includes(p.candidate_id)),'other target points leaked');
  assert(await page.locator('#pointDetailSelect option').count()===target.candidate_ids.length,'point detail dropdown not scoped');
  const groups=new Set(saved.dataset.candidates.map(p=>JSON.stringify([p.platform_id,p.t])));
  assert(await page.locator('#candidateGroupList tbody tr').count()===groups.size,'wrong target rows');
  assert(calls===0,'target selection invoked a model');
  const label=await page.locator('#candidateGroupList input:not(:disabled)').first().getAttribute('aria-label');
  await page.getByRole('checkbox',{name:label,exact:true}).first().check();
  const draft=JSON.parse(await page.locator('#chatInput').inputValue());
  assert(draft.selected_time_groups.flatMap(g=>[...g.hull,...g.tow]).every(p=>target.candidate_ids.includes(p.candidate_id)),'composer contains other target points');
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  const first=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  const round=first.state.rounds[0];assert(round.input.selected_time_groups.flatMap(g=>[...g.hull,...g.tow]).every(p=>target.candidate_ids.includes(p.candidate_id)),'gateway received other target points');
  await page.locator('#targetTrackSelect').selectOption(batch.target_catalog[1].target_id);
  await page.waitForFunction(id=>document.getElementById('targetTrackSelect').value===id&&!document.getElementById('targetTrackSelect').disabled,batch.target_catalog[1].target_id);
  saved=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  assert(saved.conversationId!==first.conversationId&&saved.state===null,'target switch reused old track state');
  assert((await page.evaluate(id=>WorkspaceCache.get('prediction-conversation-'+id),first.conversationId)).state.rounds.length===1,'old target session lost');
  await page.reload();await page.waitForFunction(id=>document.getElementById('targetTrackSelect').value===id,batch.target_catalog[1].target_id);
  assert(await page.locator('#pointDetailSelect option').count()===batch.target_catalog[1].candidate_ids.length,'target scope lost after refresh');
  assert(calls===1,'target switching/refresh invoked model');
  return {targetCount:batch.target_catalog.length,filteredRows:true,filteredPointMenu:true,modelScopeValidated:true,oldSessionPreserved:true,refreshRestored:true,modelPosts:1,realModelCalls:0};
}
