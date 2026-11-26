// Run with playwright-cli run-code --filename frontend/tests/check_workspace_retention.js.
// The selected browser must point at a local test server. No paid model requests.
async (page) => {
  const origin=new URL(page.url()).origin;
  let paidRequests=0;
  await page.route('**/api/**',async route=>{
    const request=route.request();
    if(request.method()==='POST' && !request.url().endsWith('/api/incremental/sessions')){
      paidRequests++;await route.abort();return;
    }
    await route.continue();
  });
  const assert=(condition,message)=>{if(!condition)throw new Error(message);};
  await page.waitForFunction(()=>typeof workspaceReady!=='undefined' && workspaceReady);
  await page.locator('#files').setInputFiles({name:'btr_A_hull.png',mimeType:'image/png',
    buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});
  await page.locator('#platform').setInputFiles({name:'platform.json',mimeType:'application/json',buffer:Buffer.from('{"platforms":[]}')});
  await page.locator('#prompt').fill('刷新保留测试：这是用户编辑的提示词。');
  await page.locator('#consent').check();
  await page.evaluate(()=>{
    showModelResult({result_id:'20261124_102104_35b4863d',answer:'这是浏览器保留测试的模拟结果，不是模型调用',
      trajectory_image:null,trajectory_status:'not_generated',warnings:[],downloads:{'inverse_fields.json':'/api/results/20261124_102104_35b4863d/inverse_fields.json'}});
    saveWorkspace();
  });
  await page.waitForFunction(async()=>!!(await WorkspaceCache.get('main-state'))?.result);
  await page.reload();
  await page.waitForFunction(()=>workspaceReady);
  assert((await page.locator('#previews').textContent()).includes('btr_A_hull.png'),'image selection not restored');
  assert(await page.locator('#platform').evaluate(el=>el.files[0]?.name)==='platform.json','platform file not restored');
  assert(await page.locator('#prompt').inputValue()==='刷新保留测试：这是用户编辑的提示词。','prompt not restored');
  assert((await page.locator('#answer').textContent()).includes('模拟结果'),'result not restored');
  assert(await page.locator('#gaussianTransfer').isVisible(),'batch transfer link not restored');
  assert(!await page.locator('#consent').isChecked(),'consent must not be restored');
  await page.locator('#removePlatform').click();
  await page.waitForFunction(async()=>(await WorkspaceCache.get('main-files'))?.platform===null);
  await page.reload();await page.waitForFunction(()=>workspaceReady);
  assert(await page.locator('#platform').evaluate(el=>el.files.length)===0,'removed file resurrected');
  await page.evaluate(()=>{
    retainedResult=null;busy=true;
    document.getElementById('answer').textContent='模拟刷新前的未完成正文';
    document.getElementById('resultPanel').dataset.state='running';
    receiveNode({node:'inverse_field',label:'测试运行节点',status:'running'});
    status('模拟运行中');saveWorkspace();busy=false;
  });
  await page.waitForFunction(async()=>(await WorkspaceCache.get('main-state'))?.wasRunning===true);
  await page.reload();await page.waitForFunction(()=>workspaceReady);
  assert((await page.locator('#answer').textContent()).includes('未完成正文'),'partial output not restored');
  assert((await page.locator('#status').textContent()).includes('未自动续跑'),'interrupted state not explained');
  assert((await page.locator('#nodeList').textContent()).includes('刷新前的运行记录'),'node execution record not restored');
  assert(await page.locator('#send').isEnabled(),'running flag restored incorrectly');
  await page.locator('#clearWorkspaceCache').click();
  await page.waitForFunction(()=>workspaceReady && document.getElementById('previews').children.length===0);
  assert(!(await page.locator('#answer').textContent()).includes('模拟结果'),'clear did not clear result');

  await page.goto(origin+'/incremental');
  await page.waitForFunction(()=>!document.getElementById('example').disabled);
  await page.locator('#predictionAdvanced').evaluate(el=>el.hidden=false); await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#example').click();
  await page.locator('#horizon').fill('35');
  await page.locator('#seeds').fill('P0,P2,P4');
  await page.locator('#create').click();
  await page.waitForFunction(()=>document.getElementById('predictionSummary').textContent.includes('调用：0/'));
  const session=await page.locator('#sessionId').inputValue();
  await page.locator('#predictionConsent').check();
  await page.waitForFunction(async()=>!!(await WorkspaceCache.get('prediction-workspace'))?.state);
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('predictionCacheStatus').textContent.includes('已恢复'));
  assert(await page.locator('#sessionId').inputValue()===session,'prediction session not restored');
  assert(await page.locator('#horizon').inputValue()==='35','prediction options not restored');
  assert(await page.locator('#seeds').inputValue()==='P0,P2,P4','prediction seeds not restored');
  assert((await page.locator('#inventory').textContent()).includes('P15'),'prediction dataset not restored');
  assert(!await page.locator('#predictionConsent').isChecked(),'prediction consent must reset');
  assert((await page.locator('#predictionSummary').textContent()).includes('调用：0/'),'restore triggered model call');
  await page.locator('#clearPredictionCache').click();
  await page.waitForFunction(()=>document.getElementById('sessionId').value==='');
  assert(!(await page.locator('#inventory').textContent()).includes('P15'),'clear did not remove dataset');
  assert(paidRequests===0,'restoration attempted paid requests');
  return {mainFilesAndResultRestored:true,predictionDatasetAndSessionRestored:true,clearVerified:true,paidRequests};
}
