// Real timeout regression; never authorizes model dispatch.
async page=>{
  const assert=(v,m)=>{if(!v)throw Error(m);};let calls=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))calls++;});
  await page.addInitScript(()=>{
    const original=indexedDB.open.bind(indexedDB);let first=true;
    indexedDB.open=(...args)=>{if(first){first=false;return {};}return original(...args);};
  });
  await page.route('**/openapi.json',()=>{});
  await page.goto('http://127.0.0.1:8891/incremental');
  await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled,{},{timeout:35000});
  assert((await page.locator('#backendReadiness').textContent()).includes('请求超时'),'hung API not explained');
  assert((await page.locator('#pointFileImportStatus').textContent()).includes('文件库连接超时'),'hung storage not explained');
  assert(await page.locator('#chatSend').isDisabled(),'readiness error permits paid send');
  await page.locator('#pointFileDropZone').evaluate(node=>node.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:new DataTransfer()})));
  assert((await page.locator('#pointFileImportStatus').textContent()).includes('先把ZIP解压到普通文件夹'),'empty virtual archive drag silently ignored');
  await page.locator('#pointFileImport').setInputFiles([1,2,3].map(i=>`C:/Users/LENOVO/AppData/Local/Temp/btr_target_00${i} (6).zip`));
  await page.waitForFunction(()=>document.querySelector('#pointFileImportStatus').textContent.includes('966个原始观测'));
  assert((await page.evaluate(()=>PointFiles.list()))[0].data.candidates.length===966,'retry lost data');
  assert(await page.locator('#pointFileImport').isEnabled(),'import completion still locked');
  assert(calls===0,'model dispatch during import');
  return {networkTimeoutUnlock:true,storageTimeoutUnlock:true,virtualArchiveDragExplained:true,retryActualThreeZipPoints:966,paidDispatches:0};
}
