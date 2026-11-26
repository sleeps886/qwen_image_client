"""Pre-confirmation cross-array evidence and independent physical-target hypotheses.

No directory labels, expected count, reference path or future measurement is read.
Ray intersections alone never qualify a fragment pair for final spatial output.
"""
import copy
import math
import re
import statistics
from backend.app.services.association.temporal_geometry import temporal_candidates,evaluate_candidate
from backend.app.services.localization.forward_observation import GeoReference,observe


def fragment_id(legacy_id):return 'F'+legacy_id[1:] if legacy_id.startswith('T') else legacy_id


def paired_measurements(left,right):
    if not left or not right:return []
    if left[0]['platform_id']!=right[0]['platform_id'] or {left[0]['array'],right[0]['array']}!={'hull','tow'}:return []
    h,t=(left,right) if left[0]['array']=='hull' else (right,left)
    hl={p['t']:p for p in h};tl={p['t']:p for p in t}
    return [{'t':time,'hull_deg':hl[time]['bearing_deg'],'tow_deg':tl[time]['bearing_deg'],
             'hull_error_deg':max(.15,min(2.,hl[time].get('bearing_error_assumption_deg',1.))),
             'tow_error_deg':max(.15,min(2.,tl[time].get('bearing_error_assumption_deg',1.)))} for time in sorted(hl.keys()&tl.keys())]


