'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let dataset=null,state=null,busy=false;
  const active=new Set(['ready','tracking','coasting','ambiguous']);
  const status=text=>{$('predictionStatus').textContent=text;};
  function controls(){
    for(const id of ['create','reload','example','datasetFile','sessionId']) $(id).disabled=busy;
    $('step').disabled=busy||!state||!active.has(state.status)||state.calls>=state.max_calls;
    $('export').disabled=busy||!state;
    $('step').textContent=state?.initialized?'加入下一窗口并预测（一次模型调用）':'执行初始化预测（一次模型调用）';
  }
  async function request(url,body){
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail||result));
    return result;
  }
  function inventory(){
    const points=dataset?.candidates||dataset?.images?.flatMap(im=>im.candidates.map(p=>({...p,array:im.array,platform_id:im.platform_id})))||[];
    $('inventory').textContent=points.map(p=>`${p.candidate_id||p.id} | ${p.platform_id}/${p.array} | t=${p.t} | ${p.bearing_deg}°`).join('\n');
    status(`已导入${points.length}个候选；请选择同一阵列、不同时间的3～5个种子。`);
  }
  $('datasetFile').addEventListener('change',async event=>{
    try{
      const file=event.target.files[0];if(!file)return;
      if(file.size>5*1024*1024)throw new Error('候选文件最多5MiB');
      dataset=JSON.parse(await file.text());inventory();
    }catch(error){dataset=null;status(error.message);}
  });
  $('example').addEventListener('click',()=>{
    dataset={candidates:Array.from({length:16},(_,i)=>({candidate_id:`P${i}`,t:i*10,bearing_deg:30+i*1.5,array:'hull',platform_id:'A'}))};
    $('seeds').value='P0,P2,P4';inventory();
  });
  function node(tag,attrs={},text){
    const element=document.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [key,value] of Object.entries(attrs))element.setAttribute(key,String(value));
    if(text!==undefined)element.textContent=text;
    $('predictionChart').appendChild(element);return element;
  }
  function chart(){
    $('predictionChart').replaceChildren();
    const candidates=state.visible_candidates;
    const start=Math.min(...candidates.map(p=>p.t));
    const end=Math.max(start+1,state.visible_until+state.horizon);
    const x=t=>65+(t-start)/(end-start)*900,y=b=>365-b/180*320;
    node('rect',{x:0,y:0,width:1000,height:420,fill:'#fff'});
    for(let b=0;b<=180;b+=30){node('line',{x1:65,y1:y(b),x2:965,y2:y(b),stroke:'#ddd'});node('text',{x:12,y:y(b)+5,fill:'#333'},`${b}°`);}
    for(let i=0;i<=5;i++){const t=start+(end-start)*i/5;node('text',{x:x(t)-12,y:395,fill:'#333'},`${t.toFixed(1)}s`);}
    node('text',{x:65,y:22,fill:'#333'},`相对船首折叠方位 · ${state.history[0].platform_id}/${state.history[0].array}`);
    const line=(points,color,dashed=false)=>{if(points.length<2)return;node('polyline',{points:points.map(p=>`${x(p.t)},${y(p.bearing_deg)}`).join(' '),fill:'none',stroke:color,'stroke-width':2,...(dashed?{'stroke-dasharray':'7 5'}:{})});};
    for(const p of candidates)node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:3,fill:'#aaa'});
    const initial=state.history.filter(p=>!state.rounds.some(r=>r.model_answer.accepted_candidate_ids.includes(p.candidate_id)));
    line(initial,'#087f75');let tail=initial.at(-1);
    for(const round of state.rounds){
      const ids=new Set(round.model_answer.accepted_candidate_ids);
      const accepted=state.history.filter(p=>ids.has(p.candidate_id));
      if(accepted.length){line(tail?[tail,...accepted]:accepted,'#087f75');tail=accepted.at(-1);}
      else if(round.input.new_candidates.length)tail=null;
    }
    for(const p of state.history)node('circle',{cx:x(p.t),cy:y(p.bearing_deg),r:4,fill:'#087f75'});
    const lastRound=state.rounds.at(-1),last=state.history.at(-1);
    // Do not draw an observed-to-future bridge across a missing observation window.
    const anchor=last.t===state.visible_until?[last]:[];
    if(lastRound)line([...anchor,...lastRound.input.baseline_prediction],'#c47700',true);
    line([...anchor,...state.prediction],'#2166d1',true);
    node('line',{x1:x(state.visible_until),y1:35,x2:x(state.visible_until),y2:365,stroke:'#777','stroke-dasharray':'3 4'});
  }
  function render(result){
    state=result;$('sessionId').value=state.session_id;
    localStorage.setItem('incremental-session',state.session_id);
    $('predictionSummary').textContent=`状态：${state.status} | 已提交版本：${state.version} | 调用：${state.calls}/${state.max_calls}\n已开放至：${state.visible_until}s | 已接入点：${state.history.length}\n预测仅为方位—时间假设，不是空间轨迹。`;
    $('roundLog').textContent=JSON.stringify(state.rounds,null,2);chart();controls();
  }
  async function run(action){
    if(busy)return;busy=true;controls();
    try{await action();}catch(error){status(error.message);}finally{busy=false;controls();}
  }
  $('create').addEventListener('click',()=>run(async()=>{
    if(!dataset)throw new Error('请先导入候选数据');
    render(await request('/api/incremental/sessions',{dataset,seed_ids:$('seeds').value.split(/[,，\s]+/).filter(Boolean),stride:Number($('stride').value),horizon:Number($('horizon').value),max_calls:Number($('maxCalls').value)}));
    status('本地任务已创建，尚未调用模型。请确认费用后执行初始化预测。');
  }));
  $('reload').addEventListener('click',()=>run(async()=>{
    render(await request(`/api/incremental/sessions/${encodeURIComponent($('sessionId').value.trim())}`));
    status('已加载已提交状态，未调用模型；pending/未知结果不会自动重试。');
  }));
  $('step').addEventListener('click',()=>run(async()=>{
    if(!$('predictionConsent').checked)throw new Error('请先勾选点位发送及费用确认');
    status('本轮处理中，请勿重复提交；页面断开不保证模型停止或不计费。');
    try{
      render(await request(`/api/incremental/sessions/${state.session_id}/step`,{version:state.version,consent:true}));
      status('本轮已校验并保存。可检查预测后继续下一轮，或停止点击以暂停。');
    }catch(error){
      // A lost HTTP response must never enable a blind replay of a paid request.
      try{render(await request(`/api/incremental/sessions/${state.session_id}`));}catch{state=null;}
      throw error;
    }
  }));
  $('export').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`${state.session_id}-visible.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('sessionId').value=localStorage.getItem('incremental-session')||'';
  controls();
})();
