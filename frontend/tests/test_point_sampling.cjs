const assert=require('node:assert/strict');
const {sampleOrdered}=require('../point-sampling.js');
const points=Array.from({length:1224},(_,i)=>({candidate_id:'O'+i,t:i,bearing_deg:i*.01}));
const original=JSON.stringify(points);
for(const value of [0,.01,.5,.99,1-Number.EPSILON]){
  for(const count of [1,2,3,10,100,1223,1224]){
    const selected=sampleOrdered(points,count,{random:()=>value});
    assert.equal(selected.length,count);
    assert.equal(new Set(selected).size,count,'must never repeat a point');
    assert.ok(selected.every(p=>points.includes(p)),'must return original points');
    assert.ok(selected.every((p,i)=>i===0||p.t>selected[i-1].t),'order must be retained');
    if(count>1){assert.equal(selected[0],points[0]);assert.equal(selected.at(-1),points.at(-1));}
  }
}
// Exhaust dense cases where rounding can otherwise produce duplicate indices.
for(let length=1;length<=40;length++)for(let count=1;count<=length;count++){
  let n=0;const selected=sampleOrdered(points.slice(0,length),count,{random:()=>n++%2?.999:.001});
  assert.equal(selected.length,count);assert.equal(new Set(selected).size,count);
}
const count=100,step=(points.length-1)/(count-1);
const lower=sampleOrdered(points,count,{random:()=>0});
const upper=sampleOrdered(points,count,{random:()=>1-Number.EPSILON});
assert.notDeepEqual(lower,upper,'different random draws should vary the internal positions');
for(let i=1;i<count-1;i++)for(const selected of [lower,upper])assert.ok(Math.abs(selected[i].t-i*step)<=.2*step+.5,'jitter must stay small');
assert.equal(JSON.stringify(points),original,'sampling must not mutate observations');
assert.deepEqual(sampleOrdered(points,points.length),points);
for(const value of [0,-1,1.2,NaN,Infinity,1225])assert.throws(()=>sampleOrdered(points,value));
assert.throws(()=>sampleOrdered([],1));
assert.throws(()=>sampleOrdered(points,5,{random:()=>NaN}));
console.log('PASS: exact count, original data, unique IDs, small jitter, endpoints, dense cases, validation');
const {sampleSynchronousGroups}=require('../point-sampling.js');
const grouped=[{candidate_id:'a-h1',t:1,platform_id:'A',array:'hull'},{candidate_id:'a-h2',t:1,platform_id:'A',array:'hull'},
  {candidate_id:'a-t1',t:1,platform_id:'A',array:'tow'},{candidate_id:'a-h3',t:3,platform_id:'A',array:'hull'},
  {candidate_id:'a-t3',t:3,platform_id:'A',array:'tow'},{candidate_id:'single',t:2,platform_id:'A',array:'tow'}];
const groupedBefore=JSON.stringify(grouped),selectedGroups=sampleSynchronousGroups(grouped,2);
assert.equal(selectedGroups.length,5,'group count is not point count; all candidates kept');
assert.ok(!selectedGroups.some(p=>p.candidate_id==='single'),'explicit group mode should not silently add other times');
assert.equal(JSON.stringify(grouped),groupedBefore,'unselected sparse data must remain in source');
assert.throws(()=>sampleSynchronousGroups(grouped,3),/只有2组/);
assert.throws(()=>sampleSynchronousGroups([grouped[0],grouped.at(-1)],1),/单阵/);
console.log('PASS: optional synchronous groups; complete candidate groups; original sparse points preserved');
