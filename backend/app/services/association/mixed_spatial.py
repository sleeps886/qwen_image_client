"""Selected-observation-only spatial references and model coordinate reprojection gate."""
import math
from backend.app.services.localization.spatial_tracks import solve_spatial
from backend.app.services.localization.forward_observation import GeoReference,observe
from backend.app.services.association.incremental_prediction import number
from backend.app.services.association.temporal_geometry import temporal_candidates
from backend.app.services.association.joint_identity import pair_evidence,paired_measurements


def joint_spatial_references(state):
    result={'coordinate_system':'local_xy_m','reference':'platform_initial_position','status':'insufficient_joint_identity',
            'candidates':[],'measurement_groups':{},'identity_gate_required':True,'unavailable_targets':[],
            'reason':'先完成跨阵目标假设及人工集合确认；不能在绘图阶段临时拼接单阵片段。'}
    for target_id,target in sorted(state.get('targets',{}).items()):
        tids=target.get('legacy_track_ids',[])
        if target.get('review_status')!='adopted' or not target.get('evidence',{}).get('eligible_for_spatial') or len(tids)!=2 or any(tid not in state['tracks'] for tid in tids):
            result['unavailable_targets'].append({'target_id':target_id,'reason':'目标尚未采用或仅有单阵证据'});continue
        left,right=(state['tracks'][tid]['history'] for tid in tids)
        evidence=pair_evidence(left,right,state.get('platform_config'),include_paths=True)
        if not evidence['eligible_for_spatial']:
            result['unavailable_targets'].append({'target_id':target_id,'reason':evidence['reason']});continue
        pairs=paired_measurements(left,right);support={p['t']:[] for p in pairs}
        result['measurement_groups'][target_id]=pairs
        for p in left+right:
            if p['t'] in support:support[p['t']].append(state['aliases'][p['candidate_id']])
        for path in evidence['paths']:
            result['candidates'].append({**{k:path[k] for k in ('branch','handedness','method','family','position_sigma_proxy_m','rmse_deg','uncertainty_calibrated')},'candidate_key':target_id+'|'+path['branch'],'id':target_id,
                'target_id':target_id,'source_track_ids':tids,'platform_id':left[0]['platform_id'],
                'points':[{k:round(v,3) if k in ('x','y') else v for k,v in p.items()} for p in path['points']],
                'measurement_reference':target_id,'association_confirmed':False,'collection_adopted':True,'unique_solution':False})
    if result['candidates']:result.update(status='joint_spatial_candidates',reason='已采用目标假设的多时刻留出检验空间候选；不是身份真值或唯一距离。')
    return result


def spatial_references(state):
    if state.get('joint_identity_version')==1:return joint_spatial_references(state)
    config=state.get('platform_config') or {};platforms=config.get('platforms',[])
    result={'coordinate_system':'local_xy_m','reference':'platform_initial_position','status':'insufficient_geometry','candidates':[],
            'reason':'需要同平台双阵至少两个实际同步观测，不能由方位凭空补距离。'}
    if not platforms:return result
    count=0
    for hid,h in state['tracks'].items():
        if not h['history'] or h['history'][0]['array']!='hull':continue
        pid=h['history'][0]['platform_id'];platform=next((p for p in platforms if p['name']==pid),None)
        if not platform:continue
        ref=GeoReference(*platform['start_ll'],6371000.)
        for tid,t in state['tracks'].items():
            if not t['history'] or t['history'][0]['array']!='tow' or t['history'][0]['platform_id']!=pid:continue
            hl={p['t']:p for p in h['history']};tl={p['t']:p for p in t['history']};times=sorted(hl.keys()&tl.keys())
            if len(times)<2:continue
            count+=1
            if count>64:raise ValueError('空间配对候选超过64组，请缩小已选观测范围')
            pairs=[{'t':time,'hull_deg':hl[time]['bearing_deg'],'tow_deg':tl[time]['bearing_deg'],
                    'hull_error_deg':max(.15,min(2.,hl[time].get('bearing_error_assumption_deg',1.))),
                    'tow_error_deg':max(.15,min(2.,tl[time].get('bearing_error_assumption_deg',1.)))} for time in times]
            for candidate in temporal_candidates(platform,pairs):
                pts=[{**p,'support_observation_ids':[state['aliases'][hl[p['t']]['candidate_id']],state['aliases'][tl[p['t']]['candidate_id']]]} for p in candidate['points']]
                result['candidates'].append({**candidate,'candidate_key':f'S{count:03d}|'+candidate['branch'],
                    'id':f'S{count:03d}','source_track_ids':[hid,tid],'platform_id':pid,'points':pts,'measurements':pairs,
                    'association_confirmed':False,'unique_solution':False})
            try:doc=solve_spatial({'coordinate_system':'dual_array_bearings','tracks':[{'id':f'S{count:03d}','platform_id':pid,'points':pairs}]},
                                  {'platforms':[platform],'T_MAX':max(times),'B_MIN':0,'B_MAX':180,'use_candidate_uncertainty':True})
            except ValueError:continue
            for track in doc['tracks']:
                pts=[]
                for p in track['points']:
                    x,y=ref.to_xy((p['y'],p['x']));time=p['t']
                    pts.append({'t':time,'x':x,'y':y,'support_observation_ids':[state['aliases'][hl[time]['candidate_id']],state['aliases'][tl[time]['candidate_id']]]})
                result['candidates'].append({'candidate_key':f'S{count:03d}|'+track['branch'],'id':f'S{count:03d}',
                    'branch':track['branch'],'source_track_ids':[hid,tid],'platform_id':pid,'handedness':track['handedness'],
                    'points':pts,'measurements':[p for p in pairs if p['t'] in {v['t'] for v in pts}],
                    'association_confirmed':False,'unique_solution':False})
    if result['candidates']:result.update(status='candidate_geometry',reason='以下是已选同步观测的几何可行分支，仅供模型判断；配对不是目标身份真值，镜像不算新增目标。')
    return result


