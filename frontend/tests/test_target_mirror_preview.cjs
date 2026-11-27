const assert=require('node:assert/strict');
const {spatialDisplayGroups:display}=require('../point-sampling.js');
const nav={platforms:[{name:'A',start_ll:[30,122],heading_deg:0,speed_kn:0,hull_m:[0,0],tow_m:[-100,0]}]};
const units=[],points=[],membership={},edges=[];
const tracks=[];
for(const [target,index]of [['B1',0],['B2',1]]){
  const ps=[];
  for(let i=0;i<2;i++){
    const uid=`${target}U${i}`,ids=['h','t'].map(a=>uid+a);
    units.push({unit_id:uid,candidate_ids:ids,validated:true});
    for(const [j,id]of ids.entries()){
      points.push({candidate_id:id,t:i*10,platform_id:'A',array:j?'tow':'hull'});
      membership[id]={target_id:target,fragment_id:`${target}F${j}`,status:'associated'};
      if(i)edges.push({from_id:`${target}U0`+(j?'t':'h'),to_id:id,status:'low_confidence'});
    }
    ps.push({unit_id:uid,candidate_ids:ids,t:i*10,x:100+index*300+i*10,y:200+i*10,stable:true,connect_previous:!!i});
  }
  for(const side of [1,-1])tracks.push({id:target+side,platform_id:'A',pair_fragments:[target+'F0',target+'F1'],handedness:[side,side],points:ps.map(p=>({...p,x:p.x*side}))});
}
const doc={origin_lat_lon:[30,122],tracks,branch_selection_source:'model',model_branch_choices:Object.fromEntries(units.map(u=>[u.unit_id,'pp']))};
const opts={edges,membership,autoConnect:true,modelOnly:true,platformConfig:nav};
const before=JSON.stringify({doc,units,points,membership,nav});
let r=display(doc,units,points,opts);
assert.equal(r.plotted,4);assert.equal(r.mirror_targets.length,2);assert(r.mirror_targets.every(t=>t.can_switch));
r=display(doc,units,points,{...opts,targetMirrorChoices:{B1:'mirror'}});
assert(r.groups.filter(g=>g.target_id==='B1').every(g=>g.selected.point.x<0&&g.model_selected.point.x>0&&g.selection_basis.type==='manual_target_mirror_preview'));
assert(r.groups.filter(g=>g.target_id==='B2').every(g=>g.selected.point.x>0&&g.selection_basis.type==='model_selected_branch'));
assert.equal(r.display_links.length,2);assert.equal(r.mirror_tracks.length,0);
assert(r.tracks.filter(t=>t.points[0].candidate_ids[0].startsWith('B1')).every(t=>t.points.every(p=>p.x<0&&p.manual_display_override)));
r=display(doc,units,points,{...opts,targetMirrorChoices:{B1:'both'}});
assert.equal(r.plotted,4);assert.equal(r.mirror_tracks.length,1);assert.equal(r.mirror_tracks[0].points.length,2);
assert(r.groups.every(g=>g.selected.point.x>0));assert(r.mirror_tracks[0].points.every(p=>p.x<0));
assert.equal(r.mirror_tracks[0].points[1].connect_previous,true);
assert.equal(JSON.stringify({doc,units,points,membership,nav}),before,'display switch mutated original results');
// Missing/null model choices are never repaired by mirror preview.
const pending=structuredClone(doc);pending.model_branch_choices.B1U1=null;
r=display(pending,units,points,{...opts,targetMirrorChoices:{B1:'both'}});
assert.equal(r.plotted,3);assert.equal(r.groups.find(g=>g.unit_id==='B1U1').selected,null);
assert.equal(r.mirror_tracks[0].points.length,1);assert.equal(r.display_links.length,1);
// No navigation or no corresponding valid candidate => no invented reflection.
r=display(doc,units,points,{...opts,platformConfig:undefined,targetMirrorChoices:{B1:'mirror'}});
assert(r.groups.every(g=>g.selected.point.x>0));assert.equal(r.mirror_targets[0].can_switch,false);
const noMirror=structuredClone(doc);noMirror.tracks=noMirror.tracks.filter(t=>t.id!=='B1-1');
r=display(noMirror,units,points,{...opts,targetMirrorChoices:{B1:'mirror'}});
assert.equal(r.mirror_targets[0].can_switch,false);assert(r.groups.every(g=>g.selected.point.x>0));
// Moving oblique baseline: reflection is not a screen-vertical flip.
const heading=35*Math.PI/180,speed=5*1852/3600,t=10,travel=speed*t;
const h=[travel*Math.sin(heading),travel*Math.cos(heading)];
const originPoint=[h[0]+200*Math.sin(heading)+100*Math.cos(heading),h[1]+200*Math.cos(heading)-100*Math.sin(heading)];
const reflection=[h[0]+200*Math.sin(heading)-100*Math.cos(heading),h[1]+200*Math.cos(heading)+100*Math.sin(heading)];
const u=[{unit_id:'O',candidate_ids:['oh','ot'],validated:true}],p=[{candidate_id:'oh',t,platform_id:'A',array:'hull'},{candidate_id:'ot',t,platform_id:'A',array:'tow'}];
const d={origin_lat_lon:[30,122],branch_selection_source:'model',model_branch_choices:{O:'pp'},tracks:[
  {id:'p',platform_id:'A',handedness:[1,1],points:[{unit_id:'O',candidate_ids:['oh','ot'],t,x:originPoint[0],y:originPoint[1],stable:true}]},
  {id:'m',platform_id:'A',handedness:[-1,-1],points:[{unit_id:'O',candidate_ids:['oh','ot'],t,x:reflection[0],y:reflection[1],stable:true}]}]};
const config=structuredClone(nav);config.platforms[0].heading_deg=35;config.platforms[0].speed_kn=5;
r=display(d,u,p,{platformConfig:config,membership:{oh:{target_id:'B'},ot:{target_id:'B'}},targetMirrorChoices:{B:'mirror'}});
assert.equal(r.groups[0].selected.point.x,reflection[0]);assert.equal(r.groups[0].selected.point.y,reflection[1]);
assert.notEqual(reflection[1],-originPoint[1]);
console.log('PASS: independent target mirror/both; exact existing geometry only; oblique moving baseline; unchanged model/input; no missing-point fabrication; counts and edges preserved');
