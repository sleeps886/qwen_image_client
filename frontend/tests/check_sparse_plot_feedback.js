async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let paid=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))paid++;});
  await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('已就绪'));
  const load=async query=>{
    const f=await (await page.request.get(base+'/api/test-empty-spatial-fixture'+query)).json();assert(f.fake_gateway,'fake fixture required');
    const state=await (await page.request.get(base+'/api/incremental/mixed-sessions/'+f.session_id)).json();
    await page.evaluate(async({f,state})=>WorkspaceCache.set('mixed-workspace',{dataset:f.dataset,state,selected:[],groups:[''],messages:[],executionHistory:[],conversationId:crypto.randomUUID()}),{f,state});
    await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelector('#associationQualityNotice').textContent.includes('未归属11点'));
    return f;
  };
  await load('?empty=true');assert(await page.locator('#spatialTrajectoryScene').isVisible(),'empty spatial result explanation hidden');assert((await page.locator('#spatialStatusBadge').textContent()).includes('尚无空间解'),'empty result mislabeled success');
  assert((await page.locator('#associationQualityNotice').textContent()).includes('hull接入0/11'),'per-array coverage missing');assert(await page.locator('#spatialZoomIn').isDisabled(),'empty map zoom still active');assert((await page.locator('#associationQualityNotice').textContent()).includes('本地门限退回0点'),'model unassigned mistaken as local filter');
  assert(await page.locator('#bearingResultScene').isVisible(),'saved bearing diagnostic hidden');assert(await page.locator('#bearingResultChart [data-kind="actual-bearing-support"]').count()===9,'actual support lost');assert(await page.locator('#bearingResultChart polyline').count()===0,'empty model curves fabricated by UI');
  await page.locator('#spatialTrajectoryScene').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/sparse-no-space-feedback.png'});
  await load('?fresh=true');await page.locator('#modelPlotConsent').check();assert(await page.locator('#runModelPlot').isDisabled(),'empty geometry still allows paid spatial dispatch');
  await page.locator('#prepareModelPlot').click();await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('空间候选为0'));assert(paid===0,'free preflight spent model call');
  await page.locator('#modelPlotPurpose').selectOption('bearing');assert(!await page.locator('#modelPlotConsent').isChecked(),'changed purpose reused old fee consent');await page.locator('#modelPlotConsent').check();await page.locator('#runModelPlot').click();
  await page.waitForFunction(()=>document.querySelectorAll('#bearingResultChart [data-kind="validated-model-bearing-fit"]').length>0);assert(paid===1,'explicit diagnostic must be one fake call');assert((await page.locator('#spatialStatus').textContent()).includes('仅授权方位诊断'),'bearing output mislabeled spatial');
  await page.evaluate(()=>window.confirm=()=>true);await page.locator('#supplementEvidence').click();assert(paid===1,'neighbor suggestion called model');assert(!await page.locator('#chatConsent').isChecked(),'free neighborhood selection reused paid consent');
  return {unassignedExplained:true,noHiddenSpatialFailure:true,noFabricatedLines:true,emptyGeometryNoPaidCall:true,explicitBearingPurpose:true,modelCurvesShown:true,optionalNeighborhoodNoPaidCall:true,realModelCalls:0};
}
