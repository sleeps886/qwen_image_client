async page=>{
  const assert=(v,m)=>{if(!v)throw Error(m);};
  const base='http://127.0.0.1:8891';let modelPosts=0;
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('mixed-sessions'))modelPosts++;});
  await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('已就绪'));
  assert(await page.locator('#selectAllPoints').isDisabled(),'enabled without dataset');
  const data=await(await page.request.get(base+'/api/test-mixed-fixture')).json();
  await page.locator('#pointFileImport').setInputFiles({name:'selection.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>!document.querySelector('#selectAllPoints').disabled);
  const count=()=>page.locator('#selectedPoints>div').count();
  await page.locator('#clearSelection').click();
  await page.locator('#targetTrackSelect').selectOption('A038');
  await page.locator('#selectAllPoints').click();assert(await count()===10,'group select failed');
  await page.locator('#selectAllPoints').click();assert(await count()===10,'duplicate selection');
  await page.locator('#targetTrackSelect').selectOption('A031');
  assert(await count()===10,'switch cleared selection');
  await page.locator('#selectAllPoints').click();assert(await count()===20,'did not retain other group');
  await page.locator('#clearSelection').click();assert(await count()===0,'clear failed');
  await page.locator('#targetTrackSelect').selectOption('');
  await page.locator('#selectAllPoints').click();assert(await count()===20,'all candidates failed');
  await page.waitForFunction(async()=> (await WorkspaceCache.get('mixed-workspace'))?.selected.length===20);
  await page.goto(base+'/incremental');
  await page.waitForFunction(()=>document.querySelectorAll('#selectedPoints>div').length===20);
  assert(!await page.locator('#chatConsent').isChecked(),'model authorised');
  assert(modelPosts===0,'selection called backend session/model');
  return {groupAll:true,allCandidates:true,deduplicated:true,retainsOtherGroups:true,clear:true,reload:true,modelCalls:0};
}
