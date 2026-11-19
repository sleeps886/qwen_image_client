"""Local structured evaluation. Never forward reference labels to models."""
import json
from backend.app.services.evaluation.observation_support import observation_segments
import math
import numpy as np


def number(value,label):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(label+'必须是有限数值')
    return float(value)


def assignment(cost):
    """Rectangular Hungarian algorithm; rows <= columns."""
    n,m=cost.shape
    u=np.zeros(n+1);v=np.zeros(m+1);p=np.zeros(m+1,dtype=int);way=np.zeros(m+1,dtype=int)
    for i in range(1,n+1):
        p[0]=i;j0=0;minv=np.full(m+1,np.inf);used=np.zeros(m+1,dtype=bool)
        while True:
            used[j0]=True;i0=p[j0];delta=np.inf;j1=0
            for j in range(1,m+1):
                if used[j]:continue
                cur=cost[i0-1,j-1]-u[i0]-v[j]
                if cur<minv[j]:minv[j]=cur;way[j]=j0
                if minv[j]<delta:delta=minv[j];j1=j
            for j in range(m+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minv[j]-=delta
            j0=j1
            if p[j0]==0:break
        while True:
            j1=way[j0];p[j0]=p[j1];j0=j1
            if j0==0:break
    return {p[j]-1:j-1 for j in range(1,m+1) if p[j]}


def evaluate(prediction,semantic_raw=None,truth_raw=None,gate_m=1000,max_gap_s=10):
    tracks=prediction.get('tracks')
    if not isinstance(tracks,list) or len(tracks)>100:raise ValueError('AI轨迹结构无效')
    predicted_ids={t['id'] for t in tracks}
    report={'kind':'structured_trajectory_evaluation','predicted_target_count':len(predicted_ids),
        'position_metrics':None,'reference_scene':None,'warnings':[
            '评测参考仅本地使用，未发送给模型；请人工确认所有文件属于同一样本。',
            '目标数量一致不代表目标识别正确；位置RMSE仅基于已匹配、同时间有数据的点。']}
    semantic=None;platform_id=prediction.get('coordinate_reference',{}).get('platform_id')
    refs={}
    if truth_raw:
        rows=[json.loads(line) for line in truth_raw.decode('utf-8-sig').splitlines() if line.strip()]
        if not 1<=len(rows)<=100000:raise ValueError('真值行数须为1～100000')
        platforms={r.get('platform_id') for r in rows}
        if len(platforms)!=1:raise ValueError('请上传单个平台的truth.jsonl')
        truth_platform=next(iter(platforms))
        if not isinstance(truth_platform,str):raise ValueError('真值缺少platform_id')
        if platform_id and platform_id!=truth_platform:raise ValueError('AI坐标参考平台与真值平台不一致')
        platform_id=truth_platform
        for row in rows:
            tid=row.get('target_id')
            if not isinstance(tid,str) or not tid or len(tid)>80:raise ValueError('真值目标编号无效')
            t=number(row.get('time_s'),'真值时间');lat=number(row.get('lat'),'真值纬度');lon=number(row.get('lon'),'真值经度')
            if t<0 or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('真值时间或经纬度越界')
            refs.setdefault(tid,[]).append((t,lat,lon))
        for points in refs.values():
            points.sort()
            if len(points)!=len({p[0] for p in points}):raise ValueError('同一真值目标存在重复时间')
    if semantic_raw:
        semantic=json.loads(semantic_raw)
        blocks=semantic.get('platforms') if isinstance(semantic,dict) else None
        if not isinstance(blocks,list) or not blocks:raise ValueError('semantic.json缺少platforms')
        selected=[p for p in blocks if p.get('platform_id')==platform_id] if platform_id else blocks
        if len(selected)!=1:raise ValueError('无法确定semantic中的参考平台，请提供同平台逐时刻真值或重新生成含坐标参考的AI结果')
        targets=selected[0].get('targets')
        if not isinstance(targets,list) or any(not isinstance(t,dict) or not isinstance(t.get('target_id'),str) for t in targets):raise ValueError('semantic目标清单无效')
        target_ids={t['target_id'] for t in targets}
        if len(target_ids)!=len(targets):raise ValueError('semantic目标编号重复')
        if refs and target_ids!=set(refs):raise ValueError('semantic与truth的目标清单不一致；请核对样本文件')
        report['reference_scene']=semantic.get('scenario_summary')
        report['scene_comparison_status']='仅展示场景参考：当前AI轨迹输出没有对应的六项场景字段，不能自动判为识别正确。'
        reference_count=len(target_ids)
    elif refs:reference_count=len(refs)
    else:raise ValueError('至少上传semantic.json或逐时刻truth.jsonl')
    report.update(reference_target_count=reference_count,
        target_count_absolute_error=abs(len(predicted_ids)-reference_count),
        target_count_correct=len(predicted_ids)==reference_count,platform_id=platform_id)
    if not refs:
        report['position_status']='缺少逐时刻truth.jsonl，仅核对目标数量与展示场景参考。'
        return report
    if prediction.get('coordinate_system')=='bearing_time':
        report['position_status']='AI仅输出方位—时间，未计算空间位置误差。'
        return report
    frame=prediction.get('coordinate_system')
    if frame not in ('lon_lat','local_xy_m'):raise ValueError('AI坐标系无效')
    origin=prediction.get('coordinate_reference')
    if frame=='local_xy_m' and not origin:
        report['position_status']='旧AI结果缺少坐标原点记录，请重新生成后评测；未猜测原点。'
        return report
    groups={}
    for track in tracks:
        groups.setdefault(track['id'],{}).setdefault(track.get('branch','main'),[]).extend(observation_segments(track))
    if any(len(branches)>1 for branches in groups.values()):
        report['position_status']='存在未消歧候选分支，需先确定分支；未按真值挑选最优答案。'
        return report
    if not groups:raise ValueError('无可评测AI轨迹')
    if len(refs)>12:raise ValueError('当前最多评测12个真值目标')
    radius=6371000.0
    predicted={}
    for tid,branches in groups.items():
        segments=[]
        for segment in next(iter(branches.values())):
            if not isinstance(segment,list) or not 2<=len(segment)<=2000:raise ValueError('AI轨迹点数无效')
            values=np.asarray([[number(p.get(k),'AI点') for k in ('t','x','y')] for p in segment])
            if np.any(np.diff(values[:,0])<=0):raise ValueError('AI轨迹时间必须递增')
            if frame=='local_xy_m':
                lat0=number(origin.get('origin_lat'),'原点纬度');lon0=number(origin.get('origin_lon'),'原点经度')
                if abs(lat0)>=89:raise ValueError('原点过近极点，不支持此局部投影')
                lat=lat0+np.degrees(values[:,2]/radius)
                lon=lon0+np.degrees(values[:,1]/(radius*math.cos(math.radians(lat0))))
            else:lon,lat=values[:,1],values[:,2]
            if np.any(abs(lat)>90) or np.any(abs(lon)>180):raise ValueError('AI经纬度越界')
            segments.append(np.column_stack([values[:,0],lat,lon]))
        # Do not interpolate across separately emitted segments or missing spans.
        ordered=sorted(segments,key=lambda s:s[0,0])
        if any(b[0,0]<=a[-1,0] for a,b in zip(ordered,ordered[1:])):raise ValueError('同一AI目标的分段时间重叠，请先整理航迹')
        predicted[tid]=ordered
    true_ids=sorted(refs);pred_ids=sorted(predicted);pairs={}
    for i,tid in enumerate(true_ids):
        truth=np.asarray(refs[tid]);times=truth[:,0]
        for j,pid in enumerate(pred_ids):
            errors=[]
            for segment in predicted[pid]:
                if np.any(np.abs(np.diff(segment[:,2]))>180):raise ValueError('暂不支持跨日期变更线的轨迹插值')
                in_range=(times>=segment[0,0])&(times<=segment[-1,0])
                part=truth[in_range]
                if not len(part):continue
                tt=part[:,0];st=segment[:,0]
                right=np.clip(np.searchsorted(st,tt,side='left'),0,len(st)-1)
                left=np.maximum(0,right-1)
                exact=np.abs(st[right]-tt)<=1e-6
                valid=exact|((right>0)&((st[right]-st[left])<=max_gap_s))
                part=part[valid];tt=tt[valid]
                lat=np.interp(tt,st,segment[:,1]);lon=np.interp(tt,st,segment[:,2])
                p1=np.radians(part[:,1]);p2=np.radians(lat)
                h=np.sin((p2-p1)/2)**2+np.cos(p1)*np.cos(p2)*np.sin(np.radians(lon-part[:,2])/2)**2
                errors.extend((2*radius*np.arcsin(np.sqrt(np.clip(h,0,1)))).tolist())
            if len(errors)>=2:
                error=np.asarray(errors);rmse=float(np.sqrt(np.mean(error**2)))
                pairs[i,j]=(rmse,error)
    unmatched_cost=(gate_m+1)*(len(true_ids)+1)
    costs=np.full((len(true_ids),len(pred_ids)+len(true_ids)),unmatched_cost)
    for (i,j),(rmse,_) in pairs.items():costs[i,j]=rmse if rmse<=gate_m else unmatched_cost*100
    matched=[];used_pred=set();used_true=set();all_errors=[]
    for i,j in assignment(costs).items():
        if j>=len(pred_ids) or (i,j) not in pairs or pairs[i,j][0]>gate_m:continue
        rmse,error=pairs[i,j];used_pred.add(pred_ids[j]);used_true.add(true_ids[i]);all_errors.extend(error.tolist())
        matched.append({'truth_id':true_ids[i],'predicted_id':pred_ids[j],'rmse_m':round(rmse,3),
            'mean_error_m':round(float(error.mean()),3),'p95_error_m':round(float(np.percentile(error,95)),3),
            'evaluated_points':len(error),'truth_points':len(refs[true_ids[i]]),
            'time_coverage_percent':round(len(error)/len(refs[true_ids[i]])*100,2)})
    error=np.asarray(all_errors)
    report['position_status']='completed' if matched else '未找到门限内且有至少2个共同时间点的匹配航迹'
    report['position_metrics']={'matched_targets':len(matched),'unmatched_truth_ids':sorted(set(true_ids)-used_true),
        'unmatched_predicted_ids':sorted(set(pred_ids)-used_pred),'matches':matched,
        'rmse_m':round(float(np.sqrt(np.mean(error**2))),3) if error.size else None,
        'mean_error_m':round(float(error.mean()),3) if error.size else None,
        'p95_error_m':round(float(np.percentile(error,95)),3) if error.size else None,
        'evaluated_points':len(all_errors),'reference_points':sum(map(len,refs.values())),
        'overall_time_coverage_percent':round(len(all_errors)/sum(map(len,refs.values()))*100,2),
        'matching_gate_m':gate_m,'max_interpolation_gap_s':max_gap_s,
        'matching_method':'共同时间位置RMSE门控，全局一对一最小代价匹配；不按ID直接对齐'}
    report['warnings']+=['仅在相邻AI采样间隔不超过设定值时做经纬度线性插值，不外推，不跨分段补线。',
        '位置误差未做平移/缩放对齐，保留绝对位置误差；聚合误差仅覆盖已匹配点，应同时查看漏检、未匹配项和覆盖率。']
    return report
