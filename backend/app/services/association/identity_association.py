"""Conservative dual-ridge identity hypotheses, independent of model target IDs.

No geometry thresholds, target count prior, truth or external data are used.
Unique two-sided X continuations share identity; blended observations stay excluded.
"""
import copy

from backend.app.services.association.target_count import link_tracklets
from backend.app.services.association.crossing_tracks import crossing_continuity


def reconcile_identities(observations,evidence):
    fragments=link_tracklets(evidence)
    candidates={p['id']:(image,p) for image in evidence['images'] for p in image['candidates']}
    crossing=crossing_continuity(evidence,fragments)
    sections=crossing['sections'];ambiguous=set(crossing['ambiguous_ids'])
    groups={};rejected=[];before=sum(len(t['points']) for t in observations['tracks'])
    model_fingerprints={};input_ids=set()
    for source_index,track in enumerate(observations['tracks']):
        input_ids.add(track['id'])
        for point in track['points']:
            hid,tid=point['hull_candidate_id'],point['tow_candidate_id']
            why=None
            if hid in ambiguous or tid in ambiguous:why='ambiguous_crossing_or_competing_link'
            elif hid not in sections or tid not in sections:why='insufficient_continuous_ridge_support'
            if why:
                rejected.append({'model_id':track['id'],'t':point['t'],'hull_id':hid,'tow_id':tid,'reason':why});continue
            hi,hp=candidates[hid];ti,tp=candidates[tid]
            if hi['array']!='hull' or ti['array']!='tow' or hi['platform_id']!=ti['platform_id'] or hi['platform_id']!=track['platform_id']:
                raise ValueError('身份复核发现跨平台或跨阵列配对')
            key=(track['platform_id'],sections[hid],sections[tid])
            record={'point':copy.deepcopy(point),'model_id':track['id'],'model_branch':track.get('branch','main'),
                    'source_track_index':source_index,'sample_index':hp['sample_index']}
            groups.setdefault(key,[]).append(record)
            model_fingerprints.setdefault(track['id'],set()).add(key)
    output=[];identities=[];merged=0;discarded_short=0
    for key,records in sorted(groups.items(),key=lambda item:(min(r['point']['t'] for r in item[1]),item[0])):
        records.sort(key=lambda r:r['point']['t'])
        if len(records)<2:
            discarded_short+=len(records)
            for r in records:rejected.append({'model_id':r['model_id'],'t':r['point']['t'],
                'hull_id':r['point']['hull_candidate_id'],'tow_id':r['point']['tow_candidate_id'],'reason':'identity_has_only_one_synchronous_time'})
            continue
        if len({r['point']['t'] for r in records})!=len(records):raise ValueError('同一连续身份出现重复时刻，不能自动合并')
        identity=f'H{len(identities)+1}';aliases=sorted({r['model_id'] for r in records})
        output_start=len(output)
        identity_row={'id':identity,'model_ids':aliases,'hull_section':key[1],'tow_section':key[2],
                      'paired_observations':len(records),'branch_count':0,'identity_confirmed':False}
        segment=[];piece=0;last_row=None
        def flush_segment():
            nonlocal segment,piece
            if len(segment)>=2:
                output.append({'id':identity,'branch':f'continuous_section_{piece}','platform_id':key[0],
                               'points':segment,'model_target_ids':aliases})
                identity_row['branch_count']+=1
            elif segment:
                r=segment[0];rejected.append({'model_id':r['source_model_id'],'t':r['t'],
                    'hull_id':r['hull_candidate_id'],'tow_id':r['tow_candidate_id'],'reason':'unpaired_singleton_segment'})
            segment=[];piece+=1
        for record in records:
            if last_row is not None and record['sample_index']!=last_row+1:flush_segment()
            segment.append({**record['point'],'source_model_id':record['model_id'],'source_model_branch':record['model_branch']})
            last_row=record['sample_index']
        flush_segment()
        if identity_row['branch_count']:
            retained_aliases=sorted({p['source_model_id'] for t in output[output_start:] for p in t['points']})
            identity_row['model_ids']=retained_aliases
            identity_row['paired_observations']=sum(len(t['points']) for t in output[output_start:])
            for t in output[output_start:]:t['model_target_ids']=retained_aliases
            if len(retained_aliases)>1:merged+=len(retained_aliases)-1
            identities.append(identity_row)
    warnings=['程序以双阵持续亮带段维护H编号，H是身份假设而非已确认目标；不按模型名称或预设数量拼轨迹。',
              '仅双侧走向一致且解释唯一的X交叉关联为同一身份；交点不填补，其余竞争和断裂保留分段，不能据H数确认目标总数。']
    if rejected:warnings.append(f'身份复核隔离{len(rejected)}组不确定或孤立配对，不用于空间定位。')
    report={'method':'dual_continuous_ridge_sections_x_v2','crossing_analysis':crossing,'input_model_id_count':len(input_ids),
            'input_paired_observations':before,'retained_paired_observations':sum(len(t['points']) for t in output),
            'continuous_identity_count':len(identities),'merged_model_id_boundaries':merged,
            'model_ids_split_across_ridges':sorted(k for k,v in model_fingerprints.items() if len(v)>1),
            'identities':identities,'rejected_pairs':rejected,'rejected_pair_count':len(rejected),
            'unique_target_count_confirmed':False,'warnings':warnings}
    result={**copy.deepcopy(observations),'tracks':output,
            'warnings':observations.get('warnings',[])+warnings,'identity_association':{
                'method':report['method'],'continuous_identity_count':len(identities),'unique_target_count_confirmed':False}}
    return result,report,fragments
