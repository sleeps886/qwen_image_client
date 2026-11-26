// Manual retry UI, with all inference confined to the isolated fake gateway.
async page=>{
 const base=new URL(page.url()).origin,assert=(v,m)=>{if(!v)throw new Error(m);};
 assert((await (await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake server required');
 await page.setViewportSize({width:1600,height:1100});
 const load=async id=>{await page.goto(base+'/incremental?empty=1');await page.getByText('导入数据与参数（免费）',{exact:true}).click();await page.locator('#sessionId').fill(id);await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#rerunPanel').hidden);};
 // Same situation as the user's screenshot: no new selection after a local recovery.
 const recovered=await (await page.request.get(base+'/api/test-legacy-fragment-review')).json();
 let url=base+'/api/incremental/mixed-sessions/'+recovered.session_id;
 await page.request.post(url+'/recover-model-plot',{data:{version:2}});await load(recovered.session_id);
 const before=(await (await page.request.get(url)).json()).calls;
 assert((await page.locator('#selectedSummary').textContent()).includes('0点'),'expected empty current selection');
 await page.locator('#prepareRerun').click();await page.waitForFunction(()=>!document.querySelector('#chatComposer').hidden&&!document.querySelector('#chatPrepare').disabled);
 assert((await page.locator('#selectedSummary').textContent()).includes('2点'),'previous round points not restored');
 assert(!await page.locator('#chatConsent').isChecked()&&await page.locator('#chatSend').isDisabled(),'new request inherited fee consent');
 assert((await (await page.request.get(url)).json()).calls===before,'free preparation spent a call');
 await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#chatComposer').hidden&&!document.querySelector('#chatPrepare').disabled);
 assert((await page.locator('#selectedSummary').textContent()).includes('2点'),'reload lost retry selection');
 assert(!await page.locator('#chatConsent').isChecked(),'reload granted fee consent');
 await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
 await page.waitForFunction(()=>!document.querySelector('#trajectoryReview').hidden&&!document.querySelector('#adoptTrajectory').disabled);
 let state=await (await page.request.get(url)).json();assert(state.calls===before+1,'manual send did not make exactly one fake call');
 assert(state.rounds.length===2&&state.prediction_ledger.length===1,'old round or prediction ledger overwritten');
 await page.screenshot({path:'output/playwright/manual-rerun-association.png',fullPage:true});
 // Known-invalid association can also be reopened before any submitted points exist.
 const invalid=await (await page.request.get(base+'/api/test-rerun-fixture?phase=association')).json();await load(invalid.session_id);
 await page.locator('#prepareRerun').click();await page.waitForFunction(()=>!document.querySelector('#chatPrepare').disabled);
 assert((await page.locator('#selectedSummary').textContent()).includes('2点'),'failed association input not restored');
 assert(!await page.locator('#chatConsent').isChecked(),'failed association inherited consent');
 // Unknown provider result requires explicit acknowledgement, then another plot consent.
 const unknown=await (await page.request.get(base+'/api/test-rerun-fixture?phase=plot&unknown=true')).json();await load(unknown.session_id);
 assert(await page.locator('#prepareRerun').isDisabled(),'unknown result was silently reopened');
 await page.locator('#rerunUnknownAck').check();await page.locator('#prepareRerun').click();
 await page.waitForFunction(()=>!document.querySelector('#modelPlotPanel').hidden&&!document.querySelector('#prepareModelPlot').disabled);
 assert(!await page.locator('#modelPlotConsent').isChecked()&&await page.locator('#runModelPlot').isDisabled(),'plot retry inherited fee consent');
 state=await (await page.request.get(base+'/api/incremental/mixed-sessions/'+unknown.session_id)).json();assert(state.calls===2,'plot preparation spent a call');
 assert(state.rerun_history[0].unknown_cost_acknowledged,'unknown-cost acknowledgement not recorded');
 await page.screenshot({path:'output/playwright/manual-rerun-plot.png',fullPage:true});
 return {zeroNewPointsSupported:true,failedAssociationSupported:true,unknownCostAcknowledged:true,freshConsentRequired:true,reloadRetention:true,oldHistoryPreserved:true,realModelCalls:0};
}
