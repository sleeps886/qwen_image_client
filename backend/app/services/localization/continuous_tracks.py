"""Bounded, measurement-constrained window estimation. No fabricated timestamps."""
import copy
import math
import os

import numpy as np
from scipy.optimize import least_squares

from backend.app.services.localization.spatial_tracks import array_position,MAX_RANGE_M


def tracking_options(duration):
    defaults={'max_speed_mps':30.,'max_acceleration_mps2':.5,'acceleration_prior_mps2':.1,
              'bearing_sigma_deg':1.,'max_angle_error_deg':2.,
              'max_gap_s':max(1.,duration*.94/15*1.5),'window_points':9}
    ranges={'max_speed_mps':(.1,200),'max_acceleration_mps2':(.001,20),
            'acceleration_prior_mps2':(.001,20),'bearing_sigma_deg':(.05,10),
            'max_angle_error_deg':(.05,20),'max_gap_s':(.01,604800),'window_points':(3,15)}
    for key,default in defaults.items():
        try:value=float(os.environ.get('TRACK_'+key.upper(),default))
        except (ValueError,TypeError):raise ValueError('TRACK_'+key.upper()+'必须为有效数值') from None
        lo,hi=ranges[key]
        if not math.isfinite(value) or not lo<=value<=hi or key=='window_points' and value!=int(value):
            raise ValueError('TRACK_'+key.upper()+'超出允许范围')
        defaults[key]=int(value) if key=='window_points' else value
    return defaults


def to_xy(point,origin):
    return np.array([6371000*math.cos(math.radians(origin[0]))*math.radians(point['x']-origin[1]),
                     6371000*math.radians(point['y']-origin[0])])


def from_xy(xy,origin):
    return origin[1]+math.degrees(xy[0]/(6371000*math.cos(math.radians(origin[0])))),origin[0]+math.degrees(xy[1]/6371000)


def angle_difference(a,b):
    return (a-b+180)%360-180


def source_for(track,document):
    return document['observations']['tracks'][track['source_track_index']]


def contiguous(previous,current,observations,options):
    a,b=previous['observation_index'],current['observation_index']
    pa,pb=observations['points'][a],observations['points'][b]
    sa,sb=pa.get('sample_index'),pb.get('sample_index')
    return (b==a+1 and 0<current['t']-previous['t']<=options['max_gap_s']
            and (sa is None or sb is None or sb==sa+1))


