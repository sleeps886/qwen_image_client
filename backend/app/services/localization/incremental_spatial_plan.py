"""Combine an opened bearing forecast with platform geometry, retaining mirror branches.

No spatial truth, saved full-run target path, image, or unopened measurement is read.
"""
import math
import statistics

from backend.app.services.localization.forward_observation import GeoReference,array_location,fold_relative
from backend.app.services.localization.spatial_tracks import solve_spatial


def build_spatial_plan(state,round_index=-1):
    rounds=state.get('rounds',[])
    if not rounds:return {'status':'waiting','branches':[],'reason':'尚无已校验的方位预测'}
    index=round_index if round_index>=0 else len(rounds)-1
    current=rounds[index];cutoff=current['input']['visible_until'];platform_id=current['input']['platform_id']
    result={'status':'bearing_only','coordinate_system':'local_xy_m','coordinate_reference':'platform_initial_position',
        'platform_id':platform_id,'visible_until':cutoff,'identity_confirmed':False,'unique_solution':False,
        'branches':[],'direction_rays':[],'paired_observation_count':0,
        'forecast_method':'model_primary_bearing_plus_numeric_companion_projection',
        'warnings':['空间分支为候选假设，不是身份已确认的真实路线。',
            '角误差采用工程代理而非标定置信度；几何一致不等于真实空间位置正确。',
            '未来空间点为混合预测：主阵方位来自模型，另一阵来自近期观测数值外推；不是模型直接生成的空间坐标。']}
    config=state.get('platform_config')
    platform=next((p for p in (config or {}).get('platforms',[]) if p['name']==platform_id),None)
    if not platform:
        result['reason']='缺少合法平台位置与阵列几何，保留方位预测，不生成空间坐标。';return result
    reference=GeoReference(*platform['start_ll'],config.get('EARTH_R',6371000))
    # solve_spatial's local tangent projection uses the existing 6371000m convention.
    solver_reference=GeoReference(*platform['start_ll'],6371000)
    primary=current['input']['array'];companion='tow' if primary=='hull' else 'hull'
    # Rays carry only direction. Their display length is never interpreted as range.
    for p in current['model_answer']['prediction']:
        ll=array_location(platform,platform[primary+'_m'],p['t'],reference);xy=reference.to_xy(ll)
        for side in (-1,1):result['direction_rays'].append({'t':p['t'],'east_m':xy[0],'north_m':xy[1],
            'absolute_bearing_deg':(platform['heading_deg']+side*p['bearing_deg'])%360,'range_known':False})
    groups={}
    for round_ in rounds[:index+1]:
        for group in round_['input'].get('selected_time_groups',[]):
            if group['platform_id']==platform_id and group['t']<=cutoff:groups[group['t']]=group
    pairs=[];ambiguous=0
    for point in state['history']:
        if point['t']>cutoff:continue
        group=groups.get(point['t']);others=group.get(companion,[]) if group else []
        if len(others)!=1:
            ambiguous+=int(len(others)>1);continue
        other=others[0]
        if other['platform_id']!=platform_id or other['array']!=companion:continue
        pair={'t':point['t'],'source_candidate_ids':[point['candidate_id'],other['candidate_id']]}
        for p in (point,other):
            pair[p['array']+'_deg']=p['bearing_deg']
            pair[p['array']+'_error_deg']=max(.15,min(2,p.get('bearing_error_assumption_deg',1.)))
        pairs.append(pair)
    pairs=sorted(pairs,key=lambda p:p['t'])
    if len(pairs)>500:raise ValueError('空间规划超过500个配对时刻，请拆分批次')
    result.update(paired_observation_count=len(pairs),ambiguous_companion_times=ambiguous,lookback_limit=500)
    result['paired_observation_ids']=[{'t':p['t'],'candidate_ids':p['source_candidate_ids']} for p in pairs]
    if len(pairs)<2:
        result['reason']='同步双阵有效配对不足2个时刻，或另一阵存在多个竞争点；只显示平台与方位方向，不编造目标距离。';return result
    context={'platforms':[platform],'T_MAX':current['input']['prediction_end'],'B_MIN':0,'B_MAX':180,'use_candidate_uncertainty':True}
    def solve(points):
        try:
            cadence=statistics.median(sorted(groups)[i+1]-t for i,t in enumerate(sorted(groups)[:-1])) if len(groups)>1 else math.inf
            chunks=[[]]
            for point in points:
                if chunks[-1] and (point['t']-chunks[-1][-1]['t']>cadence*1.8 or any(chunks[-1][-1]['t']<t<point['t'] for t in groups)):
                    chunks.append([])
                chunks[-1].append(point)
            tracks=[{'id':'target_hypothesis','platform_id':platform_id,'points':chunk} for chunk in chunks if len(chunk)>=2]
            if not tracks:return []
            return solve_spatial({'coordinate_system':'dual_array_bearings','tracks':tracks},context)['tracks']
        except ValueError:return []
    observed=solve(pairs)
    if not observed:
        result['reason']='双阵交会未通过视差或角误差敏感性检查，不能可靠形成目标空间路线。';return result
    future=[]
    if len(pairs)>=3 and current['model_answer']['prediction']:
        recent=pairs[-4:];mt=statistics.mean(p['t'] for p in recent);mb=statistics.mean(p[companion+'_deg'] for p in recent)
        rate=sum((p['t']-mt)*(p[companion+'_deg']-mb) for p in recent)/sum((p['t']-mt)**2 for p in recent)
        for p in current['model_answer']['prediction']:
            future.append({'t':p['t'],primary+'_deg':p['bearing_deg'],companion+'_deg':fold_relative(mb+rate*(p['t']-mt)),
                primary+'_error_deg':pairs[-1][primary+'_error_deg'],companion+'_error_deg':max(p[companion+'_error_deg'] for p in recent)})
    projected=solve([pairs[-1],*future]) if future else []
    branches={}
    def add(tracks,key):
        for track in tracks:
            handedness=tuple(track['handedness']);branch=branches.setdefault(handedness,{
                'branch_id':f'h{handedness[0]:+}t{handedness[1]:+}','handedness':list(handedness),'observed_segments':[],'prediction_segments':[]})
            segment=[]
            for point in track['points']:
                if key=='prediction_segments' and point['t']<=cutoff:continue
                x,y=solver_reference.to_xy((point['y'],point['x']))
                segment.append({'t':point['t'],'x':x,'y':y,'sensitivity_m':point['sensitivity_m'],
                    'source_candidate_ids':([cid for p in pairs[-4:] for cid in p['source_candidate_ids']] if key=='prediction_segments' else next(p['source_candidate_ids'] for p in pairs if p['t']==point['t'])),
                    'position_source':'hybrid_prediction' if key=='prediction_segments' else 'dual_array_candidate_intersection'})
            if segment:branch[key].append(segment)
    add(observed,'observed_segments');add(projected,'prediction_segments')
    result['branches']=list(branches.values());has_prediction=any(b['prediction_segments'] for b in result['branches'])
    result['status']='spatial_prediction_candidates' if has_prediction else 'spatial_history_only'
    result['reason']='保留双阵几何与角误差检查通过的镜像分支；未来虚线为混合预测。' if has_prediction else '可显示部分历史空间候选，但未来交会不稳定或同步点不足，暂不输出未来空间路线。'
    result['warnings'].append('缺失/未通过检查的区间断线；射线只表示方向，不表示距离。')
    return result
