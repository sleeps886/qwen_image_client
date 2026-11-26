// Explicitly authorized real production UI smoke test. Prepare only: no inference.
async page=>{
  const base='http://127.0.0.1:8870',assert=(v,m)=>{if(!v)throw Error(m);};
  await page.setViewportSize({width:1750,height:1100});await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#openNewConversation').disabled);
  await page.locator('#openNewConversation').click();
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  assert(await page.locator('#sequenceSpatialCards').isChecked(),'new queue does not default to joint spatial protocol');
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (2).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'),{},{timeout:30000});
  await page.locator('#samplePointCount').fill('50');await page.locator('#samplePoints').click();
  await page.locator('#sequenceBudget').fill('65');await page.locator('#sequenceBudget').dispatchEvent('change');
  await page.locator('#sequenceRetryBudget').fill('14');await page.locator('#sequenceRetryBudget').dispatchEvent('change');
  await page.locator('#chatQuestion').fill('实际工作台真实测试：50组联合空间判断；不固定目标数，保留镜像假设，不读取真值。');
  await page.locator('#chatQuestion').dispatchEvent('input');await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备50组'),{},{timeout:30000});
  const sid=await page.locator('#sessionId').inputValue();
  const root=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();
  assert(root.calls===0,'free preparation spent calls');
  return {sessionId:sid,importedObservations:966,selectedGroups:50,newProtocolDefault:true,callsBeforeConsent:root.calls,
    preview:await page.locator('#sequenceStatus').textContent()};
}