def estimate_continuous(document,context):
    result=copy.deepcopy(document);origin=context['platforms'][0]['start_ll']
    options=context.get('tracking_options') or tracking_options(context['T_MAX'])
    report={'method':'overlapping_bearing_residual_windows','options':options,'optimized_windows':0,
            'failed_windows':0,'short_segments':0,'splits':0,'input_branch_points':sum(len(t['points']) for t in result['tracks']),
            'output_branch_points':0,'added_timestamps':0,'statistical_confidence':False}
    output=[]
    for track in result['tracks']:
        observations=source_for(track,result)
        platform=next(p for p in context['platforms'] if p['name']==track['platform_id'])
        segments=[];segment=[]
        for point in track['points']:
            if segment and not contiguous(segment[-1],point,observations,options):
                segments.append(segment);segment=[];report['splits']+=1
            segment.append(point)
        if segment:segments.append(segment)
        for segment_index,points in enumerate(segments):
            if len(points)<2:continue
            seed=np.array([to_xy(p,origin) for p in points]);times=np.array([p['t'] for p in points])
            estimates=[[] for _ in points];window=options['window_points']
            if len(points)<3:report['short_segments']+=1
            else:
                starts=list(range(0,max(1,len(points)-window+1),max(1,window-2)))
                if starts[-1]!=max(0,len(points)-window):starts.append(max(0,len(points)-window))
                for start in starts:
                    if report['optimized_windows']+report['failed_windows']>=256:
                        raise ValueError('连续轨迹优化窗口超过256个预算，请拆分目标数据')
                    stop=min(start+window,len(points));local=points[start:stop];initial=seed[start:stop]
                    local_times=times[start:stop];dt=np.diff(local_times)
                    origins=np.array([[array_position(platform,platform[array],p['t'],origin)
                                       for array in ('hull_m','tow_m')] for p in local])
                    measures=np.array([[platform['heading_deg']+sign*observations['points'][p['observation_index']][key]
                                        for sign,key in zip(track['handedness'],('hull_deg','tow_deg'))] for p in local])
                    radius=np.array([max(20,min(500,p['sensitivity_m'])) for p in local])
                    sigmas=np.full((len(local),2),options['bearing_sigma_deg'])
                    if context.get('use_candidate_uncertainty'):
                        sigmas=np.array([[max(.15,observations['points'][p['observation_index']].get(k,options['bearing_sigma_deg']))
                                          for k in ('hull_error_deg','tow_error_deg')] for p in local])
                    def residual(flat):
                        xy=flat.reshape(-1,2);delta=xy[:,None,:]-origins
                        angles=np.degrees(np.arctan2(delta[:,:,0],delta[:,:,1]))
                        bearing=(angle_difference(angles,measures)/sigmas).ravel()
                        velocity=np.diff(xy,axis=0)/dt[:,None]
                        acceleration=np.diff(velocity,axis=0)/((dt[:-1]+dt[1:])/2)[:,None]
                        speed=np.maximum(0,np.linalg.norm(velocity,axis=1)-options['max_speed_mps'])/5
                        prior=(xy-initial).ravel()/np.repeat(radius,2)*.25
                        return np.concatenate((bearing,acceleration.ravel()/options['acceleration_prior_mps2'],speed,prior))
                    fit=least_squares(residual,initial.ravel(),bounds=((initial-radius[:,None]).ravel(),(initial+radius[:,None]).ravel()),
                                      loss='huber',f_scale=1,max_nfev=80)
                    if not fit.success or not np.isfinite(fit.x).all():report['failed_windows']+=1;continue
                    report['optimized_windows']+=1
                    for i,xy in enumerate(fit.x.reshape(-1,2)):estimates[start+i].append(xy)
            estimated=[np.mean(e,axis=0) if e else seed[i] for i,e in enumerate(estimates)]
            accepted=[];last_velocity=None;last_dt=None;piece=0
            def flush():
                nonlocal accepted,piece,last_velocity,last_dt
                if len(accepted)>=2:
                    output.append({**track,'branch':track['branch']+f':w{segment_index}.{piece}',
                                   'points':accepted,'estimation':'window_optimized' if len(points)>=3 and any(estimates) else 'short_or_unoptimized'})
                accepted=[];piece+=1;last_velocity=None;last_dt=None
            for i,(point,xy) in enumerate(zip(points,estimated)):
                if accepted:
                    dt=point['t']-accepted[-1]['t'];velocity=(xy-to_xy(accepted[-1],origin))/dt
                    speed=float(np.linalg.norm(velocity))
                    acceleration=0 if last_velocity is None else float(np.linalg.norm(velocity-last_velocity)/((dt+last_dt)/2))
                    if speed>options['max_speed_mps'] or acceleration>options['max_acceleration_mps2']:
                        report['splits']+=1;flush()
                    else:last_velocity=velocity;last_dt=dt
                lon,lat=from_xy(xy,origin)
                accepted.append({**point,'x':lon,'y':lat,'adjustment_m':round(float(np.linalg.norm(xy-seed[i])),4),
                                 'sensitivity_reference':'raw_ray_intersection_not_posterior_confidence'})
            flush()
    result['tracks']=output
    report['output_branch_points']=sum(len(t['points']) for t in output)
    result['tracking']=report
    result['warnings'].append('已执行有界观测残差窗口估计；速度、加速度及角度误差为工程假设，不是目标真值或统计置信区间。缺测不补点，镜像未消歧。')
    return result,report


def project_point(point,platform,array,context):
    origin=context['platforms'][0]['start_ll'];xy=to_xy(point,origin)
    center=np.array(array_position(platform,platform[array+'_m'],point['t'],origin))
    delta=xy-center;distance=float(np.linalg.norm(delta))
    global_angle=math.degrees(math.atan2(delta[0],delta[1]))
    relative=(global_angle-platform['heading_deg'])%360
    return min(relative,360-relative),distance,angle_difference(relative,0)


