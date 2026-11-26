// Synthetic known geometry on the isolated fake gateway, not model accuracy.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  const data=await(await page.request.get(base+'/api/test-sequential-spatial-dataset')).json();
  await page.setViewportSize({width:1700,height:1000});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles({name:'spatial-geometry.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留10点'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('2');await page.locator('#samplePoints').click();await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备2组'));
  for(let i=1;i<=2;i++){
    await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
    await page.waitForFunction(i=>document.querySelector('#sequenceStatus').textContent.includes(`已处理${i}/2组`),i);
    assert(await page.locator('#sequenceSpatialChart circle').count()===i,'not one provisional position per group');
    assert(await page.locator('#sequenceSpatialGroupList>details').count()===i,'group detail missing');
  }
  assert(await page.locator('#sequenceSpatialChart [data-spatial-line]').count()===1,'checked provisional continuation missing');
  assert((await page.locator('#sequenceSpatialStatus').textContent()).includes('约定'),'mirror convention not disclosed');
  await page.locator('#sequenceSpatialGroupList>details').first().locator('summary').click();
  const detail=await page.locator('#sequenceSpatialGroupList>details').first().textContent();assert(detail.includes('"hull"')&&detail.includes('"tow"')&&detail.includes('handedness')&&detail.includes('selection_basis'),'complete hypothesis/mirror detail missing');
  const sid=await page.locator('#sessionId').inputValue(),root=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(root.calls===1&&Object.keys(root.tracks).length===0,'preview changed fees or confirmed state');
  await page.locator('#sequenceSpatialChart circle').first().hover();
  const explanation=await page.locator('#sequenceSpatialHover').textContent();assert(explanation.includes('本地假设')||explanation.includes('显示约定'),'display selection masquerades as inference');
  const before=await page.locator('#sequenceSpatialChart circle').first().getAttribute('cx');
  await page.locator('#sequenceSpatialZoomIn').click();assert((await page.locator('#sequenceSpatialChart circle').first().getAttribute('cx'))!==before,'zoom ineffective');await page.locator('#sequenceSpatialReset').click();
  await page.locator('#sequenceMirrorSide').selectOption('left');
  assert((await page.locator('#sequenceSpatialChart circle').first().getAttribute('cx'))!==before,'mirror switch ineffective');
  await page.waitForFunction(async()=>{const s=await WorkspaceCache.get('mixed-workspace');return s.spatialMirrorSide==='left';});
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#sequenceSpatialChart circle').length===2);
  assert(await page.locator('#sequenceMirrorSide').inputValue()==='left','mirror display preference lost');
  const after=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(after.calls===root.calls&&JSON.stringify(after.tracks)===JSON.stringify(root.tracks),'switch changed backend state or model calls');
  await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/sequence-provisional-one-position.png'});
  assert(!errors.length,errors.join('\n'));
  return {oneHypothesisPerGroup:true,explicitMirrorConvention:true,switchAndReload:true,allAlternativesKept:true,checkedLine:true,backendStateUnchanged:true,fakeCalls:1,realCalls:0};
}
