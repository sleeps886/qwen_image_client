// Delayed local fake gateway required; no real provider calls.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.endsWith('/step'))calls++;});
  await page.goto(origin+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false); await page.locator('#predictionDebug').evaluate(el=>el.open=true);await page.locator('#example').click();
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatOptions').evaluate(el=>el.open=true);
  await page.locator('#singlePrediction').check();
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  assert(calls===1,'more than one prediction POST');
  assert(await page.locator('#executionNodes li').count()===3,'platform/model/spatial core nodes missing');
  assert(await page.locator('#executionSupportNodes li').count()===4,'local safeguards missing');
  assert(await page.locator('#chatSend').textContent()==='准备下一轮（免费）','next-round preparation action missing');
  const sid=await page.locator('#sessionId').inputValue();
  const state=await (await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  assert(state.calls===1&&state.rounds.length===1,'single model round not saved');
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  assert(calls===1,'refresh reissued model request');
  await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent.includes('等待发送'));
  assert(calls===1,'manual prepare called a model');
  assert(await page.locator('#chatPointTable input:not(:disabled)').count()>0,'next-round group remains locked');
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent.includes('等待发送'));
  assert(calls===1,'prepared-round refresh called a model');
  return {predictionPosts:1,visibleModelNodes:1,localSafeguards:4,savedRounds:1,stopped:true,refreshNoReplay:true,realModelCalls:0};
}
