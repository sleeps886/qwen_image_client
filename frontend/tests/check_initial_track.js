// Uses a local saved Gaussian batch. Blocks all POSTs: initial display is free.
async (page) => {
  const origin=new URL(page.url()).origin;let posts=0;
  await page.route('**/api/**',async route=>{
    if(route.request().method()==='POST'){posts++;await route.abort();return;}
    await route.continue();
  });
  const assert=(condition,message)=>{if(!condition)throw new Error(message);};
  await page.goto(origin+'/incremental?source_result=20261124_105150_802e63a7');
  await page.waitForFunction(()=>document.getElementById('predictionSummary').textContent.includes('初始3点轨迹已显示'));
  assert(await page.locator('#predictionChart polyline').count()===1,'initial line not drawn');
  assert(await page.locator('#predictionChart polyline[stroke-dasharray]').count()===0,'initial line mislabeled as future prediction');
  assert(await page.locator('#sessionId').inputValue()==='','initial display created a task');
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false); await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#suggestSeeds').click();
  const history=JSON.parse(await page.locator('#chatInput').inputValue()).point_data.history;
  assert(history.length===3,'incorrect initial point count');
  assert(history.at(-1).t===30,'seed selector used future times');
  assert(history.every((p,i)=>i===0||Math.abs(p.bearing_deg-history[i-1].bearing_deg)<20),'first-candidate jump not fixed');
  const original=history.map(p=>p.candidate_id).join(',');
  await page.locator('#seeds').fill('missing');
  assert(await page.locator('#predictionChart polyline').count()===0,'invalid IDs left stale line');
  await page.locator('#seeds').fill(original);
  assert(await page.locator('#predictionChart polyline').count()===1,'valid edited seeds not displayed');
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('predictionSummary').textContent.includes('初始3点轨迹已显示'));
  assert(posts===0,'initial preview attempted model/task POST');
  return {initialTrackDisplayed:true,modelCalls:0,taskCreated:false,seedJumpAvoided:true,invalidSeedsCleared:true,refreshRestored:true};
}
