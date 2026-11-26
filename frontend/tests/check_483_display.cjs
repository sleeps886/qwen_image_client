// Independent numerical fixture, not a completed Agent/model run.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {spatialDisplayGroups}=require('../point-sampling.js');
const root='output/supplied-zip-test/full-483/';
const v=JSON.parse(fs.readFileSync(root+'geometry-fixture-483.json','utf8'));
const start=performance.now();
const d=spatialDisplayGroups(v.spatial_preview,v.units,v.points,{autoConnect:true,mirrorSide:'left',membership:v.membership,edges:[]});
assert.equal(d.groups.length,483);assert.equal(d.plotted+d.groups.filter(g=>!g.selected).length,483);
assert.equal(new Set(d.groups.map(g=>g.unit_id)).size,483);
assert(d.groups.every(g=>g.model_assignment.length===2));
assert(d.auto_notice?.includes('160'),'large-set degradation should be explicit');
const report={fixtureGroups:483,mainPositionPoints:d.plotted,noPositionGroups:d.groups.filter(g=>!g.selected).length,
  lines:d.display_links.length,notice:d.auto_notice,elapsedMilliseconds:Math.round(performance.now()-start),
  modelAccuracyEvaluated:false,realModelCalls:0};
fs.writeFileSync(root+'display-report.json',JSON.stringify(report,null,2));
console.log(JSON.stringify(report));
