"""Offline labels-only evaluator. Never imported by runtime inversion or agents.

Best-alternative errors are explicitly oracle-side lower bounds; the evaluator
does not choose a mirror in any stored or served inference result.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import numpy as np
from backend.app.services.evaluation.observation_support import observation_segments
from scipy.optimize import linear_sum_assignment


def evaluate(directory,truth_path,gate_m=1000.):
    directory=Path(directory);truth_path=Path(truth_path)
    if not math.isfinite(gate_m) or gate_m<=0:raise ValueError('评价门限必须为正数')
    paths=[directory/'tracks.json',truth_path]
    if any(p.stat().st_size>16*1024*1024 for p in paths):raise ValueError('评价文件超过16MiB限制')
    document=json.loads(paths[0].read_text(encoding='utf-8'));reference=defaultdict(list)
    if document.get('coordinate_system')!='lon_lat':raise ValueError('离线位置评价要求lon_lat数值轨迹')
    platform_ids={t.get('platform_id') for t in document['tracks']}
    for line in truth_path.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        p=json.loads(line)
        if p['platform_id'] not in platform_ids:continue
        values=[p[k] for k in ('time_s','lat','lon')]
        if not all(type(x) in (int,float) and math.isfinite(x) for x in values):raise ValueError('真值含无效数值')
        reference[p['target_id']].append(values)
    if not reference or len(reference)>12:raise ValueError('需有1～12个对应平台的真值目标')
    for key,rows in reference.items():reference[key]=np.array(sorted(rows))
    grouped=defaultdict(lambda:defaultdict(list))
    for track in document['tracks']:
        alternative=str(track.get('mirror_variant',track.get('handedness','unresolved')))
        for segment in observation_segments(track):grouped[track['id']][alternative].extend(segment)
    if len(grouped)>24:raise ValueError('候选身份超过离线评价预算')
    predictions=list(grouped);targets=list(reference);matrix=np.full((len(predictions),len(targets)),1e9);details={}
    for i,identity in enumerate(predictions):
        for j,target in enumerate(targets):
            rows=reference[target];variants=[]
            for alternative,points in grouped[identity].items():
                points=sorted({p['t']:p for p in points}.values(),key=lambda p:p['t'])
                supported=[p for p in points if rows[0,0]<=p['t']<=rows[-1,0]]
                if len(supported)<2:continue
                times=np.array([p['t'] for p in supported]);lat=np.interp(times,rows[:,0],rows[:,1]);lon=np.interp(times,rows[:,0],rows[:,2])
                north=6371000*np.radians(np.array([p['y'] for p in supported])-lat)
                east=6371000*np.cos(np.radians(lat))*np.radians(np.array([p['x'] for p in supported])-lon)
                rmse=float(np.sqrt(np.mean(north**2+east**2)))
                visible=max(1.,float(rows[-1,0]-rows[0,0]));max_gap=max(1.,visible/20)
                coverage=float(np.minimum(np.diff(times),max_gap).sum()/visible)
                variants.append({'alternative':alternative,'rmse_m':rmse,'supported_times':len(times),
                                 'visible_time_coverage_proxy':min(1.,coverage)})
            if variants:
                best=min(variants,key=lambda v:v['rmse_m']);matrix[i,j]=best['rmse_m']
                details[(i,j)]={'predicted_identity':identity,'reference_target':target,
                    'oracle_best_alternative_rmse_m':best['rmse_m'],
                    'oracle_best_alternative':best['alternative'],
                    'primary_alternative_rmse_m':variants[0]['rmse_m'],
                    'all_alternative_rmse_m':[v['rmse_m'] for v in variants],
                    'alternatives':variants,'oracle_side_given_for_evaluation_only':True}
    ii,jj=linear_sum_assignment(matrix);assignments=[details[(i,j)]|{'within_gate':bool(matrix[i,j]<=gate_m)} for i,j in zip(ii,jj) if (i,j) in details]
    return {'evaluation_only':True,'runtime_truth_used':False,'new_model_calls':0,
            'source_result_id':directory.name,'reference_target_count':len(targets),'predicted_identity_count':len(predictions),
            'target_count_absolute_error':abs(len(targets)-len(predictions)),
            'matched_targets_at_gate':sum(a['within_gate'] for a in assignments),'position_gate_m':gate_m,
            'matching_policy':'one_to_one_oracle_best_alternative_rmse','assignments':assignments,
            'all_mirrors_are_still_present_in_runtime_result':True,
            'notes':['最佳分支误差是假设已知正确侧别的评价下界，不代表系统已完成镜像消歧。',
                     '可见时间覆盖为有真值时间范围内的采样间隔代理，不是不可见全时段恢复。',
                     '只评价明确提供的标签；结果不会回写当前推断或选择生产镜像。']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result_directory');parser.add_argument('truth_jsonl');parser.add_argument('--gate-m',type=float,default=1000.)
    parser.add_argument('--output');args=parser.parse_args()
    report=evaluate(args.result_directory,args.truth_jsonl,args.gate_m)
    if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
