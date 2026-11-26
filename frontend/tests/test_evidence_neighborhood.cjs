'use strict';
const assert=require('node:assert/strict');
const {propose}=require('../evidence-neighborhood.js');
const points=[0,1,2,3,4,5].flatMap(t=>['hull','tow'].map(array=>({candidate_id:array+t,t,platform_id:'A',array,bearing_deg:30+t}))).concat([{candidate_id:'other',t:3,platform_id:'B',array:'hull',bearing_deg:80}]);
const before=JSON.stringify(points),ids=propose(points,[points.find(p=>p.candidate_id==='hull3')]);
assert.equal(ids.length,10);assert(!ids.includes('other'));assert(!ids.includes('hull0'));assert.equal(new Set(ids).size,ids.length);assert(ids.every(id=>points.some(p=>p.candidate_id===id)));assert.equal(JSON.stringify(points),before);assert.deepEqual(propose(points,[]),[]);assert.throws(()=>propose(points,[points[0]],0));
console.log('PASS: optional real-neighborhood selection, same platform, all candidates retained, no generated points, no mutation');
