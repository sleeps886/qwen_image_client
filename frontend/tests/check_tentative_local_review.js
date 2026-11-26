// Controlled fake reasoning, not model accuracy. Real model calls prohibited.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
  await page.setViewportSize({width:1700,height:1000});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const data={candidates:[40,60,61].flatMap((b,i)=>['hull','tow'].map(array=>({candidate_id:array+i,t:i*10,platform_id:'A',array,bearing_deg:b,quality:'fitted',sigma_deg:1})))};
  await page.locator('#pointFileImport').setInputFiles({name:'local-review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留6点'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('3');await page.locator('#samplePoints').click();
  await page.locator('#chatQuestion').fill('TEST_ONLY_TENTATIVE_REVIEW');await page.locator('#chatQuestion').dispatchEvent('input');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备3组'));
  const sid=await page.locator('#sessionId').inputValue(),stateUrl=base+'/api/incremental/mixed-sessions/'+sid;
  for(let i=1;i<=3;i++){
    await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
    await page.waitForFunction(i=>document.querySelector('#sequenceStatus').textContent.includes(`已处理${i}/3组`),i);
    assert(await page.locator('#sequenceWorkingChart circle').count()===i,'current group missing');
    if(i===1)assert((await(await page.request.get(stateUrl)).json()).calls===0,'seed model call');
    if(i===2){
      assert(await page.locator('#sequenceWorkingChart polyline[data-edge-status="low_confidence"]').count()===1,'sparse append not drawn');
      await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/v11-sparse-append.png'});
    }
  }
  const state=await(await page.request.get(stateUrl)).json(),run=await(await page.request.get(stateUrl+'/runs/'+state.active_sequential_run)).json();
  assert(run.spec.continuation_policy==='tentative-local-v1','wrong policy');
  assert(run.steps[2].context.continuation_policy.mode==='local_review','doubt not targeted');
  assert(run.steps[2].revision.withdrawn_edges.length===2,'old hull/tow links not withdrawn');
  assert(run.working_view.continuation_graph.edges.every(e=>!e.from_id.endsWith('0')),'old false edge remains');
  assert(await page.locator('#sequenceWorkingChart polyline').count()===1,'corrected hull line missing');
  assert((await page.locator('#sequenceRevisionList').textContent()).includes('撤回旧边2条'),'revision invisible');
  assert(state.calls===2&&Object.keys(state.tracks).length===0,'cost or confirmation bypass');
  assert(await page.locator('#selectedSourceChart polyline').count()===0,'source preview invents identity');
  await page.locator('#sequenceWorkingRevisions').evaluate(n=>n.open=true);
  await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/v11-local-revision.png'});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理3/3组'));
  assert(await page.locator('#sequenceWorkingChart polyline').count()===1,'revised graph lost after refresh');
  return {seedFree:true,sparseTentativeLine:true,localRevision:true,withdrawnEdges:2,rootUnchanged:true,reloadRestored:true,fakeCalls:2,realModelCalls:0};
}
