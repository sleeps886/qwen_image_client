async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};let paid=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/(step|plot-step)$/.test(new URL(r.url()).pathname))paid++;});
  await page.goto(base+'/incremental?empty=1');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const point={candidate_id:'legacy-kept',t:1,array:'hull',platform_id:'A',bearing_deg:30};
  await page.evaluate(async point=>{
    await WorkspaceCache.set('mixed-workspace',{
      dataset:{candidates:[point],import_filter:{original_count:2,excluded:[{...point,candidate_id:'legacy-excluded',t:2}],rule:'same-platform-exact-time-hull-and-tow'}},
      state:null,selected:[point.candidate_id],groups:[''],messages:[],conversationId:'legacy-m0-test'
    });
  },point);
  await page.goto(base+'/incremental');await page.waitForFunction(()=>document.querySelector('#importFilterReport').textContent.includes('旧快照曾排除1点'));
  assert(await page.locator('#selectedPoints>div').count()===1,'legacy selection changed');
  assert((await page.locator('#importFilterReport').textContent()).includes('请重新打开原始数据文件'),'legacy recovery guidance absent');
  assert((await page.evaluate(()=>WorkspaceCache.get('mixed-workspace'))).dataset.candidates.length===1,'legacy catalog silently expanded');
  assert(paid===0,'legacy restore sent model request');
  return {legacySnapshotUnchanged:true,explicitRecoveryGuidance:true,realModelCalls:0};
}
