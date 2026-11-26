"""Bounded RANSAC bearing/time proposals, not identity assignments or XY truth."""
import random
import statistics
import numpy as np


def _window_tracklets(points,aliases,*,max_candidates=6):
    output=[]
    for key in sorted({(p['platform_id'],p['array']) for p in points}):
        rows=sorted((p for p in points if (p['platform_id'],p['array'])==key),key=lambda p:(p['t'],p['bearing_deg']))
        times=sorted({p['t'] for p in rows})
        if len(times)<4:continue
        start,end=times[0],times[-1];span=end-start
        x=np.array([(p['t']-start)/span for p in rows]);y=np.array([p['bearing_deg'] for p in rows])
        tolerances=np.array([max(.6,min(3.,3*p.get('bearing_error_assumption_deg',.5))) for p in rows])
        strength=[p.get('strength',0.) for p in rows];median=max(1.,statistics.median(strength))
        # Strength is a weak tie-breaker, never a target-presence decision.
        weights=np.array([1.+min(.2,max(0.,s)/median*.05) for s in strength])
        rng=random.Random(19);proposals={}
        for degree in (1,2):
            for _ in range(180):
                sample=rng.sample(range(len(rows)),degree+1)
                if len({rows[i]['t'] for i in sample})<=degree:continue
                coefficients=np.polyfit(x[sample],y[sample],degree)
                prediction=np.polyval(coefficients,x)
                prediction=np.abs((prediction+180.)%360.-180.)
                residual=abs(prediction-y);eligible=np.flatnonzero(residual<=tolerances)
                # A tentative path can occupy only one candidate per time.
                chosen={}
                for i in eligible:
                    t=rows[i]['t']
                    if t not in chosen or residual[i]<residual[chosen[t]]:chosen[t]=int(i)
                indices=tuple(sorted(chosen.values()))
                if len(indices)<4:continue
                covered_span=rows[indices[-1]]['t']-rows[indices[0]]['t']
                if covered_span<span*.12:continue
                score=sum(weights[list(indices)])-degree*.8
                previous=proposals.get(indices)
                if previous is None or score>previous[0]:proposals[indices]=(score,degree,float(np.sqrt(np.mean(residual[list(indices)]**2))))
        selected=[]
        for indices,(score,degree,rmse) in sorted(proposals.items(),key=lambda item:-item[1][0]):
            ids=set(indices)
            if any(len(ids&old)/min(len(ids),len(old))>.75 for old in selected):continue
            selected.append(ids)
            output.append({'reference_id':f'R{len(output)+1:03d}','platform_id':key[0],'array':key[1],
                           'observation_ids':[aliases[rows[i]['candidate_id']] for i in indices],
                           'model_family':'bearing_time_linear' if degree==1 else 'bearing_time_quadratic',
                           'rmse_deg':round(rmse,3),'reference_only':True,
                           'support_count':len(indices),'fit_parameters':degree+1,
                           'validation':'in_sample_only','evidence_level':'weak_four_point_quadratic' if degree==2 and len(indices)<=4 else 'candidate_only',
                           'identity_confirmed':False})
            if len(selected)>=max_candidates:break
    return output


def tracklet_references(points,aliases,*,rolling=False):
    output=[];seen=set()
    for key in sorted({(p['platform_id'],p['array']) for p in points}):
        rows=[p for p in points if (p['platform_id'],p['array'])==key]
        times=sorted({p['t'] for p in rows})
        starts=([0] if len(times)<=8 else sorted({*range(0,len(times)-7,4),len(times)-8})) if rolling else ([0] if len(times)<=8 else sorted({0,(len(times)-8)//2,len(times)-8}))
        for start in starts:
            window=set(times[start:start+8]);local=[p for p in rows if p['t'] in window]
            limit=6 if len(starts)==1 else 4
            for row in _window_tracklets(local,aliases)[:limit]:
                ids=tuple(row['observation_ids'])
                if ids in seen:continue
                seen.add(ids);output.append({**row,'reference_id':f'R{len(output)+1:03d}',
                                             'observed_time_window':[min(window),max(window)]})
    return output
