// Display styling on an isolated fake gateway; no real model calls.
async page=>{
  const base='http://127.0.0.1:8891',assert=(v,m)=>{if(!v)throw Error(m);};
  assert((await(await page.request.get(base+'/api/test-fake-gateway')).json()).fake_gateway,'fake required');
  await page.setViewportSize({width:1700,height:1100});await page.goto(base+'/incremental');await page.waitForFunction(()=>!document.querySelector('#pointFileImport').disabled);
  const platform={name:'A',start_ll:[30,122],heading_deg:0,speed_kn:0,hull_m:[0,0],tow_m:[-150,0]};
  const data={platform_config:{platforms:[platform]},candidates:[40,100,41,101].flatMap((b,i)=>['hull','tow'].map(array=>({candidate_id:array+i,t:i*10,platform_id:'A',array,bearing_deg:b-(array==='tow'?5:0)})))};
  await page.locator('#pointFileImport').setInputFiles({name:'solid-colors.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(()=>document.querySelector('#batchSource').textContent.includes('已保留8点'));
  await page.locator('#sequenceMode').selectOption('sequential');await page.locator('#samplePointCount').fill('4');await page.locator('#samplePoints').click();await page.locator('#chatQuestion').fill('TEST_ONLY_UNKNOWN_CANDIDATE');await page.locator('#chatQuestion').dispatchEvent('input');
  await page.locator('#prepareSequence').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('已免费准备4组'));
  await page.locator('#sequenceConsent').check();await page.locator('#sequenceStart').click();await page.waitForFunction(()=>document.querySelector('#sequenceStatus').textContent.includes('completed_awaiting_review'),{},{timeout:30000});
  const styles=()=>page.locator('#sequenceSpatialChart [data-spatial-line]').evaluateAll(nodes=>nodes.map(n=>({chain:n.dataset.spatialChain,color:n.getAttribute('stroke'),dash:n.getAttribute('stroke-dasharray')})));
  const lines=await styles();assert(lines.length===2,'expected two temporary chains');assert(new Set(lines.map(l=>l.color)).size===2,'distinct chains share color');assert(lines.every(l=>l.dash==='none'),'line is still dashed');
  const dots=await page.locator('#sequenceSpatialChart circle').evaluateAll(nodes=>nodes.map(n=>({chain:n.dataset.spatialChain,color:n.getAttribute('stroke')})));
  assert(dots.length===4&&dots.every(d=>lines.some(l=>l.chain===d.chain&&l.color===d.color)),'points and chain lines differ in color');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#sequenceSpatialChart [data-spatial-line]').length===2);assert(JSON.stringify(await styles())===JSON.stringify(lines),'colors changed after refresh');
  await page.locator('#sequenceMirrorSide').selectOption('left');assert(JSON.stringify(await styles())===JSON.stringify(lines),'mirror change recolored same chains');
  const sid=await page.locator('#sessionId').inputValue(),s=await(await page.request.get(base+'/api/incremental/mixed-sessions/'+sid)).json();assert(s.calls===3&&Object.keys(s.tracks).length===0,'display altered cost/confirmed state');
  await page.locator('#sequenceWorkingPreview').screenshot({path:'output/supplied-zip-test/solid-chain-colors.png'});
  return {twoChainsTwoColors:true,solidLines:true,matchingPointColors:true,stableAfterReloadAndMirrorChange:true,fakeCalls:3,realCalls:0};
}
