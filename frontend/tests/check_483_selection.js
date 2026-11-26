// Actual supplied packages; import/full selection and preflight only. No calls.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'isolated server required');
  await page.setViewportSize({width:1750,height:1100});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (2).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('483');await page.locator('#samplePoints').click();
  const plot=await page.getByRole('region',{name:'已勾选观测的来源颜色预览'}).textContent();
  assert(plot.includes('483组双阵')&&plot.includes('966条观测'),'full selection count wrong');
  const budgetMax=await page.locator('#sequenceBudget').getAttribute('max');
  await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequencePreparationStatus').textContent.includes('最多需要482次'));
  const error=await page.locator('#sequencePreparationStatus').textContent();
  const result={selectedGroups:483,rawObservations:966,budgetMax,error,modelCalls:0};
  await page.screenshot({path:'output/supplied-zip-test/full-483/selection-budget-blocked.png'});
  return result;
}
