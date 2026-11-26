// UI activity style verification, isolated fake gateway only.
async page=>{
 const base=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 assert((await (await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
 const dataset=await (await page.request.get(base+'/api/test-mixed-fixture')).json();
 const created=await (await page.request.post(base+'/api/incremental/mixed-sessions',{data:{dataset,max_calls:2}})).json(),url=base+'/api/incremental/mixed-sessions/'+created.session_id;
 const body={version:0,selected_ids:dataset.candidates.map(p=>p.candidate_id),question:'隔离界面回放',consent:true};
 const preview=await (await page.request.post(url+'/preview',{data:body})).json();
 const response=await page.request.post(url+'/step',{data:{...body,context_digest:preview.context_digest}});assert(response.ok(),'fake association failed');
 await page.setViewportSize({width:1600,height:1100});await page.goto(base+'/incremental?empty=1');
 await page.getByText('导入数据与参数（免费）',{exact:true}).click();await page.locator('#sessionId').fill(created.session_id);await page.locator('#reload').click();
 await page.waitForFunction(()=>document.querySelector('#executionPanel'));
 const panel=page.locator('#executionPanel');assert(await panel.evaluate(n=>!n.open),'finished activity not folded');
 assert((await panel.locator(':scope > summary').textContent()).includes('用时'),'duration summary missing');
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/chat-activity-collapsed.png',fullPage:true});
 const before=(await (await page.request.get(url)).json()).calls;
 await panel.locator(':scope > summary').click();
 assert(await page.locator('#executionProgress').isHidden(),'dashboard progress still visible');
 assert(await page.locator('#executionNodes .execution-tool-icon').count()>0,'compact tool icons missing');
 assert(await page.locator('#executionNodes .node-evidence[open]').count()===0,'large details expanded automatically');
 assert(await page.locator('#executionNodes .execution-node.completed').first().evaluate(n=>parseFloat(getComputedStyle(n).borderBottomWidth)===0),'table separators remain');
 await panel.locator('[data-node="update"] .node-evidence > summary').click();assert(await panel.locator('.association-collection').first().isVisible(),'collection details unavailable');
 await panel.locator('[data-node="update"] .node-evidence > summary').click();
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/chat-activity-expanded.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();assert((await panel.boundingBox()).width<=390,'mobile activity overflow');
 await page.screenshot({path:'output/playwright/chat-activity-mobile.png',fullPage:true});
 const after=(await (await page.request.get(url)).json()).calls;assert(before===after,'expanding activity called a model');
 return {durationSummary:true,defaultCollapsed:true,compactTools:true,noProgressBar:true,detailsOnDemand:true,mobile:true,realModelCalls:0};
}
