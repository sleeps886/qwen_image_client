async page=>{
 const assert=(v,m)=>{if(!v)throw Error(m);};
 await page.goto('http://127.0.0.1:8766/studio/advanced');await page.waitForFunction(()=>document.querySelector('#previewState').textContent==='配置已验证');
 const cfg=await(await page.request.get('http://127.0.0.1:8766/studio/defaults')).json();
 cfg.count=1;cfg.duration=5;cfg.dt=1;cfg.bearing_step=2;
 cfg.axes={platform_count:[2],motion:['moving'],target_count:[2],distance:['mid'],path:['straight'],noise:['normal']};
 cfg.ranges.appear=[0,0];cfg.ranges.disappear=[1,1];cfg.observation_sampling='random_sparse';cfg.observation_keep_percent=50;
 await page.evaluate(async config=>{applyConfig(config);await preview();},cfg);
 await page.locator('#startBtn').click();await page.waitForFunction(()=>['completed','completed_with_errors','failed'].includes(document.querySelector('#taskBadge').dataset.state),{timeout:60000});
 assert(await page.locator('#taskBadge').getAttribute('data-state')==='completed','batch failed');
 const rows=page.locator('.target-package-row');assert(await rows.count()===2,'must be two packages, not four platform/target packages');
 assert((await rows.nth(0).textContent()).includes('12条观测 / 6个真值时刻 / 2个平台'),'wrong per-target counts');
 for(let i=0;i<2;i++){const download=page.waitForEvent('download');await rows.nth(i).locator('a').click();const file=await download;assert(file.suggestedFilename().endsWith('.zip'),'not archive download');await file.saveAs('output/playwright/target-package-'+(i+1)+'.zip');}
 const first=await rows.nth(0).locator('a').getAttribute('href');await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.target-package-row').length===2);assert(await page.locator('.target-package-row a').first().getAttribute('href')===first,'packages lost on reload');
 await page.locator('#targetExports').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/target-packages.png'});
 return {actualBatch:true,targets:2,platforms:2,packages:2,zipDownloads:2,perTargetObservations:12,perTargetTruth:6,reload:true,modelCalls:0};
}
