"""Bounded beam association in unfolded bearing space. No expected target count."""
import copy
from itertools import product

import numpy as np
from scipy.optimize import linear_sum_assignment


def nearest_unfolded(bearing,prediction):
    return min((sign*bearing+360*k for sign,k in product((1,-1),range(-2,3))),key=lambda value:abs(value-prediction))


def _assignment(active,points,time,forbidden=None):
    n,m=len(active),len(points);cost=np.full((n+m,m+n),40.,dtype=float)
    if n:cost[:n,m:]=7.
    if m:cost[n:,:m]=7.
    cost[n:,m:]=0.
    for i,track in enumerate(active):
        last=track[-1];dt=time-last['t'];slope=0.
        if len(track)>1:slope=(last['unfolded_deg']-track[-2]['unfolded_deg'])/(last['t']-track[-2]['t'])
        prediction=last['unfolded_deg']+np.clip(slope,-1.5,1.5)*dt
        for j,p in enumerate(points):
            value=nearest_unfolded(p['bearing_deg'],prediction);delta=abs(value-prediction)
            if delta<=max(3.,.6*dt):
                width=abs(np.log(max(.1,p['sigma_deg'])/max(.1,last['sigma_deg'])))
                cost[i,j]=delta/max(.5,.08*dt)+width*.4+(1. if p.get('mixed') else 0.)
    if forbidden is not None:cost[forbidden]=1000.
    ii,jj=linear_sum_assignment(cost)
    links=[(i,j) for i,j in zip(ii,jj) if i<n and j<m and cost[i,j]<14]
    total=float(sum(cost[i,j] for i,j in zip(ii,jj)))
    return links,total,cost


def ridge_hypotheses(evidence,control,beam_width=4):
    output=[];crossings=[]
    for image in evidence['images']:
        rows={}
        for p in image['candidates']:rows.setdefault(p['sample_index'],[]).append(p)
        beam=[{'tracks':[],'cost':0.}]
        for row,points in sorted(rows.items()):
            control.check();time=points[0]['t'];children=[]
            for state in beam:
                active=[t for t in state['tracks'] if 0<time-t[-1]['t']<=max(30,3*context_step(image))]
                links,total,cost=_assignment(active,points,time)
                choices=[(links,total)]
                if active and points:
                    for i,j in sorted(links,key=lambda ij:cost[ij],reverse=True)[:2]:
                        alternatives,score,_=_assignment(active,points,time,(i,j))
                        if score-total<4.:
                            choices.append((alternatives,score))
                            crossings.append({'image_index':image['image_index'],'sample_index':row,
                                              'candidate_id':points[j]['id'],'type':'competing_temporal_assignment'})
                for assignment,score in choices:
                    tracks=[list(t) for t in state['tracks']];mapping={id(t):index for index,t in enumerate(state['tracks'])};used=set()
                    for i,j in assignment:
                        old=active[i];last=old[-1];dt=time-last['t'];slope=0.
                        if len(old)>1:slope=(last['unfolded_deg']-old[-2]['unfolded_deg'])/(last['t']-old[-2]['t'])
                        unfolded=nearest_unfolded(points[j]['bearing_deg'],last['unfolded_deg']+slope*dt)
                        tracks[mapping[id(old)]].append({**points[j],'unfolded_deg':unfolded});used.add(j)
                    for j,p in enumerate(points):
                        if j not in used:tracks.append([{**p,'unfolded_deg':p['bearing_deg']}])
                    children.append({'tracks':tracks,'cost':state['cost']+score})
            beam=sorted(children,key=lambda s:s['cost'])[:beam_width]
        unique=[]
        for state in beam[:3]:
            for points in state['tracks']:
                if len(points)<5 or points[-1]['t']-points[0]['t']<20:continue
                ids={p['id'] for p in points}
                if any(len(ids&existing)/len(ids|existing)>.8 for existing in unique):continue
                unique.append(ids)
                output.append({'id':f"R{image['image_index']}_{len(unique)}",'image_index':image['image_index'],
                    'array':image['array'],'platform_id':image['platform_id'],'points':points,
                    'support':len(points),'start_t':points[0]['t'],'end_t':points[-1]['t']})
                if len(unique)>=20:break
            if len(unique)>=20:break
    distinct={(r['image_index'],r['sample_index'],r['candidate_id']):r for r in crossings}
    return {'tracks':output,'crossing_regions':list(distinct.values())[:100],
            'method':'bounded_unfolded_beam_v1','beam_width':beam_width,'count_confirmed':False}