def validate_spatial_result(raw,context):
    references=context.get('spatial_references',{});available={c['candidate_key']:c for c in references.get('candidates',[])}
    base={'coordinate_system':'local_xy_m','tracks':[],'unique_solution':False,'status':'unavailable','reason':references.get('reason','没有空间资料')}
    if raw is None:return {**base,'status':'not_returned','reason':'该回复只返回方位图，没有返回要求的空间轨迹；不能把过渡图当最终图。'}
    if not isinstance(raw,dict) or raw.get('coordinate_system')!='local_xy_m':return {**base,'status':'invalid','reason':'模型空间图必须使用local_xy_m坐标，不能把时间或角度当东／北坐标。'}
    tracks=raw.get('tracks',[])
    if not isinstance(tracks,list) or len(tracks)>64:return {**base,'status':'invalid','reason':'模型空间轨迹列表无效或超限'}
    errors=[];seen=set()
    for tr in tracks:
        try:
            key=tr.get('source_candidate_key');candidate=available.get(key)
            if not candidate or key in seen:raise ValueError('空间轨迹来源不存在或重复')
            seen.add(key)
            points=tr.get('points')
            if not isinstance(points,list) or not 2<=len(points)<=128:raise ValueError('空间轨迹需要2～128个可核对坐标点')
            platform=next(p for p in context['platform_config']['platforms'] if p['name']==candidate['platform_id'])
            source_measurements=candidate.get('measurements',references.get('measurement_groups',{}).get(candidate.get('measurement_reference'),[]))
            reference=GeoReference(*platform['start_ll'],6371000.);measures={p['t']:p for p in source_measurements}
            clean=[];previous=-1.;residuals=[]
            reference_points={p['t']:p for p in candidate['points']}
            for p in points:
                t,x,y=(number(p.get(k)) for k in ('t','x','y'))
                if t not in measures or t<=previous or math.hypot(x,y)>100000:raise ValueError('空间坐标没有实际同步测量支撑、时间倒序或范围越界')
                if references.get('identity_gate_required'):
                    original=reference_points[t];limit=max(150.,min(5000.,3*candidate['position_sigma_proxy_m']))
                    if math.dist((x,y),(original['x'],original['y']))>limit:raise ValueError('空间坐标偏离经多时刻验证的距离分支，角域回投不能单独放行任意距离')
                prediction=observe(platform,reference.to_ll((x,y)),t,reference);m=measures[t]
                residuals.extend([prediction['hull_deg']-m['hull_deg'],prediction['tow_deg']-m['tow_deg']])
                clean.append({'t':t,'x':x,'y':y});previous=t
            rmse=math.sqrt(sum(r*r for r in residuals)/len(residuals))
            if rmse>1.5 or max(map(abs,residuals))>3:raise ValueError('模型空间坐标未通过双阵方位回投门限')
            base['tracks'].append({'id':candidate['id'],'branch':candidate['branch'],'source_track_ids':candidate['source_track_ids'],
                                  'source_candidate_key':key,'points':clean,'rmse_deg':rmse,'candidate':True,
                                  **{k:candidate[k] for k in ('method','position_sigma_proxy_m','uncertainty_calibrated') if k in candidate}})
        except (ValueError,TypeError,KeyError,AttributeError,StopIteration) as exc:errors.append(str(exc))
    if base['tracks']:
        base.update(status='spatial_candidates',reason='模型输出且通过双阵回投的空间候选；保留镜像解释，分支数不是目标数。')
    else:base['reason']=raw.get('unavailable_reason') if isinstance(raw.get('unavailable_reason'),str) else base['reason']
    if errors and not base['tracks']:base['reason']='模型空间坐标未通过回投校验：'+'；'.join(errors[:3])
    base['validation_errors']=errors
    competitors=[]
    groups={tr['id']:set(tr['source_track_ids']) for tr in base['tracks']}
    ids=sorted(groups)
    for i,left in enumerate(ids):
        for right in ids[i+1:]:
            if groups[left]&groups[right]:competitors.append([left,right])
    base['competing_pairings']=competitors
    if competitors:base['reason']+=' 共享观测的配对存在竞争，不能重复计为独立目标。'
    base['unrepresented_source_tracks']=sorted(set(context['tracks'])-{tid for tr in base['tracks'] for tid in tr['source_track_ids']})
    chosen={tr['source_candidate_key'] for tr in base['tracks']}
    chosen_groups={tr['id'] for tr in base['tracks']}
    base['missing_alternatives']=[key for key,tr in available.items() if tr['id'] in chosen_groups and key not in chosen]
    if base['missing_alternatives']:
        base['reason']='模型仅返回部分空间候选，未覆盖全部镜像／算法解释；没有侧别证据，不能视为唯一轨迹。'
        if competitors:base['reason']+=' 共享观测配对仍有竞争。'
    base['coverage_complete']=not base['unrepresented_source_tracks'] and not base['missing_alternatives']
    return base
