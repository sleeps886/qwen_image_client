// Existing hull-only real task: read-only fusion must not invent a spatial target route.
async (page) => {
  let posts=0;await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();});
  await page.setViewportSize({width:1500,height:1050});
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready');
  const origin=new URL(page.url()).origin,path='/api/incremental/sessions/20261124_141703_cec5feef';
  const before=await(await page.request.get(origin+path)).json();
  await page.getByRole('button',{name:'高级设置',exact:true}).click();await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#sessionId').fill('20261124_141703_cec5feef');await page.locator('#reload').click();
  await page.waitForFunction(()=>document.querySelectorAll('.spatial-plan-figure').length===1);
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  const plan=before.rounds[0].spatial_plan;
  if(plan.status!=='bearing_only'||plan.paired_observation_count!==0||!plan.computed_on_read)throw new Error('old hull-only task was treated as uniquely localizable');
  if(await page.locator('.spatial-plan-figure polyline[data-spatial-kind="prediction"]').count())throw new Error('invented spatial target forecast');
  if(await page.locator('.spatial-plan-figure polyline[data-spatial-kind="direction_only"]').count()!==2)throw new Error('bearing direction alternatives missing');
  if(!(await page.locator('.spatial-plan-status').textContent()).includes('不足2'))throw new Error('missing-data explanation absent');
  const after=await(await page.request.get(origin+path)).json();
  if(after.calls!==before.calls||posts)throw new Error('read-only planning called model');
  return {combinedView:true,hullOnlyCorrectlyUnobservable:true,mirrorDirections:true,noInventedRange:true,modelCallsAdded:0};
}
