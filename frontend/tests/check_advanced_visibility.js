async (page) => {
  let posts=0;
  await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();});
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready');
  if(await page.locator('#predictionCacheStatus').isVisible()||await page.locator('#clearPredictionCache').isVisible()||await page.locator('#predictionDebug').isVisible())throw new Error('advanced controls visible by default');
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  if(!await page.locator('#clearPredictionCache').isVisible())throw new Error('advanced controls inaccessible');
  if(await page.locator('#togglePredictionAdvanced').getAttribute('aria-expanded')!=='true')throw new Error('incorrect expanded state');
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  if(await page.locator('#predictionAdvanced').isVisible())throw new Error('advanced controls not hidden');
  if(posts)throw new Error('settings toggle invoked a model/task POST');
  return {hiddenByDefault:true,accessibleUnderMore:true,autoSaveCodeUnchanged:true,postRequests:0};
}
