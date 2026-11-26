async page=>{
 const assert=(v,m)=>{if(!v)throw Error(m);};await page.goto('http://127.0.0.1:8766/studio/advanced');await page.waitForFunction(()=>document.querySelector('#previewState').textContent==='配置已验证');
 const job=await(await page.request.get('http://127.0.0.1:8766/batch/status/batch_20261125_161222_53383a')).json();await page.evaluate(j=>showJob(j),job);
 const all=page.getByRole('link',{name:'导出本批全部目标 ZIP ↓',exact:true}),one=page.getByRole('link',{name:'导出当前样本全部目标 ZIP ↓',exact:true}).first();assert(await all.count()===1,'batch button absent');
 for(const [link,name] of [[all,'bulk-all.zip'],[one,'bulk-sample.zip']]){const promise=page.waitForEvent('download');await link.click();const file=await promise;await file.saveAs('output/playwright/'+name);assert(file.suggestedFilename().endsWith('_targets.zip'),'wrong filename');}
 await all.scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/bulk-export-buttons.png'});return {batchButton:true,sampleButton:true,twoDownloads:true,expectedPackages:job.samples.filter(s=>s.state==='completed').reduce((n,s)=>n+(s.target_exports||[]).length,0),modelCalls:0};
}