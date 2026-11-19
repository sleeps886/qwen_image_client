"""Predict missing intervals within one motion hypothesis; never claim observations."""
from collections import defaultdict
import math
import numpy as np
from backend.app.services.localization.batch_inverse import evaluate_path,reference_for


def estimate_model_gaps(tracks,context,*,max_gap_s=90.):
    groups=defaultdict(list)
    for track in tracks:
        if not track.get('model_parameters') or not track.get('association_hypothesis_id'):
            continue
        key=(track['id'],track.get('platform_id'),track['association_hypothesis_id'],track.get('mirror_variant'))
        if type(key[-1]) is not int:continue
        groups[key].append(track)
    reference=reference_for(context);output=[]
    for key,segments in groups.items():
        segments=sorted(segments,key=lambda t:t['points'][0]['t'])
        for left,right in zip(segments,segments[1:]):
            if left['model_parameters']!=right['model_parameters']:continue
            start,end=left['points'][-1],right['points'][0]
            duration=end['t']-start['t']
            if not 0<duration<=max_gap_s:continue
            variant=left['model_parameters']
            # Keep prediction density bounded, include supported endpoints exactly.
            times=np.linspace(start['t'],end['t'],min(257,max(3,math.ceil(duration/3)+1)))
            xy=evaluate_path(variant['parameters'],times,variant['model'],variant['t0'],variant['span'])
            if not np.isfinite(xy).all():continue
            points=[]
            for time,position in zip(times,xy):
                lat,lon=reference.to_ll(tuple(map(float,position)))
                points.append({'t':float(time),'x':lon,'y':lat,'point_source':'motion_model_gap_prediction'})
            # Reject incompatible/stale models instead of drawing an artificial jump.
            if any(abs(points[index][axis]-endpoint[axis])>1e-7
                   for index,endpoint in ((0,start),(-1,end)) for axis in ('x','y')):continue
            output.append({'id':key[0],'platform_id':key[1],'association_hypothesis_id':key[2],
                'mirror_variant':key[3],'start_t':start['t'],'end_t':end['t'],
                'estimated_not_observed':True,'used_for_evaluation':False,'points':points})
    return output
