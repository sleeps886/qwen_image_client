// Isolated fake server only: verifies joint identity UI, never real LLM accuracy.
async page=>{
 const base=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 assert((await (await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
 const data=await (await page.request.get(base+'/api/test-joint-dataset')).json();
 await page.setViewportSize({width:1600,height:1100});await page.goto(base+'/incremental?empty=1');
 await page.getByText('导入数据与参数（免费）',{exact:true}).click();
 await page.locator('#datasetFile').setInputFiles({name:'joint-synthetic.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
 await page.waitForFunction(()=>document.querySelectorAll('#candidateGroupList input[data-candidate-id]').length===18);
 for(const checkbox of await page.locator('#candidateGroupList > .mixed-row > label > input').all())await checkbox.check();
 await page.locator('#chatPrepare').click();await page.waitForFunction(()=>document.querySelector('#chatInput').textContent.includes('joint_identity'));
 await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
 await page.waitForFunction(()=>document.querySelector('#predictionSummary').textContent.includes('物理目标假设1个'));
 const block=page.locator('#chatMessages > details[data-execution-group]').last();await block.evaluate(n=>n.open=true);
 const collection=block.locator('details').filter({hasText:'查看本轮集合'}).first();if(await collection.count())await collection.evaluate(n=>n.open=true);
 assert(await page.getByText(/T001 · 物理目标假设/).count()===1,'physical target collection missing');
 assert(await page.getByText(/F001 · .*个观测点/).count()===1,'fragment F001 not distinct');
 await page.locator('#adoptTrajectory').click();await page.locator('#prepareModelPlot').click();await page.waitForFunction(()=>!document.querySelector('#prepareModelPlot').disabled);await page.locator('#modelPlotConsent').check();
 await page.locator('#runModelPlot').click();assert(await page.locator('#spatialTrajectoryScene').isHidden(),'spatial plot shown during streaming');await page.waitForFunction(()=>document.querySelectorAll('#spatialChart [data-spatial-track="T001"]').length>0);
 assert(await page.locator('#spatialChart [data-platform-marker]').count()===1,'platform missing');
 assert(await page.locator('#sharedTrajectoryScene').evaluate(n=>n.open),'bearing chart was automatically hidden');
 await page.screenshot({path:'output/playwright/joint-identity-verified.png',fullPage:true});
 return {physicalTargets:1,arrayFragments:2,jointIdentityBeforeReview:true,modelPlotAfterReview:true,spatialTargetNamespace:true,realModelCalls:0};
}
