"""Join compatible model fragments for display without changing model observations."""
import copy
from collections import defaultdict
import numpy as np


def continuous_model_paths(tracks,side):
    groups=defaultdict(list)
    for index,track in enumerate(tracks):
        branch=side(track)
        key=(track['id'],branch) if branch[0]=='source_variant' else ('unresolved',index)
        groups[key].append(track)
    output=[];decisions=[];joined=0
    for group in groups.values():
        ordered=sorted(group,key=lambda t:t['points'][0]['t']);chains=[[ordered[0]]]
        for track in ordered[1:]:
            previous=chains[-1][-1];a=previous['points'];b=track['points'];dt=b[0]['t']-a[-1]['t']
            status='time_overlap_or_gap_exceeds_120s';accepted=False
            if 0<dt<=120 and len(a)>=2 and len(b)>=2:
                def velocity(points,tail):
                    points=points[-5:] if tail else points[:5]
                    return np.median([(np.array([q['x'],q['y']])-np.array([p['x'],p['y']]))/(q['t']-p['t'])
                                      for i,p in enumerate(points) for q in points[i+1:] if q['t']>p['t']],axis=0)
                va,vb=velocity(a,True),velocity(b,False)
                delta=np.array([b[0]['x']-a[-1]['x'],b[0]['y']-a[-1]['y']])
                speed=max(float(np.linalg.norm(va)),float(np.linalg.norm(vb)),1.)
                error=max(float(np.linalg.norm(delta-va*dt)),float(np.linalg.norm(delta-vb*dt)))
                accepted=speed<=30 and error<=max(25.,.2*speed*dt) and np.linalg.norm(va-vb)<=max(2.,.3*speed)
                status='same_identity_side_and_endpoint_motion' if accepted else 'endpoint_motion_mismatch'
            decisions.append({'id':track['id'],'left_branch':previous.get('branch'),
                              'right_branch':track.get('branch'),'gap_seconds':dt,'joined':bool(accepted),'reason':status})
            if accepted:chains[-1].append(track);joined+=1
            else:chains.append([track])
        for chain in chains:
            merged=copy.deepcopy(chain[0])
            merged['render_mirror_variant']=side(chain[0])[1] if side(chain[0])[0]=='source_variant' else chain[0].get('mirror_variant',0)
            if len(chain)>1:
                merged['points']=[copy.deepcopy(p) for t in chain for p in t['points']]
                merged['render_segments']=[copy.deepcopy(t['points']) for t in chain]
                merged['render_gap_links']=[[copy.deepcopy(a['points'][-1]),copy.deepcopy(b['points'][0])] for a,b in zip(chain,chain[1:])]
                merged['source_fragment_branches']=[t.get('branch','main') for t in chain]
                merged['source_candidate_ids']=list(dict.fromkeys(s for t in chain for s in t.get('source_candidate_ids',[])))
                merged['continuity']={'estimated_not_observed':True,'method':'same_identity_side_and_bidirectional_motion',
                                      'source_fragment_count':len(chain),'added_measurement_points':0}
            output.append(merged)
    return output,{'method':'model_fragment_display_continuity','merged_fragment_count':joined,
                   'source_fragment_count':len(tracks),'output_track_count':len(output),
                   'added_measurement_points':0,'identity_confirmed':False,'decisions':decisions}
