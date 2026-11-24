"""Bounded, observation-only gap hypotheses; never synthesize measurements."""
import copy
import numpy as np
from backend.app.services.association.ridge_hypotheses import nearest_unfolded

MAX_GAP_SECONDS=120.
MAX_JOINED=12
MAX_PAIRS=128


def scan_gaps(ridges,evidence,control):
    result=copy.deepcopy(ridges);tracks=[];split_count=0;short_fragments=0
    steps={}
    for image in evidence['images']:
        times=sorted({p['t'] for p in image['candidates']})
        steps[image['image_index']]=float(np.median(np.diff(times))) if len(times)>1 else 10.
    for track in result['tracks']:
        control.check();points=sorted(track['points'],key=lambda p:p['t']);parts=[[]]
        threshold=max(20.,steps.get(track['image_index'],10.)*2.2)
        for point in points:
            if parts[-1] and point['t']-parts[-1][-1]['t']>threshold:parts.append([])
            parts[-1].append(point)
        split_count+=len(parts)-1
        for i,part in enumerate(parts):
            if len(part)<3:short_fragments+=1;continue
            tracks.append({**track,'id':track['id']+(f'.s{i+1}' if len(parts)>1 else ''),
                           'points':part,'support':len(part),'start_t':part[0]['t'],'end_t':part[-1]['t']})
    result['tracks']=tracks
    pairs=[]
    for a in tracks:
        for b in tracks:
            control.check()
            if a['image_index']!=b['image_index'] or a['array']!=b['array'] or a['platform_id']!=b['platform_id']:continue
            gap=b['start_t']-a['end_t']
            if max(20.,2.2*steps.get(a['image_index'],10.))<gap<=MAX_GAP_SECONDS:
                pairs.append((a['id'],b['id']))
    by_id={t['id']:t for t in tracks}
    pairs.sort(key=lambda pair:(by_id[pair[1]]['start_t']-by_id[pair[0]]['end_t'],pair))
    report={'method':'bidirectional_observed_gap_v1','status':'needs_review' if pairs else 'no_candidate_gaps',
            'split_count':split_count,'short_fragments_not_used':short_fragments,
            'budget_excluded_pairs':max(0,len(pairs)-MAX_PAIRS),'candidate_pairs':[list(p) for p in pairs[:MAX_PAIRS]],
            'max_gap_seconds':MAX_GAP_SECONDS,'synthetic_measurements':0,
            'association_confirmed':False,'decisions':[]}
    return result,report


def _endpoint(points,tail):
    part=points[-5:] if tail else points[:5]
    times=np.array([p['t'] for p in part],float)
    values=[]
    for p in part:
        values.append(nearest_unfolded(p['bearing_deg'],values[-1] if values else p.get('unfolded_deg',p['bearing_deg'])))
    anchor=float(times[-1] if tail else times[0])
    # Median pairwise slopes resist an isolated speckle/centroid outlier.
    slopes=[(values[j]-values[i])/(times[j]-times[i]) for i in range(len(times))
            for j in range(i+1,len(times)) if times[j]>times[i]]
    slope=float(np.median(slopes));intercept=float(np.median(np.array(values)-slope*(times-anchor)))
    return anchor,float(intercept),float(slope)


def review_gaps(ridges,report,control):
    result=copy.deepcopy(ridges);audit=copy.deepcopy(report)
    by_id={t['id']:t for t in result['tracks']};eligible=[]
    for left,right in audit['candidate_pairs']:
        control.check();a,b=by_id[left],by_id[right]
        ta,va,sa=_endpoint(a['points'],True);tb,vb,sb=_endpoint(b['points'],False)
        _,vb,orientation=min((abs(sign*vb+360*k-(va+sa*(tb-ta))),sign*vb+360*k,sign)
                             for sign in (1,-1) for k in range(-2,3))
        sb*=orientation
        dt=tb-ta;forward=abs(va+sa*dt-vb);backward=abs(vb-sb*dt-va)
        limit=min(6.,2.+.03*dt)
        score=max(forward,backward)/limit+abs(sa-sb)/.08
        accepted=forward<=limit and backward<=limit and abs(sa-sb)<=.08
        decision={'left':left,'right':right,'image_index':a['image_index'],'array':a['array'],
                  'missing_interval':[float(ta),float(tb)],'gap_seconds':float(dt),'forward_error_deg':forward,
                  'backward_error_deg':backward,'slope_difference_deg_s':abs(sa-sb),
                  'score':float(score),'status':'candidate' if accepted else 'rejected',
                  'reason':'angular_continuity' if accepted else 'inconsistent_endpoint_prediction'}
        audit['decisions'].append(decision)
        if accepted:eligible.append(decision)
    # Near-equal competing links remain unresolved instead of forcibly choosing one.
    for d in eligible:
        competitors=[c for c in eligible if c is not d and (c['left']==d['left'] or c['right']==d['right'])]
        if any(c['score']<=d['score']+.35 for c in competitors):
            d.update(status='ambiguous',reason='competing_fragment')
        else:d['status']='retained_hypothesis'
    links={d['left']:d for d in eligible if d['status']=='retained_hypothesis'}
    added=[]
    for first in sorted(links):
        control.check();ids=[first];current=first
        while current in links and len(ids)<4:
            current=links[current]['right'];ids.append(current)
            if len(added)>=MAX_JOINED:break
            parts=[by_id[i] for i in ids];points=[copy.deepcopy(p) for part in parts for p in part['points']]
            for i,p in enumerate(points):
                p['unfolded_deg']=nearest_unfolded(p['bearing_deg'],points[i-1]['unfolded_deg'] if i else p['bearing_deg'])
            added.append({**parts[0],'id':'+'.join(ids),'points':points,'support':len(points),
                          'start_t':points[0]['t'],'end_t':points[-1]['t'],
                          'fragment_ids':list(ids),'gap_hypothesis':True,
                          'missing_intervals':[[x['end_t'],y['start_t']] for x,y in zip(parts,parts[1:])]})
        if len(added)>=MAX_JOINED:break
    result['tracks']+=added;result['gap_review_applied']=True
    audit.update(status='reviewed',retained_joined_hypotheses=len(added),
                 dual_array_validation='pending_pair_fit_and_exclusive_selection')
    result['gap_continuity']=audit
    return result,audit