def reproject_tracks(document,context,evidence):
    result=copy.deepcopy(document);options=context.get('tracking_options') or tracking_options(context['T_MAX'])
    lookup={p['id']:(im,p) for im in evidence['images'] for p in im['candidates']}
    report={'method':'local_geometry_to_locked_pixel_candidates','angle_gate_deg':options['max_angle_error_deg'],
            'statistical_confidence':False,'rows':[],'checked_branch_points':0,'rejected_branch_points':0,
            'notes':['重投影检查与已选择候选的一致性，不是独立真值验证；错误ROI、杂波关联和镜像也可能通过。']}
    output=[]
    for track in result['tracks']:
        observations=source_for(track,result);platform=next(p for p in context['platforms'] if p['name']==track['platform_id'])
        segment=[];piece=0
        def flush():
            nonlocal segment,piece
            if len(segment)>=2:output.append({**track,'branch':track['branch']+f':r{piece}','points':segment})
            segment=[];piece+=1
        for point in track['points']:
            observation=observations['points'][point['observation_index']];checks=[]
            for array,sign in zip(('hull','tow'),track['handedness']):
                candidate_id=observation.get(array+'_candidate_id')
                if candidate_id not in lookup:raise ValueError('重投影缺少已锁定候选点，不能独立核对像素')
                im,candidate=lookup[candidate_id]
                if im['platform_id']!=track['platform_id'] or im['array']!=array:raise ValueError('重投影候选平台或阵列不一致')
                bearing,distance,signed_bearing=project_point(point,platform,array,context)
                left,top,right,bottom=im['roi'];bmin,bmax=context.get('B_MIN',0),context.get('B_MAX',180)
                x=left+(bearing-bmin)/(bmax-bmin)*(right-left)
                ratio=point['t']/context['T_MAX']
                y=bottom-ratio*(bottom-top) if context.get('image_time_axis','bottom_to_top')=='bottom_to_top' else top+ratio*(bottom-top)
                angle_error=abs(bearing-observation[array+'_deg']);pixel_error=math.hypot(x-candidate['x_px'],y-candidate['y_px'])
                signed_error=abs(angle_difference(signed_bearing,sign*observation[array+'_deg']))
                angle_gate=options['max_angle_error_deg']
                if context.get('use_candidate_uncertainty'):angle_gate=min(angle_gate,max(.3,3*observation.get(array+'_error_deg',1.)))
                pixel_gate=max(3,angle_gate/(bmax-bmin)*(right-left))
                passed=(0<distance<=MAX_RANGE_M and left<=x<=right and top<=y<=bottom
                        and angle_error<=angle_gate and pixel_error<=pixel_gate)
                passed=passed and signed_error<=angle_gate
                checks.append({'array':array,'candidate_id':candidate_id,'image_index':im['image_index'],
                               'x_px':x,'y_px':y,'angle_error_deg':angle_error,'signed_error_deg':signed_error,'pixel_error':pixel_error,'passed':passed})
            passed=all(c['passed'] for c in checks);report['checked_branch_points']+=1
            report['rows'].append({'id':track['id'],'branch':track['branch'],'t':point['t'],'passed':passed,'arrays':checks})
            if not passed:report['rejected_branch_points']+=1;flush();continue
            segment.append({**point,'reprojection_max_error_deg':max(c['angle_error_deg'] for c in checks)})
        flush()
    result['tracks']=output
    report['max_angle_error_deg']=max((c['angle_error_deg'] for row in report['rows'] for c in row['arrays']),default=None)
    report['final_branch_points']=sum(len(t['points']) for t in output)
    report['status']='partial_candidates' if output else 'needs_review'
    # Coverage counts unique observations, not mirrored branches as extra detections.
    supplied=sum(len(t['points']) for t in result['observations']['tracks'])
    retained={(t['source_track_index'],p['observation_index']) for t in output for p in t['points']}
    report['observation_count']=supplied;report['retained_unique_observations']=len(retained)
    report['retained_observation_fraction']=len(retained)/supplied if supplied else 0
    result['reprojection_summary']={k:v for k,v in report.items() if k!='rows'}
    result['warnings']+=report['notes']
    return result,report
