// Layout-only check: no model requests or persisted draft changes.
async (page) => {
  let posts=0;
  await page.route('**/api/**',async route=>{
    if(route.request().method()==='POST'){posts++;await route.abort();return;}
    await route.continue();
  });
  await page.reload();
  await page.waitForSelector('#toggleComposer');
  const assert=(ok,message)=>{if(!ok)throw new Error(message);};
  const results=[];
  for(const viewport of [{width:1440,height:960},{width:390,height:844}]){
    await page.setViewportSize(viewport);
    assert(!await page.locator('#chatOptions').evaluate(el=>el.open),'options should start folded');
    const before=await page.evaluate(()=>({
      composer:document.querySelector('.chat-composer').getBoundingClientRect().height,
      messages:document.querySelector('#chatMessages').getBoundingClientRect().height,
      draft:document.querySelector('#chatQuestion').value,
      consent:document.querySelector('#chatConsent').checked
    }));
    assert(before.composer<=viewport.height*.3+2,'composer occupies too much viewport');
    assert(before.messages>viewport.height*.4,'conversation has insufficient space');
    await page.locator('#toggleComposer').click();
    assert(!await page.locator('#chatQuestion').isVisible(),'collapsed input still visible');
    const compact=await page.locator('.chat-composer').boundingBox();
    assert(compact.height<60,'collapsed composer too tall');
    await page.locator('#toggleComposer').click();
    assert(await page.locator('#chatQuestion').inputValue()===before.draft,'draft lost');
    assert(await page.locator('#chatConsent').isChecked()===before.consent,'consent changed');
    const noOverlap=await page.evaluate(()=>document.querySelector('#chatMessages').getBoundingClientRect().bottom<=document.querySelector('.chat-composer').getBoundingClientRect().top+1);
    assert(noOverlap,'composer covers conversation');
    results.push({viewport,height:before.composer,collapsedHeight:compact.height,noOverlap});
  }
  await page.screenshot({path:'work/compact-composer-mobile.png'});
  assert(posts===0,'unexpected POST');
  return {results,modelPosts:posts};
}
