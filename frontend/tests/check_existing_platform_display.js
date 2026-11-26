// Read-only existing real session. Any POST is blocked; no inference is replayed.
async (page) => {
  let posts=0;await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();});
  await page.setViewportSize({width:1440,height:1000});
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready');
  const origin=new URL(page.url()).origin,path='/api/incremental/sessions/20261124_141703_cec5feef';
  const before=await(await page.request.get(origin+path)).json();
  await page.getByRole('button',{name:'高级设置',exact:true}).click();await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#sessionId').fill('20261124_141703_cec5feef');await page.locator('#reload').click();
  await page.waitForFunction(()=>document.querySelectorAll('.platform-motion-figure').length>0);
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  const card=page.locator('.platform-motion-figure').first(),text=await card.textContent();
  for(const expected of ['30.062300','122.041500','30.0°','0.00 节','0.0 米','非实时GPS'])if(!text.includes(expected))throw new Error('Missing platform field: '+expected);
  await page.locator('.platform-analysis-details').evaluate(el=>el.open=true);
  const after=await(await page.request.get(origin+path)).json();
  if(before.calls!==after.calls||posts)throw new Error('display caused a model call');
  await page.locator('#chatMessages').evaluate(el=>{el.style.scrollBehavior='auto';const card=el.querySelector('.platform-motion-figure');el.scrollTop+=card.getBoundingClientRect().top-el.getBoundingClientRect().top;});
  await page.screenshot({path:'work/existing-platform-display.png'});
  return {oldFirstRoundBackfilled:true,coordinates:true,stationaryConfiguration:true,modelCallsAdded:0,postRequests:posts};
}
