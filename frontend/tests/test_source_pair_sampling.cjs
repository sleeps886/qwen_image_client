const assert=require('node:assert/strict');
const {sourcePairGroups,sampleSourcePairs}=require('../point-sampling.js');
const {groupMarkers}=require('../point-sampling.js');
const points=[],catalog=[];
for(let source=0;source<3;source++){
  const group={target_id:'source-'+source,candidate_ids:[]};catalog.push(group);
  for(let t=0;t<40;t++)for(const array of ['hull','tow']){const p={candidate_id:`${source}-${t}-${array}`,platform_id:'A',array,t,bearing_deg:t+source};points.push(p);group.candidate_ids.push(p.candidate_id);}
}
const before=JSON.stringify({points,catalog});
assert.equal(sourcePairGroups(points,catalog).length,120);
for(const count of [1,20,100,120]){
  const selected=sampleSourcePairs(points,count,catalog,{random:()=>.5});assert.equal(selected.length,count*2);
  const groups=sourcePairGroups(selected,catalog);assert.equal(groups.length,count);
  const markers=groupMarkers(selected,catalog);assert.equal(markers.length,count);
  assert.ok(markers.every(m=>m.point.array==='hull'&&m.observations.length===2));
  assert.deepEqual(new Set(markers.flatMap(m=>m.observations)),new Set(selected));
  for(const g of groups){assert.equal(g.candidates.length,2);assert.equal(new Set(g.candidates.map(p=>p.array)).size,2);assert.ok(g.candidates.every(p=>points.includes(p)));}
}
assert.equal(JSON.stringify({points,catalog}),before);
const cross=[points[0],points[81]];assert.equal(sourcePairGroups(cross,catalog).length,0,'never pair across source');
assert.equal(sourcePairGroups([points[0],{...points[1],platform_id:'B'}],catalog).length,0);
assert.equal(sourcePairGroups([points[0],{...points[1],t:1}],catalog).length,0);
assert.equal(sourcePairGroups([points[0],points[1],{...points[0],candidate_id:'extra'}],[]).length,0,'multiple candidates not arbitrarily paired');
assert.throws(()=>sampleSourcePairs(points,121,catalog));
assert.throws(()=>sampleSourcePairs(cross,1,catalog));
assert.equal(sourcePairGroups([points[0],points[1]],[]).length,1,'unique unlabelled pair accepted');
assert.equal(groupMarkers([points[0]],catalog).length,1,'never hide unpaired observations');
console.log('PASS: 20 groups = 40 raw observations; source/platform/time isolation; ambiguity and missing arrays retained, never fabricated; no mutation');
