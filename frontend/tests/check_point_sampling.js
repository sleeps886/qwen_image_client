async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let modelPosts=0;
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('mixed-sessions'))modelPosts++;});
  await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  assert(await page.locator('#samplePoints').isDisabled(),'sampling enabled without data');
  const candidates=Array.from({length:1224},(_,i)=>({candidate_id:'O'+String(i).padStart(6,'0'),platform_id:'P',array:i%2?'tow':'hull',t:Math.floor(i/4)*2,bearing_deg:30+(i%4)*20}));
  const catalog=['A','B'].map((target_id,g)=>({target_id,candidate_ids:candidates.filter((p,i)=>Math.floor(i%4/2)===g).map(p=>p.candidate_id)}));
  await page.locator('#pointFileImport').setInputFiles({name:'random-spacing.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({candidates,target_catalog:catalog}))});
  await page.waitForFunction(()=>!document.querySelector('#samplePoints').disabled);
  const chosen=()=>page.locator('#selectedPoints>div>span').evaluateAll(nodes=>nodes.map(n=>n.textContent.split(' · ')[1]));
  const sample=async n=>{await page.locator('#samplePointCount').fill(String(n));await page.locator('#samplePoints').click();};
  await page.locator('#selectAllPoints').click();assert((await chosen()).length===1224,'setup all selection failed');
  await sample(100);const first=await chosen();
  assert(first.length===100&&new Set(first).size===100,'not exactly 100 unique points');
  assert(first.includes('O000000')&&first.includes('O001223'),'time endpoints not preserved');
  await sample(100);assert(JSON.stringify(await chosen())!==JSON.stringify(first),'resampling did not vary');
  const before=await chosen(),bIds=new Set(catalog[1].candidate_ids),outside=before.filter(id=>bIds.has(id));
  await page.locator('#targetTrackSelect').selectOption('A');await sample(40);
  let selected=await chosen();assert(selected.filter(id=>!bIds.has(id)).length===40,'group count incorrect');
  assert(JSON.stringify(selected.filter(id=>bIds.has(id)))===JSON.stringify(outside),'other group selection changed');
  const prior=JSON.stringify(selected);
  for(const invalid of ['0','1.5','','613']){await sample(invalid);assert(JSON.stringify(await chosen())===prior,'invalid input changed selection');}
  assert((await page.locator('#pointSamplingStatus').textContent()).includes('612'),'missing available count error');
  await page.locator('#targetTrackSelect').selectOption('');await sample(100);
  await page.waitForFunction(async()=>{const s=await WorkspaceCache.get('mixed-workspace');return s?.selected.length===100&&s.sampleCount==='100';});
  const files=await page.evaluate(()=>PointFiles.list());
  assert(files.find(f=>f.name==='random-spacing.json').data.candidates.length===1224,'original data was cut down');
  await page.goto(base+'/incremental');
  await page.waitForFunction(()=>document.querySelectorAll('#selectedPoints>div').length===100);
  assert(await page.locator('#samplePointCount').inputValue()==='100','count not restored');
  assert(!await page.locator('#chatConsent').isChecked(),'sampling authorised a model');
  assert(modelPosts===0,'sampling called backend/model');
  await page.locator('.point-sampling').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/point-sampling.png'});
  return {exact100:true,randomVariation:true,groupScope:true,retainsOtherGroups:true,invalidInputSafe:true,original1224Preserved:true,reload:true,modelCalls:0};
}
