async page=>{
 const assert=(v,m)=>{if(!v)throw Error(m);},errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewportSize({width:1500,height:950});await page.goto('http://127.0.0.1:8891/');await page.waitForTimeout(600);
 assert(await page.locator('.app-nav-links a[aria-current="page"]').textContent().then(t=>t.includes('图片识别')),'wrong recognition active');assert(await page.locator('#predictionIntro').count()===0,'old intro link remains');
 await page.locator('.app-nav-links a[href="/incremental"]').click();await page.waitForURL('**/incremental');await page.waitForTimeout(500);assert((await page.locator('.app-nav-links a[aria-current="page"]').textContent()).includes('轨迹工作台'),'wrong trajectory active');
 assert(await page.locator('#pointSidebar').count()===1,'observation sidebar removed');const rect=await page.locator('body>header').boundingBox();assert(rect.x>=170,'content covered by navigation');
 await page.screenshot({path:'output/playwright/app-navigation-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile horizontal overflow');await page.locator('.app-nav-links a[href="/"]').click();await page.waitForURL('http://127.0.0.1:8891/');await page.waitForTimeout(400);await page.screenshot({path:'output/playwright/app-navigation-mobile.png',fullPage:true});assert(!errors.length,errors.join(';'));return {twoPageNavigation:true,activePage:true,sidebarPreserved:true,mobile:true,pageErrors:errors};
}
