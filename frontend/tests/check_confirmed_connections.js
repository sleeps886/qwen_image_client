// Display-only test fixture. No model invocation; requires the isolated fake server.
async page=>{
 const origin=new URL(page.url()).origin;
 if(!(await (await page.request.get(origin+'/api/test-fake-gateway')).json()).fake_gateway)throw new Error('Isolated fake server required');
 const assert=(v,m)=>{if(!v)throw new Error(m);};
 const point=(candidate_id,t,bearing_deg)=>({candidate_id,t,bearing_deg,platform_id:'P',array:'hull'});
 const history=[point('last',300,60),point('first',0,30),point('near',10,31),point('middle',20,32)];
 const gray=point('unassigned',150,80),ambiguous=point('ambiguous',180,75),other=point('other',400,100),raw=point('raw',500,110);
 const data={candidates:[...history,gray,ambiguous,other,raw]};
 const created=await (await page.request.post(origin+'/api/incremental/mixed-sessions',{data:{dataset:data}})).json();
 const tid=created.session_id;
 const tracks={T001:{history,prediction:[],model_segments:[],plot_source:'model',max_connected_gap_s:30,reason:'测试已确认集合'},T002:{history:[other],prediction:[],model_segments:[],plot_source:'model',reason:'单点假设'}};
 let state={...created,version:2,status:'ready',plot_status:'generated',tracks,confirmed_tracks:tracks,observations:data.candidates.slice(0,-1),submitted_ids:data.candidates.slice(0,-1).map(p=>p.candidate_id),unassigned_ids:['unassigned'],confirmed_unassigned_ids:['unassigned'],ambiguities:[{candidate_id:'ambiguous',possible_tracks:['T001','T002'],reason:'暂缓'}],confirmed_ambiguities:[{candidate_id:'ambiguous',possible_tracks:['T001','T002'],reason:'暂缓'}],rounds:[{revision:1,input:{step_id:1,revision_mode:'incremental'},review_status:'adopted',tracks,unassigned_ids:['unassigned'],ambiguities:[]}],prediction_ledger:[]};
 await page.route(origin+'/api/incremental/mixed-sessions/'+tid,route=>route.fulfill({json:state}));
 await page.goto(origin+'/incremental?empty=1');await page.setViewportSize({width:1600,height:1100});
 await page.getByText('导入数据与参数（免费）',{exact:true}).click();await page.locator('#sessionId').fill(tid);await page.locator('#reload').click();
 await page.waitForFunction(()=>document.querySelectorAll('#predictionChart line[data-kind="confirmed_association_link"]').length===3);
 const edges=await page.locator('#predictionChart line[data-kind="confirmed_association_link"]').evaluateAll(items=>items.map(n=>({from:n.dataset.from,to:n.dataset.to,gap:n.dataset.gap,dash:n.getAttribute('stroke-dasharray')})));
 assert(JSON.stringify(edges.map(e=>[e.from,e.to]))===JSON.stringify([['first','near'],['near','middle'],['middle','last']]),'not linked in real chronological order');
 assert(edges[2].gap==='true'&&edges[2].dash==='3 6','long gap link absent or falsely solid');
 assert(edges.every(e=>!['unassigned','ambiguous','raw'].includes(e.from)&&!['unassigned','ambiguous','raw'].includes(e.to)),'candidate or ambiguous point connected');
 await page.getByRole('checkbox',{name:'整行 P 500秒',exact:true}).check();
 assert(await page.locator('#predictionChart line[data-kind="confirmed_association_link"]').count()===3,'raw checkbox automatically joined a target');
 await page.screenshot({path:'output/playwright/confirmed-target-connections.png',fullPage:true});
 assert((await (await page.request.get(origin+'/api/incremental/mixed-sessions/'+tid)).json()).calls===0,'display triggered a model call');
 await page.unroute(origin+'/api/incremental/mixed-sessions/'+tid);
 return {allConfirmedLinked:true,longGapConnectedDashed:true,candidatesExcluded:true,rawSelectionNotAssociated:true,realModelCalls:0};
}
