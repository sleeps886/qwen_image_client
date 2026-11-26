async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let posts=0;
  await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  page.on('request',r=>{if(r.method()==='POST')posts++;});
  const body='{"tracks":[{"track_ref":"N3","observation_ids":["O00011"]}]}';
  await page.evaluate(async text=>{
    const id='saved-validation-failure',dataset={candidates:['hull','tow'].map(array=>({candidate_id:array,t:0,array,platform_id:'P',bearing_deg:30}))};
    await WorkspaceCache.set('mixed-workspace',{dataset,state:null,selected:[],groups:[''],conversationId:'validation-display',messages:[{role:'execution',execution_id:id}],executionHistory:[{execution_id:id,base_version:0,status:'failed',created_at:new Date().toISOString(),sequence:1,streamed_body:text,streamed_status:'interrupted',nodes:[{node:'validate',label:'候选占用与引用校验',kind:'local_validation',status:'failed',error_type:'ValueError',error_detail:'候选引用不存在：O00011（位置：N3）'}]}]});
  },body);
  await page.goto(base+'/incremental');await page.waitForFunction(()=>!!document.querySelector('.execution-stream-body'));
  const status=await page.locator('.execution-stream-status').textContent();
  assert(status.includes('模型正文已返回，但校验未通过'),'misclassified validation as disconnection');
  assert(!status.includes('页面流已中断'),'old misleading message remains');
  assert(await page.locator('.execution-stream-body').textContent()===body,'invalid original body not retained');
  assert(posts===0,'viewing error triggered a model request');
  return {validationNotDisconnect:true,oldSnapshotCompatible:true,originalBodyPreserved:true,modelCalls:0};
}
