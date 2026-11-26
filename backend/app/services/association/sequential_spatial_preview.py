"""Conditional geometry of visible dual-array units; not a model forecast.

No truth, source catalog, future units, assumed range or selected mirror branch.
"""
import math
from backend.app.services.localization.spatial_tracks import array_position,intersection


def spatial_preview(view,config):
    out={'protocol':'sequential-spatial-v1','coordinate_system':'local_xy_m','tracks':[],
         'platforms':[],'unavailable':[],'unique_solution':False,'association_confirmed':False,
         'reason':'双阵输入配对假设下的几何候选；不是已确认目标或未来预测。镜像分支不代表多个目标。'}
    if 'model_position_choices' in view:
        choices = view['model_position_choices']
        out['branch_selection_source'] = 'model'
        out['model_branch_choices'] = {u['unit_id']: choices.get(u['candidate_ids'][0])
            if u.get('validated', True) and all(c in choices for c in u['candidate_ids'])
            and len({choices[c] for c in u['candidate_ids']}) == 1 else None for u in view['units']}
    platforms=(config or {}).get('platforms',[])
    if not platforms:
        reason='缺少平台位置、航向及双阵偏移，不能把方位角转换为距离。请导入平台配置。'
        out.update(status='missing_platform',reason=reason,unavailable=[{'unit_id':u['unit_id'],'candidate_ids':u['candidate_ids'],'reason':reason} for u in view['units']]);return out
    origin=platforms[0]['start_ll'];out['origin_lat_lon']=list(origin)
    if abs(origin[0])>=85:
        reason='局部平面空间预览不支持极区。'
        out.update(status='unsupported_origin',reason=reason,unavailable=[{'unit_id':u['unit_id'],'candidate_ids':u['candidate_ids'],'reason':reason} for u in view['units']]);return out
    lookup={p['candidate_id']:p for p in view['points']};membership=view['membership'];paths={};times={}
    for unit in view['units']:
        ps=[lookup[c] for c in unit['candidate_ids'] if c in lookup]
        arrays={p['array']:p for p in ps};pid=ps[0]['platform_id'] if ps else None
        row={'unit_id':unit['unit_id'],'candidate_ids':unit['candidate_ids'],'reason':''}
        if len(ps)!=2 or set(arrays)!={'hull','tow'}:
            row['reason']='缺少完整双阵；不能由单阵方位猜距离';out['unavailable'].append(row);continue
        h,t=arrays['hull'],arrays['tow'];time=h['t']
        if h['platform_id']!=t['platform_id'] or h['t']!=t['t']:
            row['reason']='双阵不在同平台同一时刻';out['unavailable'].append(row);continue
        platform=next((p for p in platforms if p['name']==pid),None)
        if not platform:
            row['reason']='未找到对应平台配置';out['unavailable'].append(row);continue
        times.setdefault(pid,set()).add(time)
        if math.dist(platform['hull_m'],platform['tow_m'])<1:
            row['reason']='双阵基线不足1米';out['unavailable'].append(row);continue
        hp=array_position(platform,platform['hull_m'],time,origin);tp=array_position(platform,platform['tow_m'],time,origin)
        hm,tm=(membership.get(p['candidate_id'],{}) for p in (h,t))
        if hm.get('target_id') and tm.get('target_id') and hm['target_id']!=tm['target_id']:
            row['reason']='当前双阵归属指向不同目标，不能按同目标进行空间交会';out['unavailable'].append(row);continue
        linked=unit.get('validated',True) and hm.get('status')==tm.get('status')=='associated'
        target=hm.get('target_id') if hm.get('target_id')==tm.get('target_id') else None
        # Unpaired/invalid observations still have conditional points, never lines.
        pair_key=(target or hm.get('fragment_id'),target or tm.get('fragment_id')) if linked else (unit['unit_id'],unit['unit_id'])
        count=0
        for hs,ts in ((1,1),(-1,-1),(1,-1),(-1,1)):
            ha=platform['heading_deg']+hs*h['bearing_deg'];ta=platform['heading_deg']+ts*t['bearing_deg']
            solution=intersection(hp,tp,ha,ta,min_angle_deg=.05)
            if not solution:continue
            count+=1;x,y,distance=solution
            # Spectral peak width sigma_deg is NOT a calibrated bearing error.
            # Match the existing solver's explicitly labelled sensitivity assumption.
            he=max(.15,h.get('bearing_error_assumption_deg',1.))
            te=max(.15,t.get('bearing_error_assumption_deg',1.))
            variants=[intersection(hp,tp,ha+dh,ta+dt,min_angle_deg=.05) for dh,dt in ((he,0),(-he,0),(0,te),(0,-te))]
            sensitivity=max((math.dist(v[:2],solution[:2]) for v in variants),default=0.) if all(v is not None for v in variants) else None
            stable=sensitivity is not None and sensitivity<=min(5000.,distance*.5)
            key=(pid,*pair_key,hs,ts)
            track=paths.setdefault(key,{'id':f'C{len(paths)+1:03d}','platform_id':pid,'pair_fragments':list(pair_key),
                                       'handedness':[hs,ts],'conditional_pairing':True,'points':[]})
            track['points'].append({'t':time,'x':x,'y':y,'unit_id':unit['unit_id'],
                'candidate_ids':[h['candidate_id'],t['candidate_id']],'stable':stable,'linkable':bool(linked and stable),
                'sensitivity_m':sensitivity,'angle_error_deg':[he,te],
                'angle_error_source':['provided_assumption' if 'bearing_error_assumption_deg' in p else 'default_1deg_sensitivity_not_calibrated' for p in (h,t)],
                'warning':'' if stable else '角度误差下交点不稳定；只画空心候选点，不接成可靠轨迹'})
        if not count:
            row['reason']='射线无前向交点、视差不足或距离超过100千米；未补距离';out['unavailable'].append(row)
    for pid,ts in times.items():
        platform=next(p for p in platforms if p['name']==pid)
        out['platforms'].append({'id':pid,'points':[{'t':t,'x':(p:=array_position(platform,[0,0],t,origin))[0],'y':p[1]}
                                                         for t in sorted({0.,*ts})]})
    out['tracks']=list(paths.values())
    edges={(e['from_id'],e['to_id']):e for e in (view.get('continuation_graph') or {}).get('edges',[])}
    for tr in out['tracks']:
        tr['points'].sort(key=lambda p:p['t'])
        previous=None
        for p in tr['points']:
            links=[edges.get((a,b)) for a,b in zip(previous['candidate_ids'],p['candidate_ids'])] if previous else []
            p['connect_previous']=bool(previous and previous['linkable'] and p['linkable'] and p['t']>previous['t'] and all(links) and len(links)==2)
            p['suspect']=any(e and e['status']=='suspect' for e in links)
            previous=p
    out['status']='conditional_candidates' if out['tracks'] else 'unavailable_geometry'
    out['reason']+=' 未提供角误差时按±1°做敏感性假设（非统计置信度，峰宽sigma不当定位误差）；空心不稳定点未连线。'
    return out
