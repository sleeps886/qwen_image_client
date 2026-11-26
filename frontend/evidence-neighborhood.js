'use strict';
// Selection suggestions only. Real observations are neither altered nor assigned.
((root)=>{
  function propose(points,seeds,radius=2){
    if(!Number.isSafeInteger(radius)||radius<1)throw Error('邻域时刻数量无效');
    const byPlatform=new Map();
    for(const point of points){if(!byPlatform.has(point.platform_id))byPlatform.set(point.platform_id,new Map());const times=byPlatform.get(point.platform_id);if(!times.has(point.t))times.set(point.t,[]);times.get(point.t).push(point);}
    const result=new Set();
    for(const seed of seeds){const slots=byPlatform.get(seed.platform_id);if(!slots)continue;const times=[...slots.keys()].sort((a,b)=>a-b),i=times.indexOf(seed.t);if(i<0)continue;for(const t of times.slice(Math.max(0,i-radius),i+radius+1))for(const point of slots.get(t))result.add(point.candidate_id);}
    return [...result];
  }
  root.EvidenceNeighborhood={propose};if(typeof module!=='undefined'&&module.exports)module.exports=root.EvidenceNeighborhood;
})(globalThis);
