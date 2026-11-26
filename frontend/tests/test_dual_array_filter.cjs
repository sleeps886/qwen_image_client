// M0 policy regression: completeness is diagnostic, never an import filter.
const assert=require('node:assert/strict');
const {summarize}=require('../observation-diagnostics.js');
const p=(id,t,array,platform_id='A')=>({candidate_id:id,t,array,platform_id});
const input=[p('h',1,'hull'),p('t',1,'tow'),p('h2',2,'hull'),p('b2',2,'tow','B'),p('t3',3,'tow'),p('h3',3,'hull'),p('h4',3,'hull')];
const before=JSON.stringify(input),stats=summarize(input);
assert.equal(stats.point_count,7);assert.equal(stats.strict_dual_group_count,2);assert.equal(stats.single_array_group_count,2);
assert.equal(stats.strict_dual_point_count,5);assert.equal(stats.hull_point_count,4);assert.equal(stats.tow_point_count,3);
assert.equal(summarize([p('h',1,'hull'),p('t',1.0001,'tow')]).strict_dual_group_count,0);
assert.equal(summarize([p('h',1,'hull'),p('t',1,'tow','B')]).strict_dual_group_count,0);
assert.equal(summarize([p('one',37,'tow')]).point_count,1);
assert.equal(summarize([p('one',37,'tow')]).single_array_group_count,1);
assert.equal(summarize([]).point_count,0);assert.deepEqual(summarize([...input].reverse()),stats);
assert.equal(stats.target_pairing_confirmed,false);assert.equal(JSON.stringify(input),before);
console.log('PASS: all sparse points retained; exact-time diagnostic only; no target pairing; no mutation');
