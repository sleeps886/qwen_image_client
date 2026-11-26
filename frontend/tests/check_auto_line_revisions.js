// No model truth claims: fake replies leave new points unassigned.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1700,height:1100});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const platform={name:'A',start_ll:[30,122],heading_deg:0,speed_kn:0,hull_m:[0,0],tow_m:[-150,0]};
  const data={platform_config:{platforms:[platform]},candidates:[40,60,61].flatMap((b,i)=>['hull','tow'].map(array=>({candidate_id:array+i,t:i*10,platform_id:'A',array,bearing_deg:b-(array==='tow'?5:0)})))};
  await page.locator('#pointFileImport').setInputFiles({name:'revisable-lines.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留6点'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('3');await page.locator('#samplePoints').click();
  await page.locator('#chatQuestion').fill('TEST_ONLY_UNKNOWN_CANDIDATE');await page.locator('#chatQuestion').dispatchEvent('input');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备3组'));
  let firstLine;
  for(let i=1;i<=3;i++){
    await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
    await page.waitForFunction(i=>document.querySelector('#sequenceStatus').textContent.includes(`已处理${i}/3组`),i);
    assert(await page.locator('#sequenceSpatialChart circle').count()===i,'group position lost');
    if(i===2){
      assert(await page.locator('#sequenceSpatialChart [data-line-basis="local_provisional"]').count()===1,'initial provisional line absent');
      const snap=await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'));firstLine=snap.spatialLineSnapshot?.edges;
    }
  }
  await page.waitForFunction(async()=>{const s=await WorkspaceCache.get('mixed-workspace');return s.spatialLineChanges?.some(c=>c.removed.length>0);});
  const snap=await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'));
  const current=Object.values(snap.spatialLineSnapshot.edges);assert(current.length===1,'bad line count after review');
  assert(!firstLine[JSON.stringify([current[0].from,current[0].to])],'old line was not replaced');
  assert((await page.locator('#sequenceSpatialLinkSummary').textContent()).includes('不是模型归属'),'local line masquerades as Agent');
  const sid=await page.locator('#sessionId').inputValue(),url=base+'/api/incremental/mixed-sessions/'+sid;
  const root=await(await page.request.get(url)).json();assert(root.calls===2&&Object.keys(root.tracks).length===0,'display changed paid calls/confirmed state');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#sequenceSpatialChart [data-line-basis="local_provisional"]').length===1);
  const saved=await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'));assert(saved.spatialLineChanges.some(c=>c.removed.length),'revision record not restored');
  await page.locator('#sequenceAutoConnect').uncheck();assert(await page.locator('#sequenceSpatialChart [data-spatial-line]').count()===0,'toggle cannot disable local hypotheses');
  await page.locator('#sequenceAutoConnect').check();assert(await page.locator('#sequenceSpatialChart [data-line-basis="local_provisional"]').count()===1,'toggle lost hypothesis');
  await page.locator('#sequenceSpatialLinkChanges').locator('..').evaluate(n=>n.open=true);
  await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/auto-line-revisions.png'});
  assert(!errors.length,errors.join('\n'));
  return {initialProvisionalLine:true,newGroupRewired:true,revisionPersisted:true,disableToggle:true,rawGroupsPreserved:3,backendUnchanged:true,fakeCalls:2,realCalls:0};
}
