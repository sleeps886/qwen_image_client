// Use only the local fake gateway; verifies per-round charts without paid calls.
async (page) => {
  const origin=new URL(page.url()).origin;
  const marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};let calls=0;
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.endsWith('/step'))calls++;});
  await page.goto(origin+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);
  await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#example').click();await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatOptions').evaluate(el=>el.open=true);
  await page.locator('#singlePrediction').uncheck();
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-prediction-figure').length===1&&!document.getElementById('chatSend').disabled);
  const first=await page.locator('.chat-prediction-figure svg').innerHTML();
  assert(first===await page.locator('#predictionChart').innerHTML(),'first reply chart differs from main chart');
  assert(!await page.locator('#predictionOverview').isVisible(),'duplicate overview still visible');
  await page.locator('.bearing-evidence').evaluateAll(elements=>elements.forEach(el=>el.open=true));
  assert(await page.locator('.assistant .chat-prediction-figure').isVisible(),'reply chart hidden');
  await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-prediction-figure').length===2&&!document.getElementById('chatSend').disabled);
  assert(await page.locator('.chat-prediction-figure svg').first().innerHTML()===first,'older chart overwritten by next round');
  assert(await page.locator('.chat-prediction-figure svg').last().innerHTML()===await page.locator('#predictionChart').innerHTML(),'latest reply chart differs from main chart');
  assert(await page.locator('.bearing-evidence svg polyline[stroke="#2166d1"]').count()===2,'model prediction lines missing');
  assert(await page.locator('.bearing-evidence svg polyline[stroke="#c47700"]').count()===2,'baseline lines missing');
  await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-prediction-figure').length===2);
  assert(await page.locator('.chat-prediction-figure svg').first().innerHTML()===first,'restored historical chart changed');
  assert(calls===2,'refresh issued prediction');
  assert(!await page.locator('#predictionOverview').isVisible(),'overview visible after refresh');
  await page.locator('.chat-prediction-figure').last().scrollIntoViewIfNeeded();
  await page.screenshot({path:'work/chat-reply-prediction.png'});
  return {replyCharts:2,mainChartMatches:true,historyFrozen:true,refreshRestored:true,predictionPosts:calls,realModelCalls:0};
}
