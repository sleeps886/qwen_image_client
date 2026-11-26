// Only run against work/joint_smoke_server.py, which replaces the paid gateway.
async page=>{
  const marker=await page.request.get('http://127.0.0.1:8882/api/test-joint-fake');if(!marker.ok())throw Error('fake gateway required');
  const fixture=JSON.stringify({source_result_id:'20261125_000000_abcdef12',
    platform_config:{platforms:[{name:'A',start_ll:[30.0623,122.0415],heading_deg:30,speed_kn:0,hull_m:[0,0],tow_m:[-150,0]}]},
    candidates:['L','R'].flatMap((tid,index)=>Array.from({length:6},(_,i)=>({candidate_id:tid+i,t:i*10,bearing_deg:(index?110:30)+i,array:'hull',platform_id:'A'}))),
    target_catalog:['L','R'].map(tid=>({target_id:tid,candidate_ids:Array.from({length:6},(_,i)=>tid+i)}))});
  await page.route('**/api/incremental/batches/20261125_000000_abcdef12',r=>r.fulfill({contentType:'application/json',body:fixture}));
  let calls=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/step'))calls++;});
  await page.goto('http://127.0.0.1:8882/joint-prediction?source_result=20261125_000000_abcdef12');
  await page.waitForFunction(()=>document.getElementById('jointSeeds0').value==='L0,L1,L2');
  if(await page.locator('#jointSeeds1').inputValue()!=='R0,R1,R2')throw Error('bad default seeds');
  await page.locator('#jointCreate').click();
  await page.waitForFunction(()=>document.getElementById('jointNotice').textContent.includes('已准备至 30'));
  if(!await page.locator('#jointPlatform').innerText().then(t=>t.includes('30.0623')))throw Error('platform missing');
  await page.locator('#jointConsent').check();await page.locator('#jointSend').click();
  await page.waitForFunction(()=>!document.getElementById('jointReview').hidden);
  if(calls!==1)throw Error('not one joint request');
  const sid=await page.locator('#jointSession').inputValue();
  await page.reload();
  // With a source URL, loading the batch is free; task recovery remains explicit.
  await page.waitForFunction(()=>document.getElementById('jointSeeds0').value==='L0,L1,L2');
  await page.locator('#jointSession').fill(sid);await page.locator('#jointRestore').click();
  await page.waitForFunction(()=>!document.getElementById('jointReview').hidden);
  if(calls!==1)throw Error('reload called model');
  await page.locator('#jointAdopt').click();
  await page.waitForFunction(()=>document.getElementById('jointNotice').textContent.includes('已准备至 40'));
  await page.locator('#jointConsent').check();await page.locator('#jointSend').click();
  await page.waitForFunction(()=>document.getElementById('jointStatus').textContent.includes('调用 2/'));
  if(await page.locator('#jointChart svg').count()!==1)throw Error('multiple canvases');
  const response=await page.request.get('http://127.0.0.1:8882/api/incremental/joint-sessions/'+sid),state=await response.json();
  if(state.tracks.L.history.at(-1).candidate_id!=='L4'||state.tracks.R.history.at(-1).candidate_id!=='R4')throw Error('track mixing');
  await page.screenshot({path:'work/joint-ui-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('mobile overflow');
  if(calls!==2)throw Error('unexpected calls');
  return {jointCalls:calls,independentHistories:true,sharedChart:true,reviewAndRestore:true,mobileFits:true};
}
