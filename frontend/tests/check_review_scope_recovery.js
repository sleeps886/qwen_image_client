// Old returned reply recovery and visible validation detail. Fake server only.
async page=>{
 const base=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 assert((await (await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
 const fixture=await (await page.request.get(base+'/api/test-legacy-fragment-review')).json(),url=base+'/api/incremental/mixed-sessions/'+fixture.session_id;
 await page.setViewportSize({width:1600,height:1100});await page.goto(base+'/incremental?empty=1');await page.getByText('导入数据与参数（免费）',{exact:true}).click();
 await page.locator('#sessionId').fill(fixture.session_id);await page.locator('#reload').click();
 await page.waitForFunction(()=>document.querySelector('#executionPanel'));
 await page.locator('#executionPanel > summary').click();
 assert((await page.locator('#executionNodes .association-warning').textContent()).includes('复核请求引用不存在'),'specific validation error missing');
 assert(await page.locator('#recoverPlot').isVisible(),'free saved-reply recovery missing');
 const before=(await (await page.request.get(url)).json()).calls;await page.locator('#recoverPlot').click();
 await page.waitForFunction(()=>document.querySelector('#chatState').textContent==='需要关联复核');
 const state=await (await page.request.get(url)).json();assert(state.calls===before,'recovery called model');
 assert(state.plot_status==='awaiting_association_recheck','review suggestion lost');
 assert(state.association_recheck_requests[0].fragment_id==='F001','fragment scope not normalized');
 assert(await page.locator('#spatialTrajectoryScene').isHidden(),'fake spatial trajectory displayed');
 assert(await page.locator('#prepareAssociationRecheck').isVisible(),'association recheck action missing');
 await page.screenshot({path:'output/playwright/review-scope-recovered.png',fullPage:true});
 return {specificErrorVisible:true,freeRecovery:true,typedFragmentReview:true,noInventedSpace:true,realModelCalls:0};
}
