const assert=require('node:assert/strict');
const {spatialDisplayGroups:display}=require('../point-sampling.js');
const units=[{unit_id:'U1',candidate_ids:['h1','t1'],validated:true},{unit_id:'U2',candidate_ids:['h2','t2'],validated:true}];
const track=(id,side)=>({id,platform_id:'A',pair_fragments:['F1','F2'],handedness:[side,side],points:units.map((u,i)=>({unit_id:u.unit_id,candidate_ids:u.candidate_ids,t:i*10,x:side*(100+i*10),y:200+i*10,stable:true,connect_previous:i>0}))});
const doc={tracks:[track('C1',1),track('C2',-1)],branch_selection_source:'model',model_branch_choices:{U1:'mm',U2:'mm'}};
for(const side of ['left','right']){
  const d=display(doc,units,[],{mirrorSide:side,autoConnect:true,modelOnly:true});
  assert.equal(d.plotted,2);assert(d.groups.every(g=>g.selected.point.x<0));
  assert(d.groups.every(g=>g.selection_basis.type==='model_selected_branch'));
}
doc.model_branch_choices.U2=null;
let d=display(doc,units,[],{mirrorSide:'right',autoConnect:true,modelOnly:true});
assert.equal(d.plotted,1);assert.equal(d.groups[1].selected,null);assert.equal(d.display_links.length,0);
doc.model_branch_choices.U2='mp';
d=display(doc,units,[],{mirrorSide:'right'});assert.equal(d.groups[1].selected,null);
console.log('PASS: frontend draws only frozen model branch; no mirror/default/nearest replacement; null and invalid branches stay pending');