def context_step(image):
    times=sorted({p['t'] for p in image['candidates']})
    return float(np.median(np.diff(times))) if len(times)>1 else 1.


def propose_pairs(ridges,control):
    hull=[t for t in ridges['tracks'] if t['array']=='hull'];tow=[t for t in ridges['tracks'] if t['array']=='tow']
    continuations=[]
    def combined(tracks):
        joined=[]
        for i,first in enumerate(tracks):
            for second in tracks[i+1:]:
                a,b=first,second
                if a['start_t']>b['start_t']:a,b=b,a
                if abs(a['end_t']-b['start_t'])>30:continue
                # Gap review handles long missing intervals, not adjacent or
                # slightly overlapping beam fragments. Do not disable those
                # continuations merely because the gap node has run.
                if ridges.get('gap_review_applied') and b['start_t']-a['end_t']>20:continue
                overlap={p['sample_index'] for p in a['points']}&{p['sample_index'] for p in b['points']}
                if len(overlap)>2:continue
                if abs(nearest_unfolded(b['points'][0]['bearing_deg'],a['points'][-1]['unfolded_deg'])-a['points'][-1]['unfolded_deg'])>8:continue
                rows={p['sample_index']:p for p in a['points']};valid=True
                for p in b['points']:
                    if p['sample_index'] in rows and abs(rows[p['sample_index']]['bearing_deg']-p['bearing_deg'])>2:valid=False;break
                    if p['sample_index'] not in rows or p['error_deg']<rows[p['sample_index']]['error_deg']:rows[p['sample_index']]=p
                if valid:
                    points=[dict(rows[r]) for r in sorted(rows)]
                    for k in range(1,len(points)):points[k]['unfolded_deg']=nearest_unfolded(points[k]['bearing_deg'],points[k-1]['unfolded_deg'])
                    joined.append({**a,'id':a['id']+'+'+b['id'],'points':points,'support':len(points),'end_t':points[-1]['t'],
                                   'adjacent_fragment_ids':[a['id'],b['id']]})
        continuations.extend({'id':t['id'],'array':t['array'],'fragment_ids':t['adjacent_fragment_ids'],
                              'observed_measurements':len(t['points']),'synthetic_measurements':0} for t in joined[:8])
        return tracks+joined[:8]
    hull=combined(hull);tow=combined(tow)
    proposals=[];rejected_sync=0
    for h in hull:
        for t in tow:
            control.check()
            if h['platform_id']!=t['platform_id']:continue
            hm={p['sample_index']:p for p in h['points']};tm={p['sample_index']:p for p in t['points']};common=sorted(hm.keys()&tm.keys())
            sparse=False
            if len(common)<6:
                # A weak broad-array ridge may contain only a few clean peaks
                # while the other array has a long continuous observation.
                # Permit a bounded asymmetric hypothesis, not single-array
                # localization or a global relaxation of the six-point gate.
                lengths=sorted((len(h['points']),len(t['points'])))
                span=hm[common[-1]]['t']-hm[common[0]]['t'] if common else 0.
                sparse=len(common)>=4 and lengths[1]>=20 and lengths[0]<=lengths[1]/3 and span>=30.
                if not sparse:rejected_sync+=1;continue
            differences=[abs(hm[r]['bearing_deg']-tm[r]['bearing_deg']) for r in common]
            # A bounded broad-parallax screen, not a target classifier. Near-array
            # cases outside it remain uncovered and are reported, never invented.
            if np.median(differences)>45:continue
            proposals.append({'id':f'A{len(proposals)+1:03d}','hull':h,'tow':t,'common_rows':common,
                              'sparse_dual_support':sparse,'rank':len(common)-float(np.median(differences))*.2})
    proposals.sort(key=lambda p:p['rank'],reverse=True)
    return {'pairs':proposals[:24],'total_proposed':len(proposals),'budget_excluded':max(0,len(proposals)-24),
            'adjacent_continuations':continuations,
            'rejected_sync_gate':rejected_sync,'retained_sparse_pairs':sum(p['sparse_dual_support'] for p in proposals[:24]),
            'sparse_policy':{'minimum_common_rows':4,'minimum_long_ridge':20,'maximum_short_long_ratio':1/3,'minimum_overlap_seconds':30},
            'max_median_parallax_deg':45.}
