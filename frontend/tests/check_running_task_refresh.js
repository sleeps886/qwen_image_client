async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const data=await(await page.request.get(base+'/api/test-mixed-fixture')).json();
  await page.locator('#pointFileImport').setInputFiles({name:'refresh-task.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
  await page.locator('#chatPrepare').click();await page.waitForFunction(()=>!!document.querySelector('#sessionId').value&&!document.querySelector('#chatPrepare').disabled);
  const sid=await page.locator('#sessionId').inputValue();let pending=true,posts=0;
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('mixed-sessions'))posts++;});
  await page.route('**/mixed-sessions/'+sid,async route=>{
    const response=await route.fetch(),state=await response.json();
    if(pending)state.status='pending';
    await route.fulfill({json:state});
  });
  await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#refreshRunningTask').hidden&&!document.querySelector('#refreshRunningTask').disabled);
  await page.locator('#refreshRunningTask').click();
  await page.waitForFunction(()=>document.querySelector('#chatNotice').textContent.includes('后端仍在处理原调用'));
  assert(await page.locator('#chatSend').isDisabled(),'pending state permits duplicate send');
  pending=false;await page.locator('#refreshRunningTask').click();
  await page.waitForFunction(()=>document.querySelector('#refreshRunningTask').hidden&&!document.querySelector('#chatPrepare').disabled);
  assert(posts===0,'refresh issued a POST/model call');
  assert(await page.locator('#chatPrepare').isEnabled(),'completed status not refreshed');
  await page.unroute('**/mixed-sessions/'+sid);
  return {pendingRefreshVisible:true,refreshGetOnly:true,duplicateSendBlocked:true,stateUpdated:true,modelCalls:0};
}
