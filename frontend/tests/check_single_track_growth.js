// Local fake gateway only: same candidate fragment grows from 1 to 5 observations.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  await page.goto(origin+'/incremental?source_result=20261124_105150_802e63a7');
  await page.waitForFunction(()=>document.getElementById('chatState').textContent.includes('预载'));
  await page.locator('#loadDebugTrack').click();
  await page.waitForFunction(()=>document.getElementById('debugTrackNotice').textContent.includes('从1点开始'));
  assert(!await page.locator('#singlePrediction').isChecked(),'single-run mode blocks incremental debugging');
  await page.locator('#chatConsent').check();let first;
  for(let n=1;n<=5;n++){
    await page.locator('#chatSend').click();
    await page.waitForFunction(n=>document.querySelectorAll('#chatMessages .assistant').length===n&&!document.getElementById('chatSend').disabled,n);
    const sid=await page.locator('#sessionId').inputValue();
    const state=await(await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
    assert(state.history.length===n,'did not add exactly one observed point');
    if(n===1){assert(state.prediction.length===0&&state.status==='ambiguous','one point fabricated a prediction');first=await page.locator('.chat-prediction-figure svg').first().innerHTML();}
    else assert(state.prediction.length>0,'multi-point prediction missing');
    assert(await page.locator('.chat-prediction-figure svg').first().innerHTML()===first,'historical single-point chart changed');
    const input=state.rounds.at(-1).input;
    assert(!JSON.stringify(input).includes('F0R8P'),'future candidate leaked');
  }
  await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-prediction-figure').length===5);
  assert(await page.locator('#debugTrackNotice').isVisible(),'debug reference disclosure lost');
  return {observedCounts:[1,2,3,4,5],singlePointNoForecast:true,oneNewPointPerRound:true,historyRestored:true,realModelCalls:0};
}
