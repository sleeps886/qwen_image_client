// Platform navigation only; fake model for moving fixture, read-only recovery for old real batch.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  await page.goto(origin+'/incremental?empty=1');await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  const fixture={candidates:Array.from({length:10},(_,i)=>({candidate_id:`P${i}`,t:i*10,bearing_deg:30+i,array:'hull',platform_id:'A'})),
    platform_config:{platforms:[{name:'A',start_ll:[30.0623,122.0415],heading_deg:90,speed_kn:10,hull_m:[0,0],tow_m:[-150,0]}]}};
  await page.locator('#datasetFile').setInputFiles({name:'platform-motion.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture))});
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  assert(await page.locator('.platform-motion-figure').count()===1,'first trajectory lacks platform coordinates');
  const sid=await page.locator('#sessionId').inputValue(),state=await(await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  const first=JSON.stringify(state.rounds[0].input.platform_state);
  assert(state.rounds[0].input.platform_state.points.at(-1).t===state.visible_until,'platform snapshot timeline mismatch');
  assert(state.platform_motion.points.at(-1).east_m>0,'moving platform shown as stationary');
  await page.locator('#chatSend').click();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('#chatMessages .assistant').length===2&&document.querySelectorAll('#sharedTrajectoryScene .platform-motion-figure').length===1&&document.getElementById('chatState').textContent==='单次预测已完成');
  const updated=await(await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  assert(JSON.stringify(updated.rounds[0].input.platform_state)===first,'first platform frame overwritten');
  assert((await page.locator('.platform-motion-figure').last().textContent()).includes('51.4 米'),'per-round platform displacement not shown');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#sharedTrajectoryScene .platform-motion-figure').length===1);
  assert((await page.locator('.platform-motion-figure').textContent()).includes('51.4 米'),'platform update not restored');
  await page.locator('.platform-analysis-details').last().evaluate(el=>el.open=true);
  await page.locator('.platform-motion-figure').last().scrollIntoViewIfNeeded();await page.screenshot({path:'work/platform-motion-chat.png'});
  return {firstRoundCoordinates:true,movingPlatform:true,displacementPerRound:true,historyFrozen:true,refreshRestored:true,realModelCalls:0};
}
