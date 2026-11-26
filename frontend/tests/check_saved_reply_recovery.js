// Read-only UI verification of a repaired saved response. Abort any POST.
async (page) => {
  let posts=0;await page.route('**/api/**',async route=>{
    if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();
  });
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready');
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#sessionId').fill('20261124_132352_55ea54bd');await page.locator('#reload').click();
  await page.waitForFunction(()=>document.getElementById('chatState').textContent==='单次预测已完成');
  if(await page.locator('.assistant svg polyline[stroke="#2166d1"]').count()!==1)throw new Error('recovered forecast chart missing');
  await page.waitForFunction(()=>document.getElementById('executionCurrent').textContent.includes('未再次调用模型'));
  if(posts)throw new Error('recovery display attempted POST');
  await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.assistant svg').length===1);
  if(posts)throw new Error('refresh attempted POST');
  return {savedReplyRecovered:true,predictionChart:true,singleModeStopped:true,refreshRestored:true,postRequests:0};
}
