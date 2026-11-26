// Fake gateway only. New conversations are free, isolated, and start from time point 4.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let modelCalls=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))modelCalls++;});
  await page.goto(origin+'/incremental?empty=1');await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);await page.locator('#predictionDebug').evaluate(el=>el.open=true);await page.locator('#example').click();
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  const old=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  await page.locator('#openNewConversation').click();await page.locator('#conversationStart').fill('4');await page.locator('#createConversation').click();
  await page.waitForFunction(()=>document.getElementById('conversationNotice').textContent.includes('已免费新建'));
  assert(modelCalls===1,'new conversation called model');
  let current=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  assert(current.state.session_id!==old.state.session_id,'new conversation reused old task');
  assert(current.state.rounds.length===0,'old replies leaked to new conversation');
  assert(current.state.history.map(p=>p.t).join(',')==='0,10,20','start point 4 lacks preceding three observations');
  const draft=JSON.parse(await page.locator('#chatInput').inputValue());
  assert(draft.selected_time_groups.map(g=>g.t).join(',')==='0,10,20,30','fourth time point not selected');
  assert(!await page.locator('#chatConsent').isChecked(),'authorization copied to new conversation');
  const newId=current.conversationId;
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  current=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  assert(current.state.history.length===4&&current.state.history.at(-1).t===30,'new session did not start prediction at fourth observation');
  await page.locator(`[data-conversation-id="${old.conversationId}"]`).click();
  await page.waitForFunction(sid=>document.getElementById('sessionId').value===sid&&!document.getElementById('openNewConversation').disabled,old.state.session_id);
  assert((await page.locator('#chatMessages .assistant').count())===1,'old chat not restored');
  await page.locator(`[data-conversation-id="${newId}"]`).click();
  await page.waitForFunction(sid=>document.getElementById('sessionId').value===sid&&!document.getElementById('openNewConversation').disabled,current.state.session_id);
  await page.reload();await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  assert(await page.locator('#conversationList button').count()>=2,'conversation list lost on refresh');
  assert(modelCalls===2,'switch/refresh called model');
  return {newSessionFree:true,startTimeIndex:4,initialHistory:3,oldSessionPreserved:true,switchAndRefresh:true,modelPosts:2,realModelCalls:0};
}
