"""Fail-closed model coordinate checks against source observations, never labels."""
import math
import numpy as np
from backend.app.services.localization.forward_observation import GeoReference,observe


class ModelGeometryRejected(ValueError):
    pass


def require_geometry(report):
    if not report.get('gate_passed'):
        failed=[t.get('output_id','?')+':'+t.get('status','unknown') for t in report.get('tracks',[]) if t.get('status')!='observation_consistent_only']
        raise ModelGeometryRejected('模型坐标未通过双阵方位与方向校验：'+('；'.join(failed) or '缺少有效质检记录')+'。保留原回复及诊断，不生成最终轨迹。')


def audit_geometry(document,candidates,evidence,context,selection=None):
    reference=GeoReference(*context['platforms'][0]['start_ll'],context.get('EARTH_R',6371000.))
    platform=context['platforms'][0];aliases={};hypotheses={};sources={}
    for track in candidates['tracks']:
        aliases[track['id']]=track['id'];aliases[track['id']+'|'+track.get('branch','main')]=track['id']
        hypotheses[track['id']]=track.get('association_hypothesis_id')
        sources.setdefault(track['id'],[]).append(track)
        sources[track['id']+'|'+track.get('branch','main')]=[track]
    supports={c['id']:set(c['supported_measurement_ids']) for c in (selection or {}).get('selected',[])}
    reports=[]
    def interpolate(points,time,frame='local_xy_m'):
        x=float(np.interp(time,[p['t'] for p in points],[p['x'] for p in points]))
        y=float(np.interp(time,[p['t'] for p in points],[p['y'] for p in points]))
        return reference.to_xy((y,x)) if frame=='lon_lat' else (x,y)
    def errors(points,groups,array,frame='local_xy_m'):
        out=[]
        for time,measurements in groups.items():
            if not points[0]['t']<=time<=points[-1]['t']:continue
            try:
                bearing=observe(platform,reference.to_ll(interpolate(points,time,frame)),time,reference)[array+'_deg']
                out.append(min(abs(bearing-p['bearing_deg']) for p in measurements))
            except ValueError:continue
        return out
    def rmse(values):return float(np.sqrt(np.mean(np.square(values)))) if values else None
    for track in document['tracks']:
        source_ids={aliases[s] for s in track['source_candidate_ids']}
        allowed=set().union(*(supports.get(hypotheses[i],set()) for i in source_ids))
        points=track['points'];all_errors=[];arrays={};groups_by_array={}
        for image in evidence['images']:
            groups={}
            for p in image['candidates']:
                if points[0]['t']<=p['t']<=points[-1]['t'] and p['id'] in allowed:groups.setdefault(p['t'],[]).append(p)
            residuals=errors(points,groups,image['array']);all_errors.extend(residuals)
            groups_by_array[image['array']]=groups
            arrays[image['array']]={'samples':len(residuals),'rmse_deg':rmse(residuals)}
        common=set(groups_by_array.get('hull',{}))&set(groups_by_array.get('tow',{}))
        insufficient=len(common)<4 or any(arrays.get(a,{}).get('samples',0)<4 for a in ('hull','tow'))
        mismatch=any(arrays.get(a,{}).get('rmse_deg') is None or arrays[a]['rmse_deg']>1.5 for a in ('hull','tow'))
        baseline=[];heading_changes=[]
        for source in track['source_candidate_ids']:
            for candidate in sources.get(source,[]):
                source_points=candidate['points'];frame=candidates.get('coordinate_system','local_xy_m')
                values=[e for array,groups in groups_by_array.items() for e in errors(source_points,groups,array,frame)]
                if values:baseline.append(rmse(values))
                begin=max(points[0]['t'],source_points[0]['t']);end=min(points[-1]['t'],source_points[-1]['t'])
                if end<=begin:continue
                a=np.subtract(interpolate(source_points,end,frame),interpolate(source_points,begin,frame))
                b=np.subtract(interpolate(points,end),interpolate(points,begin))
                if min(np.linalg.norm(a),np.linalg.norm(b))<1.:continue
                angle=math.degrees(math.atan2(b[0],b[1])-math.atan2(a[0],a[1]))
                heading_changes.append(abs((angle+180)%360-180))
        combined=rmse(all_errors);base=min(baseline) if baseline else None
        change=min(heading_changes) if heading_changes else None
        improved=base is not None and combined is not None and base-combined>=max(.2,.25*base)
        unsupported_rotation=change is not None and change>15 and not improved
        status='insufficient_observations' if insufficient else 'observation_mismatch' if mismatch else 'unsupported_heading_change' if unsupported_rotation else 'observation_consistent_only'
        reports.append({'output_id':track['id'],'branch':track.get('branch','main'),
                        'source_identity_ids':sorted(source_ids),'source_measurements_only':bool(allowed),
                        'samples':len(all_errors),'synchronous_samples':len(common),'rmse_deg':combined,'arrays':arrays,
                        'candidate_heading_change_deg':change,'candidate_baseline_rmse_deg':base,
                        'heading_change_supported_by_improved_observations':bool(improved),'status':status})
    warnings=sum(t['status']!='observation_consistent_only' for t in reports)
    return {'tracks':reports,'method':'source_bound_dual_array_reprojection_gate_v2',
            'warning_track_count':warnings,'gate_passed':bool(reports) and warnings==0,
            'threshold_deg':1.5,'threshold_is_engineering_heuristic':True,
            'minimum_synchronous_samples':4,'heading_change_review_deg':15,
            'independent_truth_used':False,'independent_position_accuracy_verified':False,
            'mirror_resolved_by_this_check':False}
