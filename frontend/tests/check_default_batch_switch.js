// Default mode / pinned old queue recovery. Fake provider for deterministic UI failure.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  assert(await page.locator('#sequenceMode').inputValue()==='batch-snapshot-v1','new task still defaults to sequential');
  const dataset=await(await page.request.get(base+'/api/test-sequential-spatial-dataset')).json();
  await page.locator('#pointFileImport').setInputFiles({name:'default-batch.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(dataset))});
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('10个原始观测'));
  await page.locator('#samplePointCount').fill('2');await page.locator('#samplePoints').click();
  await page.locator('#sequenceMode').selectOption('sequential');
  await page.locator('#chatQuestion').fill('TEST_ONLY_BAD_POINT_INDEX');await page.locator('#chatQuestion').dispatchEvent('input');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备2组'));
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();
  await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('failed'),{},{timeout:30000});
  assert((await page.locator('#sequenceModeNotice').textContent()).includes('修改调用预算不会改变模式'),'old mode mismatch not explained');
  assert(await page.locator('#sequenceMode').isDisabled(),'old authorized spec silently editable');
  const sid=await page.locator('#sessionId').inputValue(),url=base+'/api/incremental/mixed-sessions/'+sid;
  const before=await(await page.request.get(url)).json(),rid=before.active_sequential_run;
  await page.evaluate(()=>{window.confirm=message=>{window.__switchConfirm=message;return true;};});
  await page.locator('#sequenceSwitchBatch').click();await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('现为50组快照模式'));
  assert(await page.locator('#sequenceMode').inputValue()==='batch-snapshot-v1','not switched');
  const after=await(await page.request.get(url)).json(),old=await(await page.request.get(url+'/runs/'+rid)).json();
  assert(after.calls===before.calls&&old.status==='rejected'&&!after.active_sequential_run,'switch invoked model or kept active old run');
  assert(old.steps.at(-1).answer.includes('99999'),'old model text lost');
  assert((await page.evaluate(()=>window.__switchConfirm)).includes('暂定更新不采纳'),'switch not explicit about provisional state');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceMode').value==='batch-snapshot-v1'&&!document.querySelector('#pointFileImport').disabled);
  await page.locator('#sequenceRetryEnabled').uncheck();await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('快照模式每批50组／并发50'));
  assert(errors.length===0,'runtime errors: '+errors.join(';'));
  await page.screenshot({path:'output/supplied-zip-test/default-batch-switch.png'});
  return {defaultBatch:true,oldModePinned:true,oldReplyPreserved:true,switchRealCalls:0,reloadPreserved:true,newPreviewBatch:true};
}
