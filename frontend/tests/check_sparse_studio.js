async page=>{
 const assert=(v,m)=>{if(!v)throw Error(m);};
 await page.goto('http://127.0.0.1:8766/studio/advanced');
 await page.waitForFunction(()=>document.querySelector('#previewState').textContent==='配置已验证');
 const cfg=await(await page.request.get('http://127.0.0.1:8766/studio/defaults')).json();
 cfg.count=1;cfg.duration=20;cfg.dt=1;cfg.bearing_step=2;
 cfg.axes={platform_count:[1],motion:['stationary'],target_count:[2],distance:['mid'],path:['straight'],noise:['normal']};
 cfg.ranges.appear=[0,0];cfg.ranges.disappear=[1,1];
 await page.evaluate(async config=>{applyConfig(config);await preview();},cfg);
 await page.locator('#observation_sampling').selectOption('random_sparse');
 await page.locator('#observation_keep_percent').fill('25');
 await page.waitForFunction(()=>document.querySelector('#previewState').textContent==='配置已验证');
 assert(await page.locator('#observation_keep_percent').isEnabled(),'ratio disabled');
 await page.locator('#startBtn').click();
 await page.waitForFunction(()=>['completed','completed_with_errors','failed'].includes(document.querySelector('#taskBadge').dataset.state),{timeout:60000});
 assert(await page.locator('#taskBadge').getAttribute('data-state')==='completed','batch failed');
 const obs=page.locator('#downloads a').filter({hasText:'下载散点／观测数据'}),truth=page.locator('#downloads a').filter({hasText:'下载完整轨迹真值'});
 const data=await(await page.request.get(new URL(await obs.getAttribute('href'),page.url()).href)).json(),ref=await(await page.request.get(new URL(await truth.getAttribute('href'),page.url()).href)).json();
 assert(data.sampling.mode==='random_sparse'&&data.sampling.retained_times===5,'wrong sparse time selection');
 assert(data.candidates.length===20&&ref.points.length===42,'not sparse input with full truth');
 assert(!JSON.stringify(data).includes('target_id'),'input identity leak');
 assert(await page.locator('#downloads a').filter({hasText:'下载对应空间轨迹图'}).count()===1,'trajectory download absent');
 assert(await page.locator('#downloads a').filter({hasText:'下载观测散点图'}).count()===1,'scatter download absent');
 await page.screenshot({path:'output/playwright/sparse-studio-result.png',fullPage:true});
 return {advancedPage:true,actualBatch:true,retainedTimes:5,observations:20,fullTruth:42,scatterAndTrackDownloads:true,modelCalls:0};
}
