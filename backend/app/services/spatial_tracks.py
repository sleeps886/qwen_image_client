"""Local dual-array ray intersection. No truth files, inferred ranges or code execution."""
import math

ANGLE_ERROR_DEG = 1.0  # Sensitivity assumption, NOT a calibrated statistical confidence.
MAX_RANGE_M = 100000.0


def cross(a,b):
    return a[0]*b[1]-a[1]*b[0]


def direction(degrees):
    angle=math.radians(degrees)
    return math.sin(angle),math.cos(angle)


def intersection(h,t,ha,ta,min_angle_deg=1.):
    u,v=direction(ha),direction(ta)
    det=cross(u,v)
    if abs(det)<math.sin(math.radians(min_angle_deg)):
        return None
    delta=(t[0]-h[0],t[1]-h[1])
    rh,rt=cross(delta,v)/det,cross(delta,u)/det
    if not 0<rh<=MAX_RANGE_M or not 0<rt<=MAX_RANGE_M:
        return None
    return h[0]+rh*u[0],h[1]+rh*u[1],max(rh,rt)


def array_position(platform,offset,t,origin):
    lat,lon=platform['start_ll']
    x=6371000*math.cos(math.radians(origin[0]))*math.radians(lon-origin[1])
    y=6371000*math.radians(lat-origin[0])
    forward=direction(platform['heading_deg'])
    speed=platform['speed_kn']*1852/3600
    # Generator convention: offset[0] forward, offset[1] to port.
    return (x+(speed*t+offset[0])*forward[0]-offset[1]*forward[1],
            y+(speed*t+offset[0])*forward[1]+offset[1]*forward[0])


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def solve_spatial(document,context):
    if not context or not context.get('platforms'):
        raise ValueError('空间定位需要同一样本的input/platform.json；未生成方位—时间替代图。')
    if document.get('coordinate_system')!='dual_array_bearings':
        raise ValueError('空间定位需要dual_array_bearings双阵同目标同步方位点；不接受模型直接猜测位置或方位—时间替代图。')
    tracks=document.get('tracks')
    if not isinstance(tracks,list) or not 1<=len(tracks)<=50:
        raise ValueError('没有可定位的双阵匹配轨迹')
    warnings=document.get('warnings',[])
    if not isinstance(warnings,list) or len(warnings)>100 or any(not isinstance(w,str) or len(w)>2000 for w in warnings):
        raise ValueError('warnings格式无效')
    platforms=context['platforms'];origin=platforms[0]['start_ll']
    if abs(origin[0])>=85:
        raise ValueError('当前局部平面定位不支持极区')
    output=[];rejected=0;total=0
    for source_index,track in enumerate(tracks):
        if not isinstance(track,dict) or not isinstance(track.get('id'),str) or not 1<=len(track['id'])<=80:
            raise ValueError('双阵目标编号无效')
        branch=track.get('branch','main')
        if not isinstance(branch,str) or len(branch)>80:raise ValueError('双阵分支编号无效')
        matches=[p for p in platforms if p.get('name')==track.get('platform_id')]
        if len(matches)!=1:raise ValueError('双阵点必须明确且唯一对应platform_id')
        platform=matches[0]
        if math.dist(platform['hull_m'],platform['tow_m'])<1:
            raise ValueError('双阵基线不足1米，无法稳定定位')
        points=track.get('points')
        if not isinstance(points,list) or not 2<=len(points)<=500:
            raise ValueError('每条双阵轨迹需要2～500个同步观测点')
        total+=len(points)
        if total>5000:raise ValueError('双阵观测总数超限')
        previous=-1
        for point in points:
            if not isinstance(point,dict) or not all(finite(point.get(k)) for k in ('t','hull_deg','tow_deg')):
                raise ValueError('双阵点必须包含有限数值t/hull_deg/tow_deg')
            if not previous<point['t']<=context['T_MAX'] or point['t']<0:
                raise ValueError('双阵时间必须递增并在观测范围内')
            previous=point['t']
            if not all(context.get('B_MIN',0)<=point[k]<=context.get('B_MAX',180) for k in ('hull_deg','tow_deg')):
                raise ValueError('双阵方位超出图片标定范围')
        # Keep each handedness hypothesis separate. Never pick the one nearest truth.
        for hs,ts in ((1,1),(-1,-1),(1,-1),(-1,1)):
            segment=[];segment_id=0
            def flush():
                nonlocal segment,segment_id
                if len(segment)>=2:
                    output.append({'id':track['id'],'branch':f'{branch}:h{hs:+}t{ts:+}:s{segment_id}',
                                   'points':segment,'platform_id':track['platform_id'],
                                   'candidate':True,'source_track_index':source_index,'handedness':[hs,ts]})
                segment=[];segment_id+=1
            for observation_index,point in enumerate(points):
                time=point['t']
                h=array_position(platform,platform['hull_m'],time,origin)
                t=array_position(platform,platform['tow_m'],time,origin)
                ha=platform['heading_deg']+hs*point['hull_deg']
                ta=platform['heading_deg']+ts*point['tow_deg']
                he=te=ANGLE_ERROR_DEG;min_angle=1.
                if context.get('use_candidate_uncertainty'):
                    he,te=(point.get(k,ANGLE_ERROR_DEG) for k in ('hull_error_deg','tow_error_deg'))
                    if not all(finite(v) and .15<=v<=2 for v in (he,te)):raise ValueError('候选角度误差假设无效')
                    min_angle=max(.05,min(.5,min(he,te)*.5))
                solution=intersection(h,t,ha,ta,min_angle)
                error=math.inf
                if solution:
                    perturbed=[intersection(h,t,ha+dh,ta+dt,min_angle) for dh,dt in
                               ((he,0),(-he,0),(0,te),(0,-te))]
                    if all(p is not None for p in perturbed):
                        error=max(math.dist(p[:2],solution[:2]) for p in perturbed)
                if not solution or error>min(5000,solution[2]*0.5):
                    rejected+=1;flush();continue
                x,y=solution[:2]
                segment.append({'t':time,'x':origin[1]+math.degrees(x/(6371000*math.cos(math.radians(origin[0])))),
                                'y':origin[0]+math.degrees(y/6371000),
                                'observation_index':observation_index,
                                'angle_error_assumption_deg':[he,te],
                                'sensitivity_m':round(error,2)})
            flush()
    if not output:
        raise ValueError('空间定位未通过：双阵射线不相交、视差不足或对本次角误差假设过于敏感；请核对亮带配对，不能据此生成可靠空间轨迹。')
    return {'coordinate_system':'lon_lat','position_observable':True,'plot_kind':'spatial_candidates',
            'tracks':output,'observations':document,
            'localization':{'method':'dual_array_local_tangent_ray_intersection',
                            'angle_error_assumption_deg':None if context.get('use_candidate_uncertainty') else ANGLE_ERROR_DEG,
                            'default_angle_error_deg':ANGLE_ERROR_DEG,
                            'error_method':'pixel_resolution_and_local_ridge_proxy' if context.get('use_candidate_uncertainty') else 'fixed_one_degree',
                            'rejected_candidate_points':rejected,'unique_solution':False},
            'warnings':['双阵几何空间候选解，未通过真值核验；目标关联仍来自大模型。',
                        '折叠方位存在左右舷镜像；候选分支不是新增目标，未自动选择唯一轨迹。',
                        '采用局部切平面和恒航向恒航速平台模型；角误差为像素/局部亮带代理或缺省1°，均未经真实样本标定，不是统计置信度。',
                        '拒绝相对角误差仍不稳定、反向或超过100公里的交点；缺失段不补点。']+warnings}
