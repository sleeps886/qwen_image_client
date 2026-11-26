async page=>{
  let posts=0;const errors=[];
  page.on('request',r=>{if(r.method()==='POST')posts++;});
  page.on('pageerror',e=>errors.push(e.message));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'));
  if(!await page.locator('#sequenceAutoConnect').isDisabled())throw Error('local guessing control enabled');
  if(await page.locator('#sequenceMirrorSide').isVisible())throw Error('irrelevant mirror convention still shown');
  if(!(await page.locator('#sequenceAutoConnectLabel').textContent()).includes('模型快照'))throw Error('wrong provenance label');
  if(posts||errors.length)throw Error('display caused a POST or runtime error');
  await page.screenshot({path:'output/supplied-zip-test/spatial-cards-workbench-ui.png'});
  return {readOnlyRefresh:true,postRequests:posts,runtimeErrors:errors.length,modelOwnedControls:true};
}
