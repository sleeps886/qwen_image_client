'use strict';
// Diagnostics never reject, delete, pair targets, or decide observability.
((root)=>{
  const sampling=typeof module!=='undefined'&&module.exports?require('./point-sampling.js'):root.PointSampling;
  function summarize(items){
    const groups=sampling.groupByPlatformTime(items),dual=groups.filter(g=>g.arrays.has('hull')&&g.arrays.has('tow'));
    return {point_count:items.length,group_count:groups.length,strict_dual_group_count:dual.length,
      single_array_group_count:groups.length-dual.length,
      hull_point_count:items.filter(p=>p.array==='hull').length,tow_point_count:items.filter(p=>p.array==='tow').length,
      strict_dual_point_count:dual.reduce((n,g)=>n+g.candidates.length,0),
      policy:'accept-sparse-v1',synchronization_rule:'same-platform-exact-time',target_pairing_confirmed:false};
  }
  root.ObservationDiagnostics={summarize};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.ObservationDiagnostics;
})(globalThis);
