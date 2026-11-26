// Requires a local delayed fake gateway, never a paid provider.
async (page) => {
  const origin=new URL(page.url()).origin;
  const marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway test server required');
  const assert=(condition,message)=>{if(!condition)throw new Error(message);};
  await page.setViewportSize({width:1600,height:1000});
  await page.goto(origin+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false); await page.locator('#predictionDebug').evaluate(el=>el.open=true);await page.locator('#example').click();
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatOptions').evaluate(el=>el.open=true);
  await page.locator('#singlePrediction').uncheck();
  const initial=await page.locator('#chatQuestion').inputValue();
  await page.locator('#chatQuestion').fill('节点成功测试'+initial.slice(initial.indexOf('\n\n点位数据：\n')));
  const chat=await page.locator('.prediction-chat').boundingBox(),panel=await page.locator('#executionPanel').boundingBox();
  assert(panel.x>=chat.x+chat.width,'execution panel not right of chat');
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelector('#executionNodes .running strong')?.textContent.includes('模型'));
  assert((await page.locator('#executionNodes li').nth(4).getAttribute('class')).includes('pending'),'downstream completed before model reply');
  await page.waitForFunction(()=>document.getElementById('executionProgress').value===7&&!document.getElementById('chatSend').disabled);
  assert(await page.locator('#executionNodes .completed').count()===7,'success path incomplete');
  const text=await page.locator('#chatQuestion').inputValue();
  await page.locator('#chatQuestion').fill('故障测试'+text.slice(text.indexOf('\n\n点位数据：\n')));
  await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('executionBadge').textContent==='执行失败');
  assert((await page.locator('#executionNodes .failed strong').textContent()).includes('模型'),'wrong failure node');
  assert(await page.locator('#executionNodes .skipped').count()===3,'downstream failure not skipped');
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('executionBadge').textContent==='执行失败');
  assert(await page.locator('#chatSend').isDisabled(),'failed model call was enabled for blind retry');
  return {rightPanel:true,actualRunningObserved:true,successNodes:7,failureNode:'model_prediction',skippedNodes:3,refreshRestored:true,realModelCalls:0};
}
