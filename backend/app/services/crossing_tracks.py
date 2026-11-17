"""Resolve isolated X crossings with two-sided tangents, not a target-count prior.

Only existing, unblended observations are linked. This is a local continuity
hypothesis, not proof of physical targets or of cross-array correspondence.
"""
from itertools import combinations

import numpy as np


def crossing_continuity(evidence, fragments):
    lookup={p['id']:p for im in evidence['images'] for p in im['candidates']}
    ambiguous={i for f in fragments for i in f.get('ambiguous_candidate_ids',[])}
    sections=[]
    for fragment in fragments:
        part=[];index=0
        def flush():
            nonlocal part,index
            if len(part)>=2:
                sections.append({'id':fragment['id']+f':section{index}',
                                 'image_index':fragment['image_index'],'points':part})
            part=[];index+=1
        for cid in fragment['candidate_ids']:
            point=lookup[cid]
            if cid in ambiguous:
                flush();continue
            if part and point['sample_index']!=part[-1]['sample_index']+1:flush()
            part.append(point)
        flush()

    def tangent(section, tail):
        points=section['points'][-3:] if tail else section['points'][:3]
        x=np.array([p['sample_index'] for p in points],dtype=float)
        y=np.array([p['bearing_deg'] for p in points],dtype=float)
        slope,intercept=np.polyfit(x,y,1)
        return float(slope),float(intercept),float(np.max(np.abs(y-(slope*x+intercept))))

    eligible=[s for s in sections if len(s['points'])>=3]
    proposals=[]
    for a,b in combinations(eligible,2):
        if a['image_index']!=b['image_index']:continue
        end=max(a['points'][-1]['sample_index'],b['points'][-1]['sample_index'])
        if abs(a['points'][-1]['sample_index']-b['points'][-1]['sample_index'])>1:continue
        sa,ia,ra=tangent(a,True);sb,ib,rb=tangent(b,True)
        if sa*sb>=0 or min(abs(sa),abs(sb))<.5 or max(ra,rb)>.75:continue
        cross=(ib-ia)/(sa-sb)
        if not end<cross<end+5:continue
        future=[s for s in eligible if s['image_index']==a['image_index'] and
                1<s['points'][0]['sample_index']-end<=5]
        for c,d in combinations(future,2):
            start=min(c['points'][0]['sample_index'],d['points'][0]['sample_index'])
            if abs(c['points'][0]['sample_index']-d['points'][0]['sample_index'])>1 or not cross<start:continue
            scores=[]
            for after in ((c,d),(d,c)):
                errors=[];valid=True
                for before,target in zip((a,b),after):
                    s,i,r=tangent(before,True);v,j,q=tangent(target,False)
                    lo=before['points'][-1]['sample_index'];hi=target['points'][0]['sample_index']
                    errors.extend((abs(s*hi+i-(v*hi+j)),abs(s*lo+i-(v*lo+j))))
                    valid &= q<=.75 and abs(s-v)<=max(.75,.35*abs(s))
                scores.append((sum(errors),valid and max(errors)<=2.,after))
            scores.sort(key=lambda item:item[0])
            best,other=scores
            if not best[1] or other[0]-best[0]<2.:continue
            proposals.append({'score':best[0],'before':(a,b),'after':best[2],
                              'image_index':a['image_index'],'crossing_sample_index':round(cross,3),
                              'alternative_margin_deg':round(other[0]-best[0],3)})

    # Competing crossing explanations remain unresolved instead of picking the
    # first one. Unique segment ownership also prevents duplicate target chains.
    usage={}
    for proposal in proposals:
        for section in (*proposal['before'],*proposal['after']):
            usage[section['id']]=usage.get(section['id'],0)+1
    groups={s['id']:s['id'] for s in sections};events=[]
    for p in proposals:
        if any(usage[s['id']]!=1 for s in (*p['before'],*p['after'])):continue
        links=[]
        for before,after in zip(p['before'],p['after']):
            groups[after['id']]=before['id']
            links.append({'before_section':before['id'],'after_section':after['id'],
                          'before_ids':[x['id'] for x in before['points']],
                          'after_ids':[x['id'] for x in after['points']]})
        events.append({'image_index':p['image_index'],'status':'two_ridge_continuity_hypothesis',
                       'crossing_sample_index':p['crossing_sample_index'],
                       'alternative_margin_deg':p['alternative_margin_deg'],'links':links,
                       'shared_crossing_observation_used':False})
    mapping={p['id']:groups[s['id']] for s in sections for p in s['points']}
    return {'sections':mapping,'events':events,'ambiguous_ids':sorted(ambiguous),
            'method':'bidirectional_tangent_unique_x_v1','target_count_confirmed':False}
