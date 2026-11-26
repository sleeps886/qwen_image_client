// Completed real UI test; read-only checks and refresh, never dispatch again.
async page=>{
  const assert=(v,m)=>{if(!v)throw Error(m);};
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:60000});
  const sid=await page.locator('#sessionId').inputValue(),base='http://127.0.0.1:8870/api/incremental/mixed-sessions/'+sid;
  const state=await(await page.request.get(base)).json(),rid=state.active_sequential_run;
  const run=await(await page.request.get(base+'/runs/'+rid)).json();
  assert(sid==='20261126_193331_33d342b4','not the owned test session');
  assert(run.cursor===50&&run.calls<=65&&run.status==='completed_awaiting_review','test incomplete');
  assert(run.spec.spatial_association_contract==='model-selected-spatial-cards-v1','wrong protocol');
  const expected=Object.values(run.working_view.spatial_preview.model_branch_choices).filter(Boolean).length;
  assert(await page.locator('#sequenceSpatialChart circle[data-unit-id]').count()===expected,'position markers missing');
  assert(await page.locator('#sequenceSpatialGroupList>details').count()===50,'group details incomplete');
  assert(await page.locator('#sequenceMirrorSide').isDisabled(),'model branches can be overwritten');
  const before=await page.locator('#sequenceSpatialChart circle[data-unit-id]').evaluateAll(ns=>ns.map(n=>[n.dataset.unitId,n.getAttribute('cx'),n.getAttribute('cy')]));
  let posts=0;page.on('request',r=>{if(r.method()==='POST')posts++;});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'));
  const after=await page.locator('#sequenceSpatialChart circle[data-unit-id]').evaluateAll(ns=>ns.map(n=>[n.dataset.unitId,n.getAttribute('cx'),n.getAttribute('cy')]));
  assert(JSON.stringify(before)===JSON.stringify(after),'positions changed after refresh');
  const latest=await(await page.request.get(base)).json();
  assert(latest.calls===state.calls&&posts===0,'refresh caused model dispatch');
  assert(Object.keys(latest.tracks).length===0,'test result adopted automatically');
  await page.screenshot({path:'output/supplied-zip-test/actual-workbench-real-50.png'});
  return {session:sid,run:rid,realRequests:run.calls,groups:run.cursor,markers:expected,
    lines:await page.locator('#sequenceSpatialChart [data-spatial-line]').count(),
    refreshStable:true,refreshModelCalls:0,adopted:false,status:run.status};
}
