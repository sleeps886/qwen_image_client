'use strict';
// Display-only links between confirmed observations. Never creates measurements or forecasts.
((root)=>{
  function build(history,excludedIds=[],maxConnectedGapS){
    const excluded=new Set(excludedIds),seen=new Set();
    const points=(history||[]).filter(p=>{
      if(!p||typeof p.candidate_id!=='string'||excluded.has(p.candidate_id)||seen.has(p.candidate_id)||!Number.isFinite(p.t)||!Number.isFinite(p.bearing_deg))return false;
      seen.add(p.candidate_id);return true;
    }).sort((a,b)=>a.t-b.t||a.candidate_id.localeCompare(b.candidate_id));
    const streams=new Map();for(const p of points){const key=JSON.stringify([p.platform_id,p.array]);if(!streams.has(key))streams.set(key,[]);streams.get(key).push(p);}
    const connections=[];
    for(const stream of streams.values()){
      const intervals=stream.slice(1).map((p,i)=>p.t-stream[i].t).filter(dt=>dt>0);
      const limit=Number.isFinite(maxConnectedGapS)&&maxConnectedGapS>0?maxConnectedGapS:intervals.length?3*Math.min(...intervals):0;
      for(let i=1;i<stream.length;i++){
        const from=stream[i-1],to=stream[i];if(to.t<=from.t)continue;
        connections.push({from,to,gap:limit>0&&to.t-from.t>limit,kind:'confirmed_association_link'});
      }
    }
    return {points,connections};
  }
  root.TrackConnections={build};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.TrackConnections;
})(globalThis);
