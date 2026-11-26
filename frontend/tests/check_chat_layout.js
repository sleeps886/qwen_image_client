// Read-only layout check against an existing saved session. Never calls a model.
async (page) => {
  let posts=0;await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts++;await route.abort();return;}await route.continue();});
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  await page.reload();
  await page.setViewportSize({width:1440,height:960});
  await page.waitForFunction(()=>document.getElementById('backendReadiness').dataset.state==='ready');
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  await page.locator('#predictionDebug').evaluate(el=>el.open=true);
  await page.locator('#sessionId').fill('20261124_132352_55ea54bd');await page.locator('#reload').click();
  await page.waitForFunction(()=>document.querySelectorAll('.assistant svg').length===1);
  await page.getByRole('button',{name:'高级设置',exact:true}).click();
  const desktop=await page.evaluate(()=>{
    const messages=document.getElementById('chatMessages').getBoundingClientRect(),composer=document.querySelector('.chat-composer').getBoundingClientRect();
    return {scroll:document.documentElement.scrollHeight<=innerHeight+1,messagesHeight:messages.height,noOverlap:messages.bottom<=composer.top+1,composerFits:composer.bottom<innerHeight};
  });
  assert(desktop.scroll&&desktop.messagesHeight>240&&desktop.noOverlap&&desktop.composerFits,'desktop viewport layout broken');
  assert(!await page.locator('#chatPointTable').isVisible(),'point attachment expanded by default');
  const before=(await page.locator('.prediction-main').boundingBox()).width;
  await page.locator('#togglePointSidebar').click();
  assert(!await page.locator('#pointSidebar').isVisible(),'point sidebar not collapsible');
  assert((await page.locator('.prediction-main').boundingBox()).width>before,'conversation did not expand');
  await page.locator('#togglePointSidebar').click();
  await page.screenshot({path:'work/chat-layout-desktop.png'});
  await page.locator('#chatAttachments summary').click();
  assert(await page.locator('#chatPointTable').isVisible(),'attachment cannot open');
  await page.locator('#chatAttachments summary').click();
  await page.setViewportSize({width:390,height:844});
  assert(!await page.locator('#pointSidebar').isVisible(),'mobile sidebar consumes chat width');
  assert(!await page.locator('#executionPanel').isVisible(),'mobile execution consumes chat width');
  await page.locator('#togglePointSidebar').click();assert(await page.locator('#pointSidebar').isVisible(),'mobile point drawer cannot open');
  await page.keyboard.press('Escape');assert(!await page.locator('#pointSidebar').isVisible(),'Escape did not close drawer');
  await page.locator('#toggleExecutionSidebar').click();assert(await page.locator('#executionPanel').isVisible(),'mobile execution drawer cannot open');
  await page.keyboard.press('Escape');
  const mobile=await page.evaluate(()=>({width:document.querySelector('.prediction-main').getBoundingClientRect().width,
    fits:document.querySelector('.chat-composer').getBoundingClientRect().bottom<innerHeight,overflow:document.documentElement.scrollWidth>innerWidth}));
  assert(mobile.width>360&&mobile.fits&&!mobile.overflow,'mobile layout overflow');
  await page.screenshot({path:'work/chat-layout-mobile.png'});
  assert(posts===0,'layout test attempted a model POST');
  return {desktop,mobile,collapsibleAttachments:true,sideDrawers:true,modelPosts:posts};
}
