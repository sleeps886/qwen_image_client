// Clipboard is mocked: never writes the user's OS clipboard; fake gateway only.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__copiedContext=text;}}}));
  const data=await(await page.request.get(base+'/api/test-sequential-spatial-dataset')).json();
  await page.setViewportSize({width:1700,height:1100});await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  await page.locator('#pointFileImport').setInputFiles({name:'context-copy.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留10点'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('2');await page.locator('#samplePoints').click();await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备2组'));
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'));
  const sid=await page.locator('#sessionId').inputValue(),rootUrl=base+'/api/incremental/mixed-sessions/'+sid,root=await(await page.request.get(rootUrl)).json(),rid=root.active_sequential_run;
  // Dummy credential-shaped test data only, not real secrets or stored state.
  await page.route('**/runs/'+rid,async route=>{const response=await route.fetch(),run=await response.json();run.steps[1].context.api_key='do-not-copy-structured';run.steps[1].answer+='\napi_key="do-not-copy-raw"\nBearer do-not-copy-auth';await route.fulfill({json:run});});
  await page.locator('#sequenceRefresh').click();
  await page.locator('#copySequenceContext').click();await page.waitForFunction(()=>window.__copiedContext?.includes('xiaoguai-workspace-context-v1'));
  const copied=await page.evaluate(()=>window.__copiedContext),payload=JSON.parse(copied.slice(copied.indexOf('{')));
  assert(payload.scope==='current_run'&&payload.steps.length===2,'whole run incomplete');assert(payload.steps[1].input.current_unit_observations.length===2,'input evidence missing');
  assert(payload.steps[1].model_output_raw.includes('tracks')&&payload.current_model_membership,'output/assignment missing');
  assert(payload.display_hypothesis.links&&payload.display_hypothesis.groups,'display/model distinction missing');
  for(const text of ['do-not-copy-structured','do-not-copy-raw','do-not-copy-auth'])assert(!copied.includes(text),'credential-shaped field leaked');
  await page.locator('#sequenceSteps>details').nth(1).evaluate(n=>n.open=true);
  await page.getByRole('button',{name:'复制第2步上下文',exact:true}).click();
  let single=await page.evaluate(()=>{const t=window.__copiedContext;return JSON.parse(t.slice(t.indexOf('{')));});assert(single.scope==='single_step'&&single.steps.length===1&&single.steps[0].index===2,'step copy wrong');
  await page.locator('#sequenceSpatialGroupList>details').first().evaluate(n=>n.open=true);await page.getByRole('button',{name:'复制此组上下文',exact:true}).first().click();
  single=await page.evaluate(()=>{const t=window.__copiedContext;return JSON.parse(t.slice(t.indexOf('{')));});assert(single.scope==='focused_unit'&&single.focused_unit.model_assignment.length===2,'focused point context missing');
  await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw Error('simulated browser denial');};document.execCommand=()=>false;});
  await page.locator('#copySequenceContextBottom').click();await page.waitForFunction(()=>document.querySelector('#contextCopyDialog')?.open);
  assert((await page.locator('#contextCopyText').inputValue()).includes('current_run'),'manual fallback incomplete');await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.unroute('**/runs/'+rid);await page.locator('#sequenceRefresh').click();
  const after=await(await page.request.get(rootUrl)).json();assert(after.calls===root.calls&&JSON.stringify(after.tracks)===JSON.stringify(root.tracks),'copy changed calls/state');
  await page.locator('#sequenceSteps>details').nth(1).evaluate(n=>n.open=true);
  await page.screenshot({path:'output/supplied-zip-test/context-copy-controls.png'});
  return {wholeRun:true,singleStep:true,focusedUnit:true,rawOutputAndInput:true,redactedCredentialFields:true,manualFallback:true,newModelCalls:0};
}
