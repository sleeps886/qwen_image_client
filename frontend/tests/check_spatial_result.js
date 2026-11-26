// Only the isolated synthetic server may generate this verification fixture.
async page=>{
 const origin=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 if(!(await (await page.request.get(origin+'/api/test-fake-gateway')).json()).fake_gateway)throw new Error('Fake server required');
 const fixture=await (await page.request.get(origin+'/api/test-spatial-fixture')).json();
 await page.setViewportSize({width:1600,height:1100});await page.goto(origin+'/incremental?empty=1');
 await page.getByText('导入数据与参数（免费）',{exact:true}).click();await page.locator('#sessionId').fill(fixture.session_id);await page.locator('#reload').click();
 await page.waitForFunction(()=>document.querySelectorAll('#spatialChart [data-spatial-track]').length>0);
 assert(await page.locator('#spatialChart [data-platform-marker]').count()===1,'platform square missing');
 assert(await page.locator('#sharedTrajectoryScene').evaluate(n=>n.tagName==='DETAILS'&&n.open),'bearing chart must be open at the top');
 const before=await page.locator('#spatialChart polyline').first().getAttribute('points');await page.locator('#spatialZoomIn').click();
 assert(await page.locator('#spatialChart polyline').first().getAttribute('points')!==before,'spatial zoom not working');await page.locator('#spatialReset').click();
 const url=await page.locator('#downloadSpatial').getAttribute('href'),png=await page.request.get(origin+url);assert(png.ok()&&(await png.body()).subarray(0,4).toString('hex')==='89504e47','old style PNG not returned');
 await page.locator('#spatialTrajectoryScene').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/model-spatial-restored.png',fullPage:true});
 assert(await page.locator('#predictionChart').isVisible(),'bearing chart missing');
 assert((await page.locator('#predictionChart').boundingBox()).height<=260,'transition preview too large');
 await page.setViewportSize({width:800,height:900});await page.waitForFunction(()=>Math.abs(document.querySelector('#predictionChart').viewBox.baseVal.width-document.querySelector('#predictionChart').clientWidth)<3);
 await page.locator('#sharedTrajectoryScene > summary').click();assert(await page.locator('#predictionChart').isHidden(),'transition chart cannot collapse');
 await page.setViewportSize({width:390,height:844});await page.locator('#spatialTrajectoryScene').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/model-spatial-mobile.png',fullPage:true});
 return {spatialXY:true,platformMarker:true,zoom:true,oldStylePNG:true,transitionCompactAndCollapsible:true,responsiveViewBox:true,realModelCalls:0};
}
