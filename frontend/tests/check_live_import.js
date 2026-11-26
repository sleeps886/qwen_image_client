async page=>{
  const base='http://127.0.0.1:8892',assert=(v,m)=>{if(!v)throw Error(m);};
  await page.goto(base+'/live?diagnostic=1');await page.locator('#create').click();await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效0点'));
  const initial=(await (await page.request.get(base+'/api/test/live-fake')).json()).calls;
  const candidates=[{candidate_id:'h',t:1,platform_id:'A',array:'hull',bearing_deg:30},{candidate_id:'t',t:1,platform_id:'A',array:'tow',bearing_deg:32},{candidate_id:'s',t:2,platform_id:'A',array:'hull',bearing_deg:33}];
  await page.locator('#files').setInputFiles({name:'observations.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({candidates}))});await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('有效3点'));
  await page.locator('#sampleMode').selectOption('synchronous_groups');await page.locator('#sampleCount').fill('1');await page.locator('#sample').click();assert((await page.locator('#selection').textContent()).includes('已选2个'),'group units incorrect');
  await page.locator('#rangeStart').fill('1');await page.locator('#rangeEnd').fill('2');await page.locator('#selectRange').click();assert((await page.locator('#selection').textContent()).includes('已选3个'),'window selection incorrect');
  const platform={platforms:[{name:'A',start_ll:[30.0623,122.0415],heading_deg:30,speed_kn:0,hull_m:[0,0],tow_m:[-150,0],color:'#ffd500'}]};
  await page.locator('#platformFile').setInputFiles({name:'platform.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(platform))});await page.waitForFunction(()=>document.querySelectorAll('#platformCards input').length===10);
  assert(await page.locator('#platformCards input').nth(1).inputValue()==='122.0415','longitude/latitude reversed');assert(await page.locator('#platformCards input[type=color]').inputValue()==='#ffd500','display color absent');assert(await page.locator('#platformCards input[type=color]').isDisabled(),'color not read-only');
  await page.locator('#prepare').click();await page.waitForFunction(()=>document.querySelector('#preview').textContent.includes('snapshot-'));
  const current=(await (await page.request.get(base+'/api/test/live-fake')).json()).calls;assert(current===initial,'file/config/selection calls model');
  const exported=await (await page.request.get(base+await page.locator('#export').getAttribute('href'))).json();assert(exported.observations.length===3,'original points changed');assert(exported.versions.configuration.length===2,'configuration versions lost');
  const response=await page.request.post(base+'/api/incremental/live-sessions/migrate',{data:{timeline_id:'explicit-legacy-clock',dataset:{candidates},legacy_history:{prediction_ledger:[{old:true}]}}});assert(response.ok(),'explicit migration failed');assert((await response.json()).legacy_unchanged,'migration modifies legacy');
  return {fileAppend:true,optionalGroupSampling:true,windowSelection:true,platformCards:true,configurationVersions:true,explicitMigration:true,realModelCalls:0};
}