def pair_evidence(left,right,config,*,include_paths=False):
    pairs=paired_measurements(left,right)
    base={'status':'insufficient_evidence','synchronous_times':len(pairs),'identity_confirmed':False,
          'eligible_for_spatial':False,'models':[],'reason':'需要至少5个实际同步时刻，才能留出观测核对跨阵配对；单次交会不是身份依据。'}
    platform=next((p for p in (config or {}).get('platforms',[]) if left and p['name']==left[0]['platform_id']),None)
    if len(pairs)<5 or not platform:return base
    # Fixed interior holdout: neither strongest-peak selection nor random refits.
    held=[pairs[i] for i in range(2,len(pairs)-1,4)] or [pairs[len(pairs)//2]]
    train=[p for p in pairs if p not in held]
    if len(train)<4:return base
    families=('cv','ca','ct');reference=GeoReference(*platform['start_ll'])
    fitted=temporal_candidates(platform,train,families=families)
    evaluated=[]
    for candidate in fitted:
        xy=evaluate_candidate(candidate,[p['t'] for p in held]);errors=[];normalized=[]
        for point,measurement in zip(xy,held):
            predicted=observe(platform,reference.to_ll(tuple(map(float,point))),measurement['t'],reference)
            for array in ('hull','tow'):
                error=predicted[array+'_deg']-measurement[array+'_deg']
                errors.append(error);normalized.append(error/measurement[array+'_error_deg'])
        rmse=math.sqrt(sum(v*v for v in errors)/len(errors));nrmse=math.sqrt(sum(v*v for v in normalized)/len(normalized))
        if rmse>1.5 or max(map(abs,errors))>3. or nrmse>3.:continue
        evaluated.append({**candidate,'heldout_rmse_deg':rmse,'heldout_normalized_rmse':nrmse,
                          'selection_score':nrmse+.15*candidate['parameter_count']})
    if not evaluated:
        return {**base,'status':'rejected_temporal_consistency','reason':'跨阵多时刻拟合、留出误差或距离可观测性不足；不因射线相交就合并目标。'}
    best=min(v['selection_score'] for v in evaluated)
    retained=[v for v in evaluated if v['selection_score']<=best+.12]
    models=[{k:v[k] for k in ('family','handedness','heldout_rmse_deg','heldout_normalized_rmse','position_sigma_proxy_m','selection_score')} for v in retained]
    result={**base,'status':'temporal_supported','eligible_for_spatial':True,'models':models,
            'heldout_times':[p['t'] for p in held],'reason':'通过选中观测的多时刻／留出检查，仍是配对候选，不是身份真值。'}
    if include_paths:
        full=temporal_candidates(platform,pairs,families=tuple(sorted({v['family'] for v in retained})))
        allowed={(v['family'],tuple(v['handedness'])) for v in retained}
        result['paths']=[v for v in full if (v['family'],tuple(v['handedness'])) in allowed]
        if not result['paths']:result.update(eligible_for_spatial=False,status='rejected_refit')
    return result


def prepare_identity_context(observations,tracklets,tracks,config):
    lookup={p['observation_id']:p for p in observations};fragments=[]
    for tid,tr in sorted(tracks.items()):
        if tr['history']:fragments.append({'reference_id':fragment_id(tid),'observation_ids':[p['observation_id'] for p in tr['history']],
                                           'history':tr['history'],'existing_fragment':True})
    seen={tuple(v['observation_ids']) for v in fragments}
    for row in tracklets:
        ids=tuple(row['observation_ids'])
        if ids in seen:continue
        seen.add(ids);fragments.append({'reference_id':row['reference_id'],'observation_ids':list(ids),
                                       'history':[lookup[oid] for oid in ids],'existing_fragment':False})
    candidates=[]
    for i,a in enumerate(fragments):
        for b in fragments[i+1:]:
            pairs=paired_measurements(a['history'],b['history'])
            if len(pairs)<2:continue
            candidates.append((statistics.median(abs(p['hull_deg']-p['tow_deg']) for p in pairs),a,b))
    # Diverse bounded shortlist: do not let one strong fragment occupy the budget.
    candidates.sort(key=lambda v:(v[0],v[1]['reference_id'],v[2]['reference_id']))
    chosen=[];uses={}
    for _,a,b in candidates:
        if max(uses.get(a['reference_id'],0),uses.get(b['reference_id'],0))>=3:continue
        chosen.append((a,b))
        for f in (a,b):uses[f['reference_id']]=uses.get(f['reference_id'],0)+1
        if len(chosen)>=12:break
    pairings=[]
    for a,b in chosen:
        pairings.append({'reference_id':f'P{len(pairings)+1:03d}','fragment_refs':[a['reference_id'],b['reference_id']],
                         **pair_evidence(a['history'],b['history'],config)})
    return {'fragments':[{k:v for k,v in f.items() if k!='history'} for f in fragments],
            'pairings':pairings,'shortlist_truncated':len(chosen)<len(candidates),'reference_only':True}


def validate_targets(state,context,result,tracks,mapping):
    rows=result.get('target_hypotheses',[])
    if not isinstance(rows,list) or len(rows)>64:raise ValueError('目标假设列表无效或超限')
    old=state.get('targets',{});next_id=state.get('next_target',1);targets={};warnings=[];occupied=set();seen=set()
    for row in rows:
        ref=row.get('target_ref');refs=row.get('fragment_refs');reason=row.get('reason')
        if isinstance(ref,str) and re.fullmatch(r'[HT]\d{1,12}',ref) and isinstance(refs,list) and len(refs)>2:
            raise ValueError(f'目标假设 {ref} 引用了{len(refs)}个片段，当前协议仅允许1至2个；请保留多个假设或待决关系，不能直接合并确认')
        if not isinstance(ref,str) or ref in seen or not isinstance(refs,list) or not 1<=len(refs)<=2 or not isinstance(reason,str) or not 0<len(reason)<=500:
            raise ValueError('目标假设编号、片段引用或依据无效')
        seen.add(ref)
        tids=[]
        removed=False
        for r in refs:
            if not isinstance(r,str):raise ValueError('目标片段引用无效')
            tid=mapping.get(r,'T'+r[1:] if r.startswith('F') else r)
            if tid not in tracks and r in mapping:
                removed=True;continue
            if tid not in tracks or tid in tids:raise ValueError('目标引用了不存在或重复的片段')
            tids.append(tid)
        if removed:
            warnings.append({'target_ref':ref,'code':'target_support_quarantined','reason':'目标所需片段已被数值校验隔离，保留其他有效目标，不恢复被隔离点'});continue
        if len(tids)==2:
            left,right=(tracks[tid]['history'] for tid in tids)
            if left[0]['platform_id']!=right[0]['platform_id'] or left[0]['array']==right[0]['array']:
                if context.get('revision_mode')=='scoped_replay':
                    warnings.append({'target_ref':ref,'fragment_refs':list(refs),'code':'invalid_cross_array_target',
                        'reason':'已拒绝错误双阵目标假设：两片段须同平台不同阵列；单阵片段保留，未自动合并、改接或确认目标身份'})
                    continue
                raise ValueError('双片段目标需同平台不同阵列')
        if occupied.intersection(tids):raise ValueError('同一片段不能确认为多个独立目标；应保留竞争而不是重复占用')
        occupied.update(tids)
        if ref in old:target_id=ref
        elif ref.startswith('H') and ref[1:].isdigit():target_id=f'T{next_id:03d}';next_id+=1
        else:raise ValueError('新物理目标假设须使用H临时编号，已有目标编号须存在')
        evidence=pair_evidence(*(tracks[tid]['history'] for tid in tids),state.get('platform_config')) if len(tids)==2 else {
            'status':'bearing_only','eligible_for_spatial':False,'reason':'单阵片段保留目标假设，不提供距离。'}
        if len(tids)==2:
            left,right=(tracks[tid]['history'] for tid in tids)
            pairing_ref=row.get('pairing_ref')
            if pairing_ref!='recompute':
                joint=context.get('joint_identity',{})
                pairing=next((p for p in joint.get('pairings',[]) if p['reference_id']==pairing_ref),None)
                fragments={f['reference_id']:set(f['observation_ids']) for f in joint.get('fragments',[])}
                actual=[{state['aliases'][p['candidate_id']] for p in tracks[tid]['history']} for tid in tids]
                expected=[fragments.get(ref,set()) for ref in pairing['fragment_refs']] if pairing else []
                if len(expected)!=2 or not (actual==expected or actual==list(reversed(expected))):
                    evidence={**evidence,'status':'pairing_reference_mismatch','eligible_for_spatial':False,
                              'reason':'跨阵候选编号未绑定或实际支持点与候选不一致；不能凭文字声称P编号通过来定位。'}
        targets[target_id]={'fragment_ids':[fragment_id(tid) for tid in tids],'legacy_track_ids':tids,
                            'pairing_ref':row.get('pairing_ref'),
                            'reason':reason,'status':'candidate' if evidence['eligible_for_spatial'] else 'needs_review',
                            'evidence':evidence,'identity_confirmed':False,'review_status':'pending'}
        if not evidence['eligible_for_spatial']:
            warnings.append({'target_ref':target_id,'code':'identity_evidence_insufficient','reason':evidence['reason']})
    # Preserve an untouched historical target, but never carry stale evidence
    # through replay or after any change in its constituent observations.
    for target_id,target in old.items():
        ids=target.get('legacy_track_ids',[])
        unchanged=all(tid in tracks and tracks[tid]['history']==state['tracks'].get(tid,{}).get('history') for tid in ids)
        if target_id not in targets and unchanged and not occupied.intersection(ids):targets[target_id]=copy.deepcopy(target)
    return targets,next_id,warnings
