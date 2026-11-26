async (page) => {
  await page.setViewportSize({width:1440,height:1000});
  await page.waitForFunction(()=>document.getElementById('chatInput').value.includes('seed_candidate_ids'));
  const sidebar=page.locator('#pointSidebar'),chat=page.locator('.prediction-chat');
  if(!await sidebar.locator('#pointDetailSelect').count())throw new Error('point property menu not in sidebar');
  if(!await sidebar.locator('#pointDetailFields').count())throw new Error('point property card missing');
  if(await chat.locator('#chatInput').count())throw new Error('duplicate input remained in chat');
  const left=await sidebar.boundingBox(),right=await chat.boundingBox();
  if(left.x+left.width>right.x)throw new Error('desktop columns overlap');
  const points=JSON.parse(await page.locator('#chatInput').inputValue()).point_data.history;
  if(points.length!==3)throw new Error('preloaded points changed');
  await page.screenshot({path:'work/point-sidebar-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  const mobileLeft=await sidebar.boundingBox(),mobileChat=await chat.boundingBox();
  if(mobileLeft.y+mobileLeft.height>mobileChat.y)throw new Error('mobile data panel is not stacked above chat');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1))throw new Error('mobile horizontal overflow');
  return {desktopSidebar:true,mobileStacked:true,preloadedPoints:points.length,modelCalls:0};
}
