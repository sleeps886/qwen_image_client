async page=>{
  const assert=(v,m)=>{if(!v)throw Error(m);};let selectionRequests=0,watch=false;
  page.on('request',r=>{if(watch&&r.url().includes('/api/'))selectionRequests++;});
  await page.goto('http://127.0.0.1:8891/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (6).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  const original=await page.evaluate(async()=>JSON.stringify((await PointFiles.list())[0].data));watch=true;
  await page.locator('#selectAllPoints').click();
  await page.waitForFunction(()=>document.querySelectorAll('#selectedSourceChart circle').length===483);
  assert((await page.locator('#selectedSourceCount').textContent()).includes('966条观测'),'selected count incomplete');
  const colors=await page.locator('#selectedSourceChart circle').evaluateAll(nodes=>{
    const groups={};for(const n of nodes)(groups[n.dataset.sourceKey]??=new Set()).add(n.getAttribute('stroke'));
    return Object.fromEntries(Object.entries(groups).map(([key,set])=>[key,[...set]]));
  });
  assert(Object.keys(colors).length===3,'missing dataset color');assert(Object.values(colors).every(c=>c.length===1),'same source varies color');
  assert(new Set(Object.values(colors).flat()).size===3,'sources indistinguishable');
  assert(await page.locator('#selectedSourceChart polyline, #selectedSourceChart path').count()===0,'source preview must be scatter-only');
  assert(await page.locator('#predictionChart polyline').count()===0,'display links leaked into main analysis preview');
  await page.locator('#clearSelection').click();assert(await page.locator('#selectedSourceChart circle').count()===0,'clear leaves source dots');
  await page.locator('#samplePointMode').selectOption('points');await page.locator('#samplePointCount').fill('25');await page.locator('#samplePoints').click();
  assert(await page.locator('#selectedSourceChart circle').count()<=25,'sample not reflected');
  await page.locator('#selectedPoints button').first().click();assert(await page.locator('#selectedSourceChart circle').count()<=24,'remove not reflected');
  await page.locator('#selectedSourceChart circle').first().hover();
  await page.locator('#selectedSourcePreview').screenshot({path:'output/supplied-zip-test/selected-source-preview.png'});
  assert(original===await page.evaluate(async()=>JSON.stringify((await PointFiles.list())[0].data)),'preview changed raw file');
  const saved=await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'));
  assert(!JSON.stringify(saved.dataset).includes('sourceColor'),'preview metadata persisted into input');
  assert(selectionRequests===0,'selection preview calls API');
  watch=false;
  await page.reload();await page.waitForFunction(()=>document.querySelector('#selectedSourceCount').textContent.includes('24条观测'));
  const matching=await page.locator('#selectedSourceChart circle').evaluateAll(nodes=>nodes.every(n=>n.dataset.sourceKey==='unknown'||!!n.getAttribute('stroke')));
  assert(matching,'reload loses colors');
  return {sameDatasetSameColor:true,differentSourcesThreeColors:true,all966Shown:true,selectionClearRemoveSync:true,restored24Selected:true,rawInputUnchanged:true,selectionApiRequests:selectionRequests};
}
