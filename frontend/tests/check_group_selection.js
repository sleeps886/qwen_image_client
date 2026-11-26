// Local fake gateway required. Tests synchronous hull/tow bundles, never paid calls.
async (page) => {
  const origin=new URL(page.url()).origin,marker=await page.request.get(origin+'/api/test-fake-gateway');
  if(!marker.ok()||!(await marker.json()).fake_gateway)throw new Error('Fake gateway test server required');
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  await page.setViewportSize({width:1600,height:1000});
  await page.evaluate(async()=>{await WorkspaceCache.remove('prediction-workspace');});
  await page.goto(origin+'/incremental?source_result=20261124_105150_802e63a7');
  await page.waitForFunction(()=>document.querySelectorAll('#targetTrackSelect option').length>1);
  await page.locator('#targetTrackSelect').selectOption({index:1});
  await page.waitForFunction(()=>document.querySelectorAll('#candidateGroupList input:checked').length===3);
  assert(await page.locator('#chatPointTable tbody tr').count()===3,'initial groups not default-selected');
  const table=await page.locator('#chatPointTable').textContent();
  assert(table.includes('F0'),'starting target bundle missing primary measurements');
  const snapshot=await page.evaluate(()=>WorkspaceCache.get('prediction-workspace'));
  const start=Math.max(...snapshot.dataset.candidates.filter(p=>snapshot.seeds.split(',').includes(p.candidate_id)).map(p=>p.t));
  const syncTime=snapshot.dataset.candidates.filter(p=>p.t>start&&p.array==='hull').find(p=>snapshot.dataset.candidates.some(q=>q.t===p.t&&q.array==='tow')).t;
  const future=page.locator('#candidateGroupList').getByRole('checkbox',{name:'选择时间组 '+syncTime+'秒 A',exact:true});
  const key=await future.getAttribute('data-group-key'),time=JSON.parse(key)[1];
  const before=await page.locator('#chatQuestion').inputValue();
  await page.locator('#candidateGroupList').getByRole('checkbox',{name:'选择时间组 '+time+'秒 '+JSON.parse(key)[0],exact:true}).check();
  assert(await page.locator('#chatPointTable tbody tr').count()===4,'checked group not mirrored in send table');
  const question=await page.locator('#chatQuestion').inputValue();
  assert(question!==before&&question.includes(time+'秒')&&question.includes('tow'),'question did not follow bundle selection');
  await page.locator('#chatConsent').check();await page.locator('#chatSend').click();
  await page.waitForFunction(()=>document.querySelectorAll('#chatMessages .assistant').length===1&&!document.getElementById('chatPrepare').disabled);
  const sid=await page.locator('#sessionId').inputValue(),state=await (await page.request.get(origin+'/api/incremental/sessions/'+sid)).json();
  assert(state.rounds.length===1,'grouped round did not commit');
  const group=state.rounds[0].input.selected_time_groups.find(g=>g.t===time);
  assert(group?.hull.length>0&&group?.tow.length>0,'model did not receive both arrays');
  assert(state.rounds[0].input.visible_until===time,'explicit selected time not reflected in evidence boundary');
  assert(state.rounds[0].input.group_policy.includes('不等于'),'bundle was claimed as confirmed target pairing');
  return {defaultStartingGroups:3,selectedGroups:4,bothArraysReceived:true,questionUpdated:true,actualRoundSaved:true,realModelCalls:0};
}
