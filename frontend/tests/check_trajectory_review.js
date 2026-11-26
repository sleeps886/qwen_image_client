async (page) => {
  const assert=(v,m)=>{if(!v)throw new Error(m);};let modelPosts=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/step'))modelPosts++;});
  await page.setViewportSize({width:1500,height:1000});
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false);
  await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#datasetFile').setInputFiles('work/shared-scene-fixture.json');
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=true);
  await page.locator('#targetTrackSelect').selectOption('G1');
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>!document.getElementById('trajectoryReview').hidden);
  await page.waitForFunction(()=>document.querySelector('#sharedTrajectoryScene polyline[data-spatial-kind="observed"]'));
  assert(await page.locator('#sharedTrajectoryScene polyline[data-spatial-kind="observed"]').first().getAttribute('stroke-dasharray'),'pending candidate must be dashed');
  assert(await page.locator('#chatSend').isDisabled(),'must stop while reviewing');
  assert(await page.locator('#sharedTrajectoryScene polyline[data-spatial-kind="platform"]').count()===0,'stationary platform path');
  await page.reload();await page.waitForFunction(()=>!document.getElementById('trajectoryReview').hidden);
  assert(modelPosts===1,'refresh repeated model');
  await page.locator('#reviewReason').fill('选点不对');await page.locator('#rejectTrajectory').click();
  await page.waitForFunction(()=>document.getElementById('trajectoryReview').hidden);
  assert(modelPosts===1,'rejection invoked model');
  await page.locator('#allPointsPreview').click();
  await page.waitForFunction(()=>!document.getElementById('trajectoryReview').hidden);
  const sid=await page.locator('#sessionId').inputValue();
  const state=await(await page.request.get(new URL(page.url()).origin+'/api/incremental/sessions/'+sid)).json();
  assert(state.calls===0&&state.history.length===6,'offline must include entire selected target');
  await page.locator('#adoptTrajectory').click();
  await page.waitForFunction(()=>document.getElementById('trajectoryReview').hidden);
  await page.waitForFunction(()=>document.querySelector('#sharedTrajectoryScene polyline[data-spatial-kind="observed"]')?.getAttribute('stroke-dasharray')===null);
  assert(modelPosts===1,'adoption invoked model');
  await page.locator('#sharedTrajectoryScene [data-scene-view="bearing"]').click();
  assert(await page.locator('#sharedTrajectoryScene [data-platform-time]').count()===1,'stationary time band should have one marker');
  await page.screenshot({path:'work/trajectory-review-verified.png'});
  return {modelPosts,realModelCalls:0,interrupt:true,rejectionRestores:true,refreshPaused:true,allPoints:6,stationarySingleMarker:true,adoptedSolid:true};
}

