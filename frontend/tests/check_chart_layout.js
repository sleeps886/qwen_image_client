// Layout-only verification on the isolated fake server; no real model calls.
async page=>{
 const base=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 assert((await (await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
 await page.setViewportSize({width:1920,height:1080});await page.goto(base+'/incremental?empty=1');
 await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('已就绪'));
 assert(await page.locator('#spatialTrajectoryScene').isHidden(),'empty spatial panel visible');
 assert(await page.locator('#spatialChart').evaluate(n=>n.getBoundingClientRect().height===0),'blank canvas reserves height');
 const fixture=await (await page.request.get(base+'/api/test-spatial-fixture')).json();
 await page.getByText('导入数据与参数（免费）',{exact:true}).click();await page.locator('#sessionId').fill(fixture.session_id);await page.locator('#reload').click();
 await page.waitForFunction(()=>document.querySelectorAll('#spatialChart [data-spatial-track]').length>0);
 const order=await page.evaluate(()=>{
  const b=document.querySelector('#sharedTrajectoryScene'),m=document.querySelector('#chatMessages'),s=document.querySelector('#spatialTrajectoryScene');
  return {bearingBeforeChat:!!(b.compareDocumentPosition(m)&Node.DOCUMENT_POSITION_FOLLOWING),spaceAfterChat:!!(m.compareDocumentPosition(s)&Node.DOCUMENT_POSITION_FOLLOWING)};
 });
 assert(order.bearingBeforeChat&&order.spaceAfterChat,'chart order incorrect');
 const alignment=await page.evaluate(()=>['sharedTrajectoryScene','chatMessages','spatialTrajectoryScene'].map(id=>document.getElementById(id).getBoundingClientRect().left));
 assert(Math.max(...alignment)-Math.min(...alignment)<2,'chat and charts do not share one column');
 assert(await page.locator('#bearingLegend .legend-item').count()===2,'separate array legend missing');
 assert(await page.locator('#predictionChart polyline,#predictionChart [data-kind="confirmed_association_link"]').count()===0,'observation preview contains trajectory lines');
 const catalogue=await (await page.request.get(base+'/api/incremental/mixed-sessions/'+fixture.session_id+'/catalog')).json();
 const times=await page.locator('#predictionChart circle[data-kind="observation"]').evaluateAll(nodes=>nodes.map(n=>Number(n.dataset.time)));
 assert(times.length===catalogue.candidates.length,'observation points duplicated or omitted');
 assert(Math.max(...times)===Math.max(...catalogue.candidates.map(p=>p.t)),'forecast points leaked into observation preview');
 assert(await page.locator('#chatMessages .prediction-message.assistant').count()>=2,'saved final answer not restored before chart');
 for(const id of ['sharedTrajectoryScene','spatialTrajectoryScene'])assert((await page.locator('#'+id).boundingBox()).width<=861,'desktop chart too wide');
 assert((await page.locator('#predictionChart').boundingBox()).height<=211,'bearing preview too tall');
 assert((await page.locator('#spatialChart').boundingBox()).height<=361,'spatial figure too tall');
 await page.locator('#spatialTrajectoryScene').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/chart-layout-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>Math.abs(document.querySelector('#predictionChart').viewBox.baseVal.width-document.querySelector('#predictionChart').clientWidth)<3);
 for(const id of ['sharedTrajectoryScene','spatialTrajectoryScene'])assert((await page.locator('#'+id).boundingBox()).width<=390,'mobile horizontal overflow');
 await page.locator('#sharedTrajectoryScene > summary').click();assert(await page.locator('#predictionChart').isHidden(),'bearing cannot collapse');
 await page.locator('#sharedTrajectoryScene > summary').click();assert(await page.locator('#predictionChart').isVisible(),'bearing cannot reopen');
 await page.locator('#spatialTrajectoryScene').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/chart-layout-mobile.png',fullPage:true});
 return {...order,emptySpaceHidden:true,desktopWidthCap:860,compactFigures:true,responsive:true,collapsible:true,realModelCalls:0};
}
