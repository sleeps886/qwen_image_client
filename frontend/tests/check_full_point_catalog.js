// Browsing the selected target's points must not change the model's current input.
async (page) => {
  const origin=new URL(page.url()).origin;let posts=0;
  await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();});
  await page.goto(origin+'/incremental?source_result=20261124_105150_802e63a7');
  const batch=await(await page.request.get(origin+'/api/incremental/batches/20261124_105150_802e63a7')).json(),target=batch.target_catalog[0];
  await page.waitForFunction(()=>document.querySelectorAll('#targetTrackSelect option').length>1);
  await page.locator('#targetTrackSelect').selectOption(target.target_id);
  await page.waitForFunction(count=>document.querySelectorAll('#pointDetailSelect option').length===count,target.candidate_ids.length);
  const before=await page.locator('#chatInput').inputValue();
  const future=await page.locator('#pointDetailSelect option').last().getAttribute('value');
  await page.locator('#pointDetailSelect').selectOption(future);
  const details=await page.locator('#pointDetailFields').textContent();
  if(!details.includes(future)||!details.includes('仅点库浏览'))throw new Error('future candidate not browseable or not marked');
  if(await page.locator('#chatInput').inputValue()!==before)throw new Error('browsing modified outgoing model context');
  if(await page.locator('#chatPointTable input').count()!==3)throw new Error('full batch leaked into initial send table');
  if(posts)throw new Error('browsing triggered a POST');
  return {selectedTargetPoints:target.candidate_ids.length,initialSendPoints:3,futureBrowseOnly:true,outgoingContextUnchanged:true,modelCalls:0};
}
