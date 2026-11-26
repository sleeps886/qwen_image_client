// Actual packages, deliberately bad R candidate at step 4; fake replies only.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
  await page.setViewportSize({width:1750,height:1100});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (2).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('20');await page.locator('#samplePoints').click();
  await page.locator('#chatQuestion').fill('TEST_ONLY_UNKNOWN_CANDIDATE');await page.locator('#chatQuestion').dispatchEvent('input');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备20组'));
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理1/20组'));
  assert((await page.locator('#sequenceWorkingStatus').textContent()).includes('尚未执行19组'),'future groups conflated with done');
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:90000});
  const sid=await page.locator('#sessionId').inputValue(),root=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  const run=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid+'/runs/'+root.active_sequential_run)).json();
  const v=run.working_view,g=v.spatial_preview,p=v.progress;
  assert(run.cursor===20&&v.units.length===20&&v.points.length===40,'units/raw observations lost');
  assert(p.unstarted_steps===0&&p.pending_steps===0,'progress not complete');
  assert(run.steps[3].warnings.some(w=>w.code==='unknown_candidate_proposal'),'bad R not reported');
  assert(run.steps[3].status==='validated','bad R stopped full queue');
  const accounted=new Set([...g.tracks.flatMap(t=>t.points.map(p=>p.unit_id)),...g.unavailable.map(p=>p.unit_id)]);
  assert(v.units.every(u=>accounted.has(u.unit_id)),'unit silently omitted from spatial accounting');
  const display=await page.evaluate(v=>PointSampling.spatialDisplayGroups(v.spatial_preview,v.units,v.points),v);assert(await page.locator('#sequenceSpatialChart circle').count()===display.plotted,'main chart duplicates mirrors');assert(await page.locator('#sequenceSpatialGroupList>details').count()===20,'input group not represented exactly once');
  assert((await page.locator('#sequenceWorkingCount').textContent()).includes('已处理20/20组'),'badge misleading');
  assert((await page.locator('#sequenceWorkingStatus').textContent()).includes('20组＝40条原始观测'),'unit/raw count missing');
  assert(await page.locator('#sequenceSpatialChart [data-line-basis=\"local_provisional\"]').count()>0,'no provisional spatial lines');
  assert(root.calls===19&&Object.keys(root.tracks).length===0,'budget or confirmation bypass');
  await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/twenty-groups-spatial-visible.png'});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceWorkingCount').textContent.includes('已处理20/20组'));
  return {validatedGroups:run.cursor,rawObservations:v.points.length,spatialGroups:p.spatial_units,spatialPointsWithMirrors:p.spatial_points_including_mirrors,explicitMissing:p.unavailable_spatial_units,mainPositionPoints:display.plotted,positionPending:display.pending,provisionalLines:await page.locator('#sequenceSpatialChart [data-line-basis=\"local_provisional\"]').count(),badReferenceIsolated:true,fakeCalls:19,realModelCalls:0};
}
