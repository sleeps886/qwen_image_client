// Exact real saved results, replayed via GET only. No models, no production writes.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);},errors=[];let posts=0,fixture;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/incremental/mixed-sessions/**',async route=>{
    if(route.request().method()!=='GET'){posts++;await route.abort();return;}
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/execution'))return route.fulfill({json:{}});
    if(path.includes('/runs/'))return route.fulfill({json:fixture.run});
    return route.fulfill({json:fixture.state});
  });
  await page.setViewportSize({width:1750,height:1100});await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const results=[];
  for(const kind of ['failed','complete']){
    fixture=await(await page.request.get(base+'/api/test-saved-display/'+kind)).json();
    await page.evaluate(async({state,dataset})=>{
      const snapshot={state,dataset,conversationId:'read-only-display-replay',selected:[],groups:[''],messages:[],executionHistory:[],
        useSequential:true,executionMode:'batch-snapshot-v1',spatialCards:true};
      await WorkspaceCache.set('mixed-workspace',snapshot);
    },fixture);
    await page.goto(base+'/incremental');
    await page.waitForFunction(cursor=>document.querySelector('#sequenceStatus').textContent.includes(`已处理${cursor}/483组`),fixture.run.cursor,{timeout:60000});
    const units=fixture.run.working_view.units.length,markers=await page.locator('#sequenceSpatialChart circle[data-unit-id]').count();
    const details=await page.locator('#sequenceSpatialGroupList>details').count();
    assert(details===units,`${kind}: group details dropped ${details}/${units}`);
    assert(!(await page.locator('#sequenceSpatialCards').isChecked()),'old run silently upgraded');
    const notice=await page.locator('#sequenceCompletionNotice').textContent();
    assert(notice.includes('未启用联合空间协议'),'old-protocol warning missing');
    if(kind==='failed'){
      assert(notice.includes('不是图漏画')&&notice.includes('有效返回30组')&&notice.includes('结果未知20组'),'wrong partial-run diagnosis');
      assert(markers===342&&details===350,'exact partial results not rendered');
    }else assert(markers===475&&details===483,'complete saved result cannot fully display');
    await page.screenshot({path:`output/supplied-zip-test/current-incomplete-display/browser-${kind}.png`});
    results.push({kind,processed:fixture.run.cursor,details,markers});
  }
  assert(posts===0&&errors.length===0,'unexpected POST or runtime errors: '+errors.join(';'));
  return {results,modelRequests:0,postRequests:posts,runtimeErrors:errors};
}
