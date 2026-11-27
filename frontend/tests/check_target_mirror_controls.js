// Replay of user's actual 100-group result. No inference/production writes.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);},errors=[];let posts=0,fixture;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/incremental/mixed-sessions/**',async route=>{
    if(route.request().method()!=='GET'){posts++;await route.abort();return;}
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/execution'))return route.fulfill({json:{}});
    return route.fulfill({json:path.includes('/runs/')?fixture.run:fixture.state});
  });
  await page.setViewportSize({width:1750,height:1150});await page.goto(base+'/incremental?empty=1');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  fixture=await(await page.request.get(base+'/api/test-saved-display/mirror100')).json();
  const snapshot={state:fixture.state,dataset:fixture.dataset,conversationId:'mirror-only-read-test',selected:[],groups:[''],messages:[],executionHistory:[],useSequential:true,executionMode:'batch-snapshot-v1',spatialCards:true};
  await page.evaluate(async s=>{await WorkspaceCache.set('mixed-workspace',s);},snapshot);
  await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelectorAll('#sequenceTargetMirrorList select').length===3);
  const blue='B000003',green='B000001',yellow='B000002';
  const dots=target=>page.locator(`#sequenceSpatialChart circle[data-target-id="${target}"][data-mirror-preview="false"]`);
  const positions=target=>dots(target).evaluateAll(ns=>ns.map(n=>[n.dataset.unitId,Number(n.dataset.worldX),Number(n.dataset.worldY),n.dataset.branch,n.getAttribute('stroke')]));
  const before={blue:await positions(blue),green:await positions(green),yellow:await positions(yellow)};
  const expected=Object.values(fixture.run.working_view.spatial_preview.model_branch_choices).filter(Boolean).length;
  const select=()=>page.locator(`#sequenceTargetMirrorList select[data-target-id="${blue}"]`);
  await select().selectOption('mirror');
  await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('人工整轨镜像预览'));
  const after=await positions(blue);
  assert(JSON.stringify(after)!==JSON.stringify(before.blue),'blue did not mirror');
  assert(JSON.stringify(await positions(green))===JSON.stringify(before.green),'green changed');
  assert(JSON.stringify(await positions(yellow))===JSON.stringify(before.yellow),'yellow changed');
  const nav=fixture.dataset.platform_config.platforms[0],origin=fixture.run.working_view.spatial_preview.origin_lat_lon,heading=nav.heading_deg*Math.PI/180;
  const fx=Math.sin(heading),fy=Math.cos(heading),east=6371000*Math.cos(origin[0]*Math.PI/180)*(nav.start_ll[1]-origin[1])*Math.PI/180,north=6371000*(nav.start_ll[0]-origin[0])*Math.PI/180;
  const times=new Map(fixture.run.working_view.spatial_preview.tracks.flatMap(t=>t.points.map(p=>[p.unit_id,p.t])));
  for(let i=0;i<before.blue.length;i++){
    const [uid,x,y]=before.blue[i],travel=nav.speed_kn*1852/3600*times.get(uid),pos=o=>[east+(travel+o[0])*fx-o[1]*fy,north+(travel+o[0])*fy+o[1]*fx];
    const h=pos(nav.hull_m),t=pos(nav.tow_m);let dx=t[0]-h[0],dy=t[1]-h[1],span=Math.hypot(dx,dy);dx/=span;dy/=span;
    const dot=(x-h[0])*dx+(y-h[1])*dy,ex=h[0]+2*dot*dx-(x-h[0]),ey=h[1]+2*dot*dy-(y-h[1]);
    assert(Math.hypot(after[i][1]-ex,after[i][2]-ey)<1e-4,'not a baseline reflection');
    assert(after[i][4]===before.blue[i][4],'target color changed');
  }
  assert(await page.locator('#sequenceSpatialChart circle[data-unit-id]').count()===expected,'mirror changed primary count');
  const raw=await page.locator('#sequenceSpatialGroupList pre').allTextContents();
  assert(raw.some(t=>t.includes('manual_target_mirror_preview')&&t.includes('original_model_candidate')),'manual preview/model original not distinguished');
  await page.screenshot({path:'output/supplied-zip-test/target-mirror-blue-flipped.png'});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'));
  assert(await select().inputValue()==='mirror','mirror selection lost on refresh');
  assert(JSON.stringify(await positions(blue))===JSON.stringify(after),'positions changed on refresh');
  await select().selectOption('both');
  const extra=page.locator(`#sequenceSpatialChart circle[data-target-id="${blue}"][data-mirror-preview="true"]`);
  assert(await extra.count()===before.blue.length,'both mode missing reflected candidates');
  assert(JSON.stringify(await positions(blue))===JSON.stringify(before.blue),'both mode changed original branch');
  assert(await page.locator(`#sequenceSpatialChart polyline[data-target-id="${blue}"][data-mirror-preview="true"]`).count()>0,'ghost line missing');
  assert(await page.locator('#sequenceSpatialGroupList>details').count()===fixture.run.working_view.units.length,'both duplicates actual groups');
  await page.screenshot({path:'output/supplied-zip-test/target-mirror-both.png'});
  await select().selectOption('model');assert(JSON.stringify(await positions(blue))===JSON.stringify(before.blue),'reset did not restore model');
  assert(await page.locator('#sequenceSpatialChart circle[data-mirror-preview="true"]').count()===0,'ghost remained on reset');
  // Selection is pinned to cursor: new model snapshot must not silently inherit it.
  await select().selectOption('mirror');fixture.run.cursor++;
  await page.locator('#sequenceRefresh').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理101/'));
  assert(await select().inputValue()==='model','old choice silently applied to changed model snapshot');
  assert((await page.locator('#sequenceTargetMirrorList').textContent()).includes('新批次已更新'),'stale manual choice warning missing');
  fixture.run.cursor--;await page.locator('#sequenceRefresh').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理100/'));
  const exported=await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'));
  assert(exported.targetMirrorHistory?.some(x=>x.target_id==='B000003'&&x.to==='mirror'&&x.model_result_changed===false),'manual audit not saved');
  assert(posts===0&&errors.length===0,'unexpected POST/runtime error '+errors.join(';'));
  return {actualSavedGroups:fixture.run.cursor,primaryMarkers:expected,blueMirrorPoints:after.length,otherTargetsUnchanged:true,
    restoredOriginal:true,bothCandidates:true,refreshStable:true,staleSnapshotGuard:true,postRequests:posts,modelCalls:0,runtimeErrors:errors};
}
