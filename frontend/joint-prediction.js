'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let dataset=null,state=null,preview=null,busy=false;
  const colors=['#65c7ad','#c7a0ff'];
  async function request(url,body){
    const response=await fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data));return data;
  }
  function controls(){
    for(const id of ['jointLoad','jointCreate','jointRestore','jointPrepare','jointAdopt','jointReject','jointTarget0','jointTarget1','jointSeeds0','jointSeeds1','jointSource','jointHorizon','jointQuestion'])$(id).disabled=busy;
    $('jointCreate').disabled=busy||!dataset;
    $('jointPrepare').disabled=busy||state?.status!=='ready';
    $('jointSend').disabled=busy||!preview||state?.status!=='ready'||!$('jointConsent').checked;
  }
  async function run(action){if(busy)return;busy=true;controls();try{await action();}catch(error){$('jointNotice').textContent=error.message;}finally{busy=false;controls();}}
  function initialSeeds(){
    if(!dataset)return;
    const groups=[0,1].map(i=>{
      const target=dataset.target_catalog.find(t=>t.target_id===$('jointTarget'+i).value),ids=new Set(target?.candidate_ids||[]),map=new Map();
      for(const p of dataset.candidates.filter(p=>ids.has(p.candidate_id)&&p.array==='hull')){
        const key=JSON.stringify([p.platform_id,p.t]);if(!map.has(key))map.set(key,[]);map.get(key).push(p);
      }return map;
    });
    const keys=[...groups[0].keys()].filter(key=>groups[0].get(key).length===1&&groups[1].get(key)?.length===1).sort((a,b)=>JSON.parse(a)[1]-JSON.parse(b)[1]).slice(0,3);
    for(let i=0;i<2;i++)$('jointSeeds'+i).value=keys.map(key=>groups[i].get(key)[0].candidate_id).join(',');
  }
  async function loadBatch(){
    dataset=await request('/api/incremental/batches/'+encodeURIComponent($('jointSource').value.trim()));
    $('jointBatchInfo').textContent=`已载入 ${dataset.candidates.length} 个原始候选；仅起始种子使用目录，后续共同竞争原始候选。`;
    for(let i=0;i<2;i++){
      const select=$('jointTarget'+i);select.replaceChildren();
      for(const target of dataset.target_catalog||[]){const option=document.createElement('option');option.value=target.target_id;option.textContent=target.target_id;select.appendChild(option);}
      select.selectedIndex=Math.min(i,select.options.length-1);
    }
    initialSeeds();
  }
  function table(points){
    const table=document.createElement('table'),head=table.createTHead().insertRow();
    for(const name of ['编号','时间（秒）','方位（度）','平台/阵列']){const th=document.createElement('th');th.textContent=name;head.appendChild(th);}
    for(const p of points){const row=table.insertRow();for(const value of [p.candidate_id,p.t,p.bearing_deg.toFixed(3),p.platform_id+'/'+p.array])row.insertCell().textContent=value;}
    return table;
  }
  async function prepare(){
    preview=null;
    if(state.status!=='ready'){controls();return;}
    preview=await request(`/api/incremental/joint-sessions/${state.session_id}/preview`);
    const context=Object.values(preview.context.tracks)[0];$('jointCandidates').replaceChildren(table(context.new_candidates));
    $('jointNotice').textContent=`已准备至 ${context.visible_until} 秒，共享 ${context.new_candidates.length} 个主阵候选；尚未调用模型。`;
  }
  function draw(){
    $('jointSession').value=state.session_id;localStorage.setItem('joint-prediction-session',state.session_id);
    $('jointStatus').textContent=`状态：${state.status} · 版本 ${state.version} · 联合调用 ${state.calls}/${state.max_calls}`;
    $('jointReview').hidden=state.status!=='awaiting_review';$('jointMessages').replaceChildren();
    for(const round of state.rounds){
      const block=document.createElement('section');
      const title=document.createElement('h3');title.textContent=`第 ${round.input.step_id} 轮 · ${round.review_status}`;block.appendChild(title);
      for(const decision of round.model_answer.decisions){const p=document.createElement('p');p.textContent=`${decision.track_id}：${decision.reason}\n接入：${decision.accepted_candidate_ids.join('、')||'无'} · ${decision.decision}`;block.appendChild(p);}
      $('jointMessages').appendChild(block);
    }
    const tracks=Object.entries(state.tracks),motion=tracks[0][1].platform_motion;
    $('jointPlatform').textContent=motion?`平台 ${motion.platform_id} · 初始纬度 ${motion.initial_latitude}° / 经度 ${motion.initial_longitude}° · 航向 ${motion.heading_deg}° · 航速 ${motion.speed_kn} 节（配置推算，非实时 GPS）`:'未提供平台配置';
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',motion?'0 0 840 400':'0 0 840 360');svg.classList.add('joint-chart');svg.setAttribute('aria-label','两个目标的联合方位预测图');
    const element=(tag,attrs)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);svg.appendChild(el);return el;};
    const points=tracks.flatMap(([,track])=>track.history.concat(track.prediction)),min=Math.min(...points.map(p=>p.t)),max=Math.max(min+1,...points.map(p=>p.t));
    const x=t=>55+(t-min)/(max-min)*750,y=b=>300-b/180*250;
    for(let b=0;b<=180;b+=30){element('line',{x1:55,x2:805,y1:y(b),y2:y(b),stroke:'#444'});element('text',{x:5,y:y(b)+4}).textContent=b+'°';}
    for(let i=0;i<=4;i++){const t=min+(max-min)*i/4;element('text',{x:x(t)-12,y:330}).textContent=t.toFixed(1)+'s';}
    tracks.forEach(([tid,track],i)=>{
      element('text',{x:60+i*350,y:22,fill:colors[i]}).textContent=tid+' · '+track.history[0].platform_id+'/'+track.history[0].array;
      const drawLine=(ps,dash)=>{if(!ps.length)return;const attrs={points:ps.map(p=>`${x(p.t)},${y(p.bearing_deg)}`).join(' '),fill:'none',stroke:colors[i],'stroke-width':2};if(dash)attrs['stroke-dasharray']='6 4';element('polyline',attrs);};
      drawLine(track.history,state.status==='awaiting_review');
      for(const p of track.history)element('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:3,fill:colors[i]});
      drawLine([track.history.at(-1),...track.prediction],true);
    });
    if(motion){
      element('circle',{cx:60,cy:370,r:5,fill:'#eee'});
      element('text',{x:75,y:374}).textContent=`平台 ${motion.platform_id}：${motion.initial_latitude.toFixed(6)}° / ${motion.initial_longitude.toFixed(6)}°（初始位置，不占用方位轴）`;
    }
    const note=document.createElement('p');note.className='note';note.textContent='实线：种子/已采用的观测接续；虚线：待确认接续或未来预测。方位—时间曲线不代表空间路线，目标身份仍为假设。';
    $('jointChart').replaceChildren(svg,note);
  }
  $('jointLoad').onclick=()=>run(loadBatch);
  for(let i=0;i<2;i++)$('jointTarget'+i).onchange=initialSeeds;
  $('jointCreate').onclick=()=>run(async()=>{
    if($('jointTarget0').value===$('jointTarget1').value)throw Error('请选择两个不同目标假设');
    state=await request('/api/incremental/joint-sessions',{dataset,horizon:Number($('jointHorizon').value),tracks:[0,1].map(i=>({track_id:$('jointTarget'+i).value,seed_ids:$('jointSeeds'+i).value.split(/[,，\s]+/).filter(Boolean)}))});
    $('jointConsent').checked=false;draw();await prepare();
  });
  async function restore(){state=await request('/api/incremental/joint-sessions/'+encodeURIComponent($('jointSession').value.trim()));preview=null;$('jointConsent').checked=false;draw();if(state.status==='ready')await prepare();}
  $('jointRestore').onclick=()=>run(restore);$('jointPrepare').onclick=()=>run(prepare);$('jointConsent').onchange=controls;
  $('jointSend').onclick=()=>run(async()=>{
    const current=preview;preview=null;$('jointNotice').textContent='正在进行一次联合模型调用，等待完整回复并校验；不会自动重试。';
    try{state=await request(`/api/incremental/joint-sessions/${state.session_id}/step`,{version:state.version,context_digest:current.context_digest,consent:$('jointConsent').checked,question:$('jointQuestion').value});draw();$('jointNotice').textContent='两个目标均通过校验，等待整轮采用或打回。';}
    catch(error){await restore();throw error;}
    finally{$('jointConsent').checked=false;}
  });
  for(const [id,adopt] of [['jointAdopt',true],['jointReject',false]])$(id).onclick=()=>run(async()=>{
    state=await request(`/api/incremental/joint-sessions/${state.session_id}/review`,{version:state.version,adopt,reason:$('jointReviewReason').value});preview=null;$('jointConsent').checked=false;draw();if(state.status==='ready')await prepare();
  });
  $('jointSource').value=new URLSearchParams(location.search).get('source_result')||'';
  $('jointSession').value=localStorage.getItem('joint-prediction-session')||'';
  controls();if($('jointSource').value)run(loadBatch);else if($('jointSession').value)run(restore);
})();
