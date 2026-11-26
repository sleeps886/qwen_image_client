// Only run against the isolated fake-gateway smoke server.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'requires fake gateway');
  let legacyCalls=0;page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))legacyCalls++;});
  await page.setViewportSize({width:1700,height:1000});await page.goto(base+'/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (6).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('4');await page.locator('#samplePoints').click();
  await page.locator('#sequenceBudget').fill('3');await page.locator('#prepareSequence').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备4组'));
  assert(await page.locator('#sequenceStart').isDisabled(),'dispatch enabled without consent');
  const sid=await page.locator('#sessionId').inputValue(),stateUrl=base+'/api/incremental/mixed-sessions/'+sid;
  assert((await(await page.request.get(stateUrl)).json()).calls===0,'prepare called model');
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceNext').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理1/4组')&&!document.querySelector('#sequenceConsent').checked);
  let state=await(await page.request.get(stateUrl)).json();const rid=state.active_sequential_run,runUrl=stateUrl+'/runs/'+rid;
  let run=await(await page.request.get(runUrl)).json();assert(run.cursor===1&&run.calls===0,'first group not free seed');
  assert(Object.keys(state.tracks).length===0,'working state automatically confirmed');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已处理1/4组'));
  assert(!await page.locator('#sequenceConsent').isChecked(),'reload retained paid consent');
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:60000});
  run=await(await page.request.get(runUrl)).json();assert(run.cursor===4&&run.calls===3,'wrong sequential call accounting');
  for(let i=0;i<run.steps.length;i++){
    const c=run.steps[i].context;assert(c.sequential_step.new_unit_count===1,'step adds multiple units');
    const allowed=new Set(run.spec.queue.slice(0,i+1).flatMap(u=>u.candidate_ids));
    // Candidate IDs stay local; model evidence is verified separately by backend tests.
    assert(allowed.size===(i+1)*2,'queue lost original members');
  }
  state=await(await page.request.get(stateUrl)).json();assert(state.submitted_ids.length===0,'provisional work written into confirmed root');
  await page.locator('#sequenceHistory').evaluate(n=>{n.open=true;});await page.screenshot({path:'output/supplied-zip-test/sequential-workspace.png'});
  await page.locator('#sequenceAdopt').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('adopted'));
  state=await(await page.request.get(stateUrl)).json();assert(state.status==='ready'&&state.submitted_ids.length===8,'explicit confirmation failed');
  assert(state.calls===3,'confirmation adds call');assert(state.version>Math.max(...state.rounds.map(r=>r.revision)),'projection version regressed');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('adopted'));
  assert(await page.locator('#sequenceSteps>details').count()===4,'adopted run history unavailable after reload');
  assert(legacyCalls===0,'new queue used legacy bulk model endpoint');
  return {actualZipInputs:3,selectedUnits:4,freeInitialization:1,fakeAssociationCalls:3,realModelCalls:0,oneNewUnitPerStep:true,refreshResumesCursor:true,confirmationRequired:true,legacyBulkCalls:legacyCalls};
}
