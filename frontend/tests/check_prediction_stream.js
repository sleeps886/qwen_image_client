// True delayed SSE from the local fake gateway; never send to a real provider.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))calls++;});
  await page.goto(origin+'/incremental?empty=1');await page.waitForFunction(()=>!document.getElementById('chatPrepare').disabled||!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);await page.locator('#predictionDebug').evaluate(el=>el.open=true);await page.locator('#example').click();
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelector('.assistant.streaming p')?.textContent.includes('模拟网关回复'));
  const live=await page.locator('.assistant.streaming p').textContent();
  assert(!live.includes('"step_id"')&&!live.includes('"reason"'),'raw protocol JSON leaked into chat');
  const sid=await page.locator('#sessionId').inputValue();
  const pending=await(await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  assert(pending.status==='pending'&&pending.version===0&&pending.rounds.length===0,'streamed draft committed before validation');
  assert(await page.locator('#chatMessages .chat-prediction-figure').count()===0,'unvalidated forecast plotted');
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  assert(await page.locator('.assistant .chat-prediction-figure').count()===1,'validated chart missing');
  assert(calls===1,'stream issued more than one model POST');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.assistant .chat-prediction-figure').length===1);
  assert(calls===1,'refresh replayed stream');
  return {textBeforeCompletion:true,noRawJson:true,noPrematureCommit:true,validatedChart:true,modelPosts:1,realModelCalls:0};
}
