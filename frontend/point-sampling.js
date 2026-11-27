'use strict';
// Selection only: return existing observations, never modify or interpolate them.
((root)=>{
  function sampleOrdered(items,count,{random=Math.random}={}){
    if(!Number.isSafeInteger(count)||count<1)throw new Error('请输入大于0的整数选点数量');
    if(count>items.length)throw new Error(`当前分组只有${items.length}个观测点，请填写不超过可用点数的数量`);
    if(count===items.length)return items.slice();
    const draw=()=>{const value=random();if(!Number.isFinite(value)||value<0||value>=1)throw new Error('随机数无效');return value;};
    if(count===1)return [items[Math.floor(draw()*items.length)]];
    const result=[],step=(items.length-1)/(count-1);
    let previous=-1;
    for(let i=0;i<count;i++){
      // Preserve the time span; jitter each internal position by <=20% of a
      // nominal rank interval. Clamp to leave room for every remaining point.
      const position=i===0?0:i===count-1?items.length-1:Math.round(i*step+(draw()-.5)*.4*step);
      const index=Math.max(previous+1,Math.min(items.length-count+i,position));
      result.push(items[index]);previous=index;
    }
    return result;
  }
  function groupByPlatformTime(items){
    const rows=new Map();
    for(const point of items){const key=JSON.stringify([point.platform_id,point.t]);if(!rows.has(key))rows.set(key,{platform_id:point.platform_id,t:point.t,candidates:[],arrays:new Set()});const row=rows.get(key);row.candidates.push(point);row.arrays.add(point.array);}
    return [...rows.values()].sort((a,b)=>a.t-b.t||a.platform_id.localeCompare(b.platform_id));
  }
  function sampleSynchronousGroups(items,count,options={}){
    if(!Number.isSafeInteger(count)||count<1)throw new Error('请输入大于0的整数时刻组数');
    const groups=groupByPlatformTime(items).filter(row=>row.arrays.has('hull')&&row.arrays.has('tow'));
    if(!groups.length)throw new Error('当前浏览范围没有严格同步双阵时刻组；单阵/异步点仍保留，可改用按点随机或手动选点');
    if(count>groups.length)throw new Error(`当前浏览范围只有${groups.length}组严格同步双阵时刻，请调整组数或改用按点随机`);
    return sampleOrdered(groups,count,options).flatMap(row=>row.candidates);
  }
  function sourcePairGroups(items,catalog=[]){
    const membership=new Map();
    catalog.forEach((group,i)=>{for(const id of new Set(group.candidate_ids||[])){if(!membership.has(id))membership.set(id,[]);membership.get(id).push(i);}});
    const buckets=new Map();
    for(const point of items){
      const matches=membership.get(point.candidate_id)||[];
      if(matches.length>1||catalog.length&&!matches.length)continue;
      const source=matches.length?matches[0]:-1,key=JSON.stringify([source,point.platform_id,point.t]);
      if(!buckets.has(key))buckets.set(key,{key,source,platform_id:point.platform_id,t:point.t,candidates:[]});buckets.get(key).candidates.push(point);
    }
    // A pair must be unambiguous within the imported source. Do not zip several
    // candidates arbitrarily, pair across sources, or synthesize missing arrays.
    return [...buckets.values()].filter(g=>g.candidates.length===2&&g.candidates.filter(p=>p.array==='hull').length===1&&g.candidates.filter(p=>p.array==='tow').length===1)
      .sort((a,b)=>a.t-b.t||a.platform_id.localeCompare(b.platform_id)||a.source-b.source);
  }
  function sampleSourcePairs(items,count,catalog=[],options={}){
    const groups=sourcePairGroups(items,catalog);
    if(!Number.isSafeInteger(count)||count<1)throw new Error('请输入大于0的整数双阵组数');
    if(count>groups.length)throw new Error(`当前范围只有${groups.length}组明确的同来源 hull＋tow 数据，不能抽取${count}组；缺阵或多候选点仍保留，可手动勾选`);
    return sampleOrdered(groups,count,options).flatMap(g=>g.candidates);
  }
  function groupMarkers(items,catalog=[]){
    const mates=new Map();
    for(const group of sourcePairGroups(items,catalog))for(const p of group.candidates)mates.set(p.candidate_id,group.candidates);
    return items.filter(p=>!mates.has(p.candidate_id)||p.array==='hull').map(point=>({point,observations:mates.get(point.candidate_id)||[point]}));
  }
  function groupDetail(records){
    const hull=records.find(p=>p.array==='hull'),tow=records.find(p=>p.array==='tow');
    if(records.length!==2||!hull||!tow)return records.length===1?{...records[0]}:{observations:records};
    const same=(a,b)=>{
      if(a===b)return true;
      if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
      const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));
    };
    const common=Object.keys(hull).filter(k=>!['array','hull','tow'].includes(k)&&Object.hasOwn(tow,k)&&same(hull[k],tow[k]));
    const shared=new Set(common),specific=p=>Object.fromEntries(Object.entries(p).filter(([k])=>k!=='array'&&!shared.has(k)));
    return {...Object.fromEntries(common.map(k=>[k,hull[k]])),hull:specific(hull),tow:specific(tow)};
  }
  function inspectQueue(units,state,budget,options={}){
    if(!Number.isInteger(budget)||budget<0||budget>10000)throw Error('调用上限须为0至10000的整数。');
    if(!Array.isArray(units)||!units.length)throw Error('请先选择至少1组尚未处理的观测。');
    if(state&&(state.active_sequential_run||state.status!=='ready'))throw Error('当前会话有未结束操作，请先完成或明确结束旧队列。');
    const submitted=new Set(state?.submitted_ids||[]);
    const pending=units.filter(ids=>ids.some(id=>!submitted.has(id)));
    if(!pending.length)throw Error('所选组已全部处理，请选择新增观测。');
    const batch=options.mode==='batch-snapshot-v1',size=options.batchSize||50;
    if(batch&&(!Number.isInteger(size)||size<1||size>50))throw Error('每批组数须为1至50的整数。');
    const freeSeed=!batch&&!submitted.size&&!state?.unassigned_ids?.length&&!state?.ambiguities?.length&&!Object.values(state?.tracks||{}).some(t=>t.history?.length);
    const retryBudget=batch?(options.retryBudget||0):0;
    if(!Number.isInteger(retryBudget)||retryBudget<0||retryBudget>1000)throw Error('额外重试预算须为0至1000的整数。');
    const base=batch?pending.length+Math.ceil(pending.length/size):Math.max(0,pending.length-Number(freeSeed)),upper=base+retryBudget;
    if(upper>budget)throw Error(`预算不足：选中${pending.length}组，${freeSeed?'首组免费初始化，':''}最多需要${upper}次关联调用，当前上限${budget}。请将上限至少改为${upper}，或减少选点；尚未创建队列、未调用模型。`);
    if(state&&upper>state.max_calls-state.calls)throw Error(`会话预算不足：最多需要${upper}次，当前会话仅剩${state.max_calls-state.calls}次。请减少组数，或新建会话使用旧文件并重新设置预算；不会自动提高上限。`);
    return {units:pending,upper,base,retryBudget,freeSeed};
  }
  function provisionalGroupLinks(groups,lookup,options){
    const usable=groups.filter(g=>g.validated&&Number.isFinite(g.t)&&g.candidates.length).map(g=>{
      const ps=g.candidate_ids.map(id=>lookup.get(id)).filter(Boolean),h=ps.find(p=>p.array==='hull'),t=ps.find(p=>p.array==='tow');
      const targets=new Set(ps.map(p=>options.membership?.[p.candidate_id]?.target_id).filter(Boolean));
      return {...g,platform:h?.platform_id,hull:h?.bearing_deg,tow:t?.bearing_deg,target:targets.size===1?[...targets][0]:null,conflict:targets.size>1};
    }).filter(g=>Number.isFinite(g.hull)&&Number.isFinite(g.tow)&&!g.conflict).sort((a,b)=>a.t-b.t||a.index-b.index);
    if(usable.length>160)return {links:[],alternatives:[],notice:'可见组超过本地暂接重算上限160组，本图仅保留Agent已校验接续；未将全部点强连。'};
    const newCost=2.4,maxGap=options.maxGapSeconds||180,fold=v=>{const a=((v%360)+360)%360;return Math.min(a,360-a);};
    let beam=[{cost:0,chains:[]}];
    for(const g of usable){
      const expanded=[];
      for(const state of beam){
        expanded.push({cost:state.cost+newCost,chains:[...state.chains,[g]]});
        state.chains.forEach((chain,index)=>{
          const last=chain.at(-1),dt=g.t-last.t;
          if(g.platform!==last.platform||dt<=0||dt>maxGap||g.target&&chain.some(old=>old.target&&old.target!==g.target))return;
          const rate=Math.max(Math.abs(g.hull-last.hull),Math.abs(g.tow-last.tow))/dt;
          if(rate>5)return;
          let cost=.15+Math.pow(rate/1.5,2);
          if(chain.length>1){const prior=chain.at(-2),span=last.t-prior.t;
            if(span>0){const eh=g.hull-fold(last.hull+(last.hull-prior.hull)*dt/span),et=g.tow-fold(last.tow+(last.tow-prior.tow)*dt/span);cost=.05+(eh*eh+et*et)/200;}
          }
          if(cost>newCost*3)return;
          const chains=state.chains.slice();chains[index]=[...chain,g];expanded.push({cost:state.cost+cost,chains});
        });
      }
      expanded.sort((a,b)=>a.cost-b.cost||a.chains.length-b.chains.length);
      beam=expanded.slice(0,24);
    }
    const linksFor=state=>state.chains.flatMap(chain=>chain.slice(1).map((g,i)=>({from:chain[i].unit_id,to:g.unit_id,basis:'local_provisional',reason:'已处理双阵方位连续性的临时划分；后续数据可撤回，不是模型归属或目标真值'})));
    return {links:beam.length?linksFor(beam[0]):[],alternatives:beam.slice(0,3).map(s=>({cost:s.cost,links:linksFor(s)})),notice:''};
  }
  function reflectedCandidate(group,doc,config){
    const selected=group.selected,origin=doc?.origin_lat_lon;
    const nav=config?.platforms?.find(p=>p.name===selected?.platform_id);
    if(!selected||!nav||!Array.isArray(origin)||origin.length!==2||!Number.isFinite(group.t))return null;
    if(![nav.start_ll,nav.hull_m,nav.tow_m].every(v=>Array.isArray(v)&&v.length===2))return null;
    const values=[...origin,...(nav.start_ll||[]),nav.heading_deg,nav.speed_kn,...(nav.hull_m||[]),...(nav.tow_m||[])];
    if(values.length!==10||!values.every(Number.isFinite))return null;
    const heading=nav.heading_deg*Math.PI/180,fx=Math.sin(heading),fy=Math.cos(heading);
    const east=6371000*Math.cos(origin[0]*Math.PI/180)*(nav.start_ll[1]-origin[1])*Math.PI/180;
    const north=6371000*(nav.start_ll[0]-origin[0])*Math.PI/180,travel=nav.speed_kn*1852/3600*group.t;
    const pos=offset=>[east+(travel+offset[0])*fx-offset[1]*fy,north+(travel+offset[0])*fy+offset[1]*fx];
    const h=pos(nav.hull_m),t=pos(nav.tow_m);let dx=t[0]-h[0],dy=t[1]-h[1];const span=Math.hypot(dx,dy);
    if(span<1)return null;dx/=span;dy/=span;
    const q=selected.point,qx=q.x-h[0],qy=q.y-h[1],along=qx*dx+qy*dy;
    const x=h[0]+2*along*dx-qx,y=h[1]+2*along*dy-qy;
    // Never fabricate reflected XY. It must already be a valid geometry branch.
    // Non-collinear arrays need not have a reflection that fits folded bearings.
    return group.candidates.find(c=>Math.hypot(c.point.x-x,c.point.y-y)<=Math.max(1e-5,Math.hypot(x,y)*1e-9)
      &&Math.hypot(c.point.x-q.x,c.point.y-q.y)>1e-5)||null;
  }
  function targetMirrorPreview(display,doc,options){
    const targets=new Map(),byUnit=new Map(display.groups.map(g=>[g.unit_id,g]));
    for(const g of display.groups){
      const ids=new Set(g.model_assignment.map(m=>m.target_id));
      if(!g.validated||ids.size!==1||!g.model_assignment[0]?.target_id)continue;
      const id=g.model_assignment[0].target_id;
      if(!targets.has(id))targets.set(id,{target_id:id,groups:[],available:0,selected:0,mode:'model',requested_mode:'model',reason:''});
      const target=targets.get(id);target.groups.push(g.unit_id);g.target_id=id;
      if(!g.selected)continue;
      target.selected++;g.model_selected=g.selected;g.model_selection_basis=g.selection_basis;
      g.mirror_candidate=reflectedCandidate(g,doc,options.platformConfig);
      if(g.mirror_candidate)target.available++;
    }
    const mirrorTracks=[];
    for(const target of targets.values()){
      const choice=options.targetMirrorChoices?.[target.target_id];
      target.requested_mode=['mirror','both'].includes(choice)?choice:'model';
      target.can_switch=target.selected>0&&target.selected===target.available;
      if(target.requested_mode!=='model'&&target.can_switch)target.mode=target.requested_mode;
      if(!target.can_switch)target.reason=target.selected?'部分已显示点无可验证的整轨镜像，保留原分支，不补位置。':'尚无已选位置；不能通过镜像补定位。';
      for(const uid of target.groups){
        const g=byUnit.get(uid);g.display_mirror_mode=target.mode;
        if(!g.selected||target.mode==='model')continue;
        g.display_preview_notice='人工镜像预览，不改模型归属和原始分支；不是独立侧向证据或定位确认。';
        if(target.mode==='mirror'){
          g.selected=g.mirror_candidate;
          g.selection_basis={...g.model_selection_basis,type:'manual_target_mirror_preview',
            model_branch:g.model_selected.handedness,display_branch:g.selected.handedness,evidence_resolves_mirror:false,note:g.display_preview_notice};
        }
      }
    }
    const reflectedPoint=(p,g)=>({...g.mirror_candidate.point,connect_previous:p.connect_previous,
      selected_handedness:g.mirror_candidate.handedness,selection_basis:{type:'manual_target_mirror_preview',
        evidence_resolves_mirror:false,model_branch:g.model_selected.handedness,display_branch:g.mirror_candidate.handedness,
        note:g.display_preview_notice},line_basis:p.line_basis,provisional:true,suspect:p.suspect,
      model_selected_point:{x:g.model_selected.point.x,y:g.model_selected.point.y,handedness:g.model_selected.handedness},manual_display_override:true});
    display.tracks=display.tracks.map(track=>{
      let ghost=false;
      const points=track.points.map(p=>{
        const g=byUnit.get(p.unit_id);if(g?.display_mirror_mode==='both')ghost=true;
        return g?.display_mirror_mode==='mirror'&&g.mirror_candidate?reflectedPoint(p,g):p;
      });
      if(ghost){
        // A ghost never bridges missing/unselected groups, even if source edges exist.
        const extras=track.points.map(p=>{const g=byUnit.get(p.unit_id);return g?.display_mirror_mode==='both'&&g.mirror_candidate?reflectedPoint(p,g):null;});
        mirrorTracks.push({...track,id:track.id+':mirror-preview',mirror_preview:true,points:extras.map((p,i)=>p?{...p,connect_previous:p.connect_previous&&i>0&&!!extras[i-1]}:null).filter(Boolean)});
      }
      return {...track,points};
    });
    display.mirror_targets=[...targets.values()].map(({groups,...target})=>({...target,group_count:groups.length}));
    display.mirror_tracks=mirrorTracks;
    return display;
  }
  function spatialDisplayGroups(doc,units,points=[],options={}){
    const lookup=new Map(points.map(p=>[p.candidate_id,p])),byUnit=new Map(),preference=options.mirrorSide==='left'?-1:1;
    for(const tr of doc?.tracks||[])for(const p of tr.points){
      if(!Number.isFinite(p.x)||!Number.isFinite(p.y))continue;
      if(!byUnit.has(p.unit_id))byUnit.set(p.unit_id,[]);
      byUnit.get(p.unit_id).push({track_id:tr.id,handedness:tr.handedness,platform_id:tr.platform_id,pair_fragments:tr.pair_fragments,point:p});
    }
    const failures=new Map((doc?.unavailable||[]).map(u=>[u.unit_id,u.reason])),edges=new Map((options.edges||[]).map(e=>[JSON.stringify([e.from_id,e.to_id]),e]));
    const groups=units.map((u,index)=>({unit_id:u.unit_id,t:byUnit.get(u.unit_id)?.[0]?.point.t??lookup.get(u.candidate_ids[0])?.t,
      candidate_ids:u.candidate_ids,candidates:byUnit.get(u.unit_id)||[],validated:u.validated!==false,index}));
    const auto=options.autoConnect&&!options.modelOnly?provisionalGroupLinks(groups,lookup,options):{links:[],alternatives:[],notice:''},connections=new Map(),occupied=new Set();
    if(options.autoConnect){
      const byRaw=new Map(groups.flatMap(g=>g.candidate_ids.map(id=>[id,g])));
      // Agent-checked links always win over the separate display-only proposals.
      for(const edge of options.edges||[]){const a=byRaw.get(edge.from_id),b=byRaw.get(edge.to_id);if(!a||!b||!a.candidates.length||!b.candidates.length||!a.validated||!b.validated||b.t<=a.t)continue;const ap=lookup.get(a.candidate_ids[0])?.platform_id,bp=lookup.get(b.candidate_ids[0])?.platform_id;if(ap&&bp&&ap!==bp)continue;
        const both=b.candidate_ids.length===2&&b.candidate_ids.every(id=>{const mate=a.candidate_ids.find(old=>lookup.get(old)?.array===lookup.get(id)?.array);return mate&&edges.has(JSON.stringify([mate,id]));});
        if(both&&!connections.has(b.unit_id)){connections.set(b.unit_id,{from:a.unit_id,to:b.unit_id,basis:'agent_checked'});occupied.add(a.unit_id);}
      }
      for(const edge of auto.links)if(!connections.has(edge.to)&&!occupied.has(edge.from)){connections.set(edge.to,edge);occupied.add(edge.from);}
    }
    const rootKey=g=>{let id=g.unit_id;const seen=new Set();while(connections.has(id)&&!seen.has(id)){seen.add(id);id=connections.get(id).from;}return JSON.stringify(['work-chain',id]);};
    const history=new Map(),streams=new Map();
    const pairKey=c=>JSON.stringify([c.platform_id,c.pair_fragments||['fixture',...((byUnit.get(c.point.unit_id)||[]).map(x=>x.track_id).sort())]]);
    const rank=c=>(c.handedness?.[0]===preference?0:2)+(c.handedness?.[1]===preference?0:1);
    const link=(a,b)=>{
      if(!a||!b.validated||!a.validated||b.t<=a.t)return false;
      if(options.autoConnect)return connections.get(b.unit_id)?.from===a.unit_id;
      const links=(b.selected.point.candidate_ids||[]).map((id,i)=>edges.get(JSON.stringify([a.selected.point.candidate_ids?.[i],id])));
      if(options.edges)return links.length===2&&links.every(Boolean);
      return b.selected.track_id===a.selected.track_id&&!!b.selected.point.connect_previous;
    };
    const branchPlan=new Map();
    if(options.autoConnect&&doc?.branch_selection_source!=='model'){
      const chains=new Map();for(const g of groups.filter(g=>g.candidates.length)){const key=rootKey(g);if(!chains.has(key))chains.set(key,[]);chains.get(key).push(g);}
      for(const chain of chains.values()){
        chain.sort((a,b)=>a.t-b.t||a.index-b.index);if(chain.length<2)continue;
        let paths=[{cost:0,choices:[]}];
        for(const g of chain){const next=[];for(const path of paths)for(const candidate of g.candidates){
          const last=path.choices.at(-1),prior=path.choices.at(-2);let cost=rank(candidate)*(last ? .001 : .03);
          if(last){const dt=g.t-last.g.t,scale=Math.max(100,20*dt),p=candidate.point,q=last.candidate.point;
            cost+=.25*Math.pow(Math.hypot(p.x-q.x,p.y-q.y)/scale,2);
            if(prior&&last.g.t>prior.g.t){const k=dt/(last.g.t-prior.g.t),r=prior.candidate.point;cost+=.75*Math.pow(Math.hypot(p.x-q.x-(q.x-r.x)*k,p.y-q.y-(q.y-r.y)*k)/scale,2);}
          }
          next.push({cost:path.cost+cost,choices:[...path.choices,{g,candidate}]});
        }next.sort((a,b)=>a.cost-b.cost);paths=next.slice(0,16);}
        for(const choice of paths[0]?.choices||[])branchPlan.set(choice.g.unit_id,choice.candidate);
      }
    }
    // Only already-visible groups enter this projection. No source labels, truth,
    // or unexecuted queue measurements are available to the selector.
    for(const g of [...groups].sort((a,b)=>(a.t??Infinity)-(b.t??Infinity)||a.index-b.index)){
      const distinct=[];for(const c of g.candidates)if(!distinct.some(d=>Math.hypot(d.point.x-c.point.x,d.point.y-c.point.y)<1e-6))distinct.push(c);
      g.distinct_positions=distinct.length;g.reason=failures.get(g.unit_id)||(!doc?'后端尚未提供空间数据':'');g.selected=null;
      if(!distinct.length){g.status='unavailable';continue;}
      const unique=distinct.length===1&&g.candidates.every(c=>c.point.stable)&&g.validated;
      if(options.allowProvisional===false&&!unique){g.status=!g.validated?'pending_validation':distinct.length>1?'mirror_ambiguous':'unstable';continue;}
      const scored=g.candidates.map(c=>{
        const h=history.get(options.autoConnect?rootKey(g):pairKey(c))||[],last=h.at(-1),probe={...g,selected:c};let residual=null;
        if(last&&link(last,probe)){
          let x=last.selected.point.x,y=last.selected.point.y;
          const prior=h.at(-2),dt=g.t-last.t;
          if(prior&&last.t>prior.t){x+=(x-prior.selected.point.x)*dt/(last.t-prior.t);y+=(y-prior.selected.point.y)*dt/(last.t-prior.t);}
          residual=Math.hypot(c.point.x-x,c.point.y-y);
        }
        return {c,residual};
      });
      const supported=scored.filter(s=>s.residual!==null).sort((a,b)=>a.residual-b.residual);
      const evidencePreference=supported.length===1||supported.length>1&&supported[1].residual-supported[0].residual>Math.max(1e-6,supported[0].residual*1e-6);
      const modelOwned=doc?.branch_selection_source==='model',branchCode=c=>(c.handedness||[]).map(v=>v===1?'p':'m').join('');
      const chosen=modelOwned?scored.find(s=>branchCode(s.c)===doc.model_branch_choices?.[g.unit_id]):branchPlan.has(g.unit_id)?scored.find(s=>s.c===branchPlan.get(g.unit_id)):evidencePreference?supported[0]:scored.sort((a,b)=>rank(a.c)-rank(b.c)||a.c.track_id.localeCompare(b.c.track_id))[0];
      if(!chosen){g.status='pending_validation';g.reason='模型尚未选择本组位置分支；前端没有代选镜像。';continue;}
      g.selected=chosen.c;g.status=!g.validated?'unvalidated_provisional':!chosen.c.point.stable?'unstable_provisional':unique?'position_ready':'provisional';
      g.selection_basis={type:modelOwned?'model_selected_branch':unique?'unique_geometry':branchPlan.has(g.unit_id)?'local_sequence_hypothesis':evidencePreference?'continuity_from_provisional_history':'explicit_mirror_convention',
        mirror_preference:preference===1?'hull_right':'hull_left',residual_m:chosen.residual,
        evidence_resolves_mirror:false,note:unique?'唯一几何交点仍依赖输入配对假设':'当前采用的工作位置假设，可切换/撤回；不是证明镜像已解除或目标身份已确认'};
      const key=options.autoConnect?rootKey(g):pairKey(chosen.c),h=history.get(key)||[],last=h.at(-1);
      const relatedEdges=last?(chosen.c.point.candidate_ids||[]).map((id,i)=>edges.get(JSON.stringify([last.selected.point.candidate_ids?.[i],id]))):[];
      const connect=link(last,g);
      const point={...chosen.c.point,connect_previous:connect,selected_handedness:chosen.c.handedness,selection_basis:g.selection_basis,
        line_basis:connect?(connections.get(g.unit_id)?.basis||'agent_checked'):null,
        provisional:g.status!=='position_ready',suspect:!!(connections.get(g.unit_id)?.basis==='local_provisional'||chosen.c.point.suspect||relatedEdges.some(e=>e?.status==='suspect')||!chosen.c.point.stable||last&&!last.selected.point.stable)};
      if(!streams.has(key))streams.set(key,{id:'W'+String(streams.size+1).padStart(3,'0'),platform_id:chosen.c.platform_id,pair_fragments:chosen.c.pair_fragments,anchor_unit_id:g.unit_id,hypothesis_key:key,points:[]});
      streams.get(key).points.push(point);h.push(g);history.set(key,h);
    }
    const renderedLinks=[];
    for(const tr of streams.values())for(let i=1;i<tr.points.length;i++)if(tr.points[i].connect_previous){const from=tr.points[i-1].unit_id,to=tr.points[i].unit_id;renderedLinks.push(connections.get(to)||{from,to,basis:tr.points[i].line_basis||'agent_checked'});}
    for(const g of groups){
      g.model_assignment=g.candidate_ids.map(id=>{const m=options.membership?.[id]||{};return {candidate_id:id,array:lookup.get(id)?.array,status:m.status||'unknown',fragment_id:m.fragment_id||null,target_id:m.target_id||null};});
      g.incident_links=renderedLinks.filter(e=>e.from===g.unit_id||e.to===g.unit_id);
      g.isolated=g.selected!==null&&g.incident_links.length===0;
      g.connection_note=!g.selected?'缺少可用交点，未放置空间点或接线。':g.isolated?`孤立起始假设：${g.model_assignment.every(m=>m.status==='unassigned')?'Agent将本组保留为未归属；':''}当前暂接方案未选择接续边。长缺口、运动残差和其他竞争划分均可能阻止接入，不代表点已删除。`:'连线仅表示当前工作接续假设，不等于目标身份确认。';
    }
    const plotted=groups.filter(g=>g.selected).length,unique=groups.filter(g=>g.status==='position_ready').length;
    return targetMirrorPreview({groups,tracks:[...streams.values()],plotted,unique,resolved:unique,provisional:plotted-unique,pending:groups.length-plotted,
      display_links:renderedLinks,alternatives:auto.alternatives,auto_notice:auto.notice},doc,options);
  }
  root.PointSampling={sampleOrdered,groupByPlatformTime,sampleSynchronousGroups,sourcePairGroups,sampleSourcePairs,groupMarkers,groupDetail,inspectQueue,spatialDisplayGroups};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.PointSampling;
})(globalThis);
