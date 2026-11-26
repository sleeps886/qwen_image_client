// Run in a fresh browser profile: exercises migration from the actual v1 schema.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let paid=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))paid++;});
  await page.goto(base+'/api/health');
  const data={source_result_id:'independent-legacy-file',file_name:'legacy-kept.json',candidates:[{candidate_id:'h1',t:1,array:'hull',platform_id:'A',bearing_deg:30},{candidate_id:'t1',t:1,array:'tow',platform_id:'A',bearing_deg:31},{candidate_id:'h2',t:2,array:'hull',platform_id:'A',bearing_deg:32}]};
  await page.evaluate(async data=>new Promise((resolve,reject)=>{
    const request=indexedDB.open('sonar-workspace-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('snapshots');request.onerror=()=>reject(request.error);
    request.onsuccess=()=>{const db=request.result,tx=db.transaction('snapshots','readwrite'),store=tx.objectStore('snapshots'),snapshot=id=>({dataset:{...data,point_file_id:data.source_result_id},state:null,selected:data.candidates.map(p=>p.candidate_id),groups:[''],messages:[],conversationId:id});
      store.put({id:data.source_result_id,name:data.file_name,data,created_at:'2026-11-25T00:00:00Z'},'point-file-'+data.source_result_id);
      store.put({id:'deleted-legacy-file',name:'deleted.json',data:{...data,source_result_id:'deleted-legacy-file'},created_at:'2026-11-24T00:00:00Z'},'point-file-deleted-legacy-file');
      store.put(true,'point-file-deleted-deleted-legacy-file');
      store.put([data.source_result_id,'deleted-legacy-file'],'point-files');
      store.put([{id:'session-a',name:'会话A'},{id:'session-b',name:'会话B'}],'mixed-conversations');store.put(snapshot('session-a'),'mixed-workspace');store.put(snapshot('session-a'),'mixed-conversation-session-a');store.put(snapshot('session-b'),'mixed-conversation-session-b');
      tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>reject(tx.error);
    };
  }),data);
  await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===2&&document.querySelectorAll('.point-file').length===1);
  const files=()=>page.evaluate(()=>PointFiles.list());
  assert(JSON.stringify((await files())[0].data)===JSON.stringify(data),'migration changed original file');
  const schema=await page.evaluate(()=>new Promise((resolve,reject)=>{const request=indexedDB.open('sonar-workspace-v1',2);request.onsuccess=()=>{const db=request.result,result={version:db.version,stores:[...db.objectStoreNames]};db.close();resolve(result);};request.onerror=()=>reject(request.error);}));
  assert(schema.version===2&&schema.stores.includes('point_files'),'files still stored only in conversation snapshots');
  assert(await page.evaluate(()=>WorkspaceCache.get('point-file-independent-legacy-file').then(Boolean)),'v1 backup removed during migration');
  await page.evaluate(()=>{window.confirm=()=>false;});await page.locator('[data-conversation-id="session-b"] .conversation-delete').click();assert(await page.locator('.conversation-item').count()===2,'cancel deleted session');
  await page.evaluate(()=>{window.confirm=()=>true;});await page.locator('[data-conversation-id="session-b"] .conversation-delete').click();await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===1);assert((await files()).length===1,'inactive session deletion removed file');
  await page.evaluate(()=>history.replaceState(null,'','/incremental?source_result=independent-legacy-file'));
  await page.locator('[data-conversation-id="session-a"] .conversation-delete').click();await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===0);assert(await page.locator('#predictionChart circle').count()===0,'deleted active session still shown');
  assert(await page.locator('.point-file').count()===1,'file library disappeared with active session');assert(JSON.stringify((await files())[0].data)===JSON.stringify(data),'session deletion mutated file contents');assert(!new URL(page.url()).searchParams.has('source_result'),'deleted session URL can resurrect it');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.point-file').length===1&&!document.querySelector('#pointFileImport').disabled);assert(await page.locator('.conversation-item').count()===0,'deleted session resurrected');
  const downloadPromise=page.waitForEvent('download');await page.locator('.point-file').getByRole('button',{name:'导出全部点位',exact:true}).click();assert((await downloadPromise).suggestedFilename()==='legacy-kept.json','kept file cannot export');
  await page.locator('.point-file').getByRole('button',{name:'打开',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('#predictionChart circle').length===2);assert((await files()).length===1,'opening kept file duplicated or replaced it');assert(await page.locator('.conversation-item').count()===1,'kept file cannot start new conversation');
  await page.evaluate(()=>window.confirm=()=>true);await page.locator('.point-file').getByRole('button',{name:'删除',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.point-file').length===0);assert(await page.evaluate(data=>PointFiles.put(data),data)===null,'deleted file resurrected from legacy backup');
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);assert(await page.locator('.point-file').count()===0,'explicit file deletion not persisted');
  await page.evaluate(async()=>Promise.all(['parallel-a','parallel-b','parallel-c'].map(source_result_id=>PointFiles.put({source_result_id,candidates:[]}))));assert((await files()).length===3,'concurrent imports lost file index');assert(paid===0,'file/session management called model');
  return {v1Migration:true,originalDataPreserved:true,separateStore:true,deletedLegacyFileNotResurrected:true,inactiveSessionDelete:true,activeSessionDeleteKeepsFile:true,reloadKeepsFile:true,reopenAndExport:true,explicitFileDeleteOnly:true,parallelImports:true,realModelCalls:0};
}
