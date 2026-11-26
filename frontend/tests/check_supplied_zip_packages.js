// Local actual-file UI regression. No model dispatch is authorized.
async page=>{
  const assert=(v,m)=>{if(!v)throw Error(m);};let dispatches=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))dispatches++;});
  await page.goto('http://127.0.0.1:8891/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (6).zip`));
  await page.waitForFunction(()=>document.querySelectorAll('.point-file').length===1&&document.querySelector('#batchSource').textContent.includes('已保留966点'));
  const records=await page.evaluate(()=>PointFiles.list()),original=JSON.stringify(records[0].data);
  assert(records[0].data.candidates.length===966,'actual ZIP import lost points');
  assert(await page.locator('#targetTrackSelect option').count()===4,'three source browse groups missing');
  await page.locator('#samplePointCount').fill('20');await page.locator('#samplePoints').click();
  assert((await page.locator('#pointSamplingStatus').textContent()).includes('20'),'20-point sample failed');
  assert(JSON.stringify((await page.evaluate(()=>PointFiles.list()))[0].data)===original,'sampling mutated original file');
  await page.screenshot({path:'output/supplied-zip-test/workbench-imported.png',fullPage:true});
  await page.evaluate(()=>window.confirm=()=>true);
  await page.locator('.conversation-delete').first().click();
  await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===0);
  assert(await page.locator('.point-file').count()===1,'deleting session removed actual imported file');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.point-file').length===1&&!document.querySelector('#pointFileImport').disabled);
  assert(JSON.stringify((await page.evaluate(()=>PointFiles.list()))[0].data)===original,'reload changed file');
  const pending=page.waitForEvent('download');await page.locator('.point-file').getByRole('button',{name:'导出全部点位',exact:true}).click();
  const download=await pending;await download.saveAs('output/supplied-zip-test/ui-export.json');
  await page.locator('.point-file').getByRole('button',{name:'打开',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留966点'));
  assert(dispatches===0,'unexpected model dispatch');
  return {actualZipCount:3,importedPoints:966,sourceBrowseGroups:3,sampledPairs:20,sampledRawObservations:40,samplingPreservesOriginal:true,deleteSessionKeepsFile:true,reloadKeepsFile:true,exported:true,reopenedPoints:966,modelDispatches:dispatches};
}

