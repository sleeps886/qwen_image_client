"""Check model-authored coordinates against uploaded observations, never labels."""
import numpy as np
from backend.app.services.localization.forward_observation import GeoReference,observe


def audit_geometry(document,candidates,evidence,context,selection=None):
    reference=GeoReference(*context['platforms'][0]['start_ll'],context.get('EARTH_R',6371000.))
    platform=context['platforms'][0];aliases={};hypotheses={}
    for track in candidates['tracks']:
        aliases[track['id']]=track['id'];aliases[track['id']+'|'+track.get('branch','main')]=track['id']
        hypotheses[track['id']]=track.get('association_hypothesis_id')
    supports={c['id']:set(c['supported_measurement_ids']) for c in (selection or {}).get('selected',[])}
    reports=[]
    for track in document['tracks']:
        source_ids={aliases[s] for s in track['source_candidate_ids']}
        allowed=set().union(*(supports.get(hypotheses[i],set()) for i in source_ids))
        points=track['points'];times=[p['t'] for p in points];errors=[];arrays={}
        for image in evidence['images']:
            groups={}
            for p in image['candidates']:
                if times[0]<=p['t']<=times[-1] and (not allowed or p['id'] in allowed):groups.setdefault(p['t'],[]).append(p)
            residuals=[]
            for time,measurements in sorted(groups.items()):
                xy=(float(np.interp(time,times,[p['x'] for p in points])),float(np.interp(time,times,[p['y'] for p in points])))
                try:bearing=observe(platform,reference.to_ll(xy),time,reference)[image['array']+'_deg']
                except ValueError:continue
                residuals.append(min(abs(bearing-p['bearing_deg']) for p in measurements))
            errors.extend(residuals)
            arrays[image['array']]={'samples':len(residuals),'rmse_deg':float(np.sqrt(np.mean(np.square(residuals)))) if residuals else None}
        rmse=float(np.sqrt(np.mean(np.square(errors)))) if errors else None
        reports.append({'output_id':track['id'],'branch':track.get('branch','main'),
                        'source_identity_ids':sorted(source_ids),'source_measurements_only':bool(allowed),
                        'samples':len(errors),'rmse_deg':rmse,'arrays':arrays,
                        'status':'insufficient_observations' if len(errors)<4 else 'observation_mismatch' if rmse>1.5 else 'observation_consistent_only'})
    return {'tracks':reports,'method':'linear_interpolation_of_model_points_to_observation_times',
            'warning_track_count':sum(t['status']!='observation_consistent_only' for t in reports),
            'threshold_deg':1.5,'threshold_is_engineering_heuristic':True,
            'independent_truth_used':False,'independent_position_accuracy_verified':False,
            'mirror_resolved_by_this_check':False}
