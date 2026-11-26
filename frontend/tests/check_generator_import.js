async page=>{
 const assert=(v,m)=>{if(!v)throw Error(m);};
 await page.goto('http://127.0.0.1:8891/generator');
 await page.locator('#startTool').click();await page.waitForFunction(()=>!document.querySelector('#startTool').disabled);
 assert((await page.locator('#toolStatus').textContent()).includes('已启动'),'tool start failed');
 const params={T_MAX:5,DT:1,DB_STEP:2,platforms:[{name:'A',start_ll:[30,122],heading_deg:30,speed_kn:0,hull_m:[0,0],tow_m:[-150,0]}],targets:[{name:'TargetForTest',start:[30.01,122.01],speed_kn:19,amp:.3,sigma_hull:4,sigma_tow:1.6,t_on:1,t_off:4,waypoints:[[30.02,122.03]]}]};
 const res=await(await page.request.post('http://127.0.0.1:8765/run',{data:params})).json();assert(res.ok,'simulation failed');
 const paths=res.platforms[0].data_files;
 const data=await(await page.request.get('http://127.0.0.1:8765'+paths.observations)).json();
 const reference=await(await page.request.get('http://127.0.0.1:8765'+paths.reference)).json();
 assert(data.candidates.length===8&&reference.points.length===4,'wrong count');assert(!JSON.stringify(data).includes('TargetForTest'),'identity leaked');
 await page.goto('http://127.0.0.1:8891/incremental?empty=1');await page.waitForFunction(()=>document.querySelector('#backendReadiness').textContent.includes('已就绪'));
 await page.locator('#pointFileImport').setInputFiles({name:'btr_A_observations.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
 await page.waitForFunction(()=>document.querySelectorAll('.point-file').length>0);assert((await page.locator('#batchSource').textContent()).includes('保留8点'),'import rejected');assert((await page.locator('#pointFileList').textContent()).includes('btr_A_observations.json'),'filename missing');
 await page.locator('#pointFileImport').setInputFiles({name:'reference.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(reference))});await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);assert((await page.locator('#chatNotice').textContent()).includes('请选择 observations.json'),'truth accepted');
 await page.screenshot({path:'output/playwright/generator-file-import.png',fullPage:true});return {toolStart:true,actualSimulationExport:true,observationsImported:true,referenceRejected:true,modelCalls:0};
}
