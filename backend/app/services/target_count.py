"""Image tracklets and target-count audit, separate from range observability.

Count bounds are evidence hypotheses, NOT truth. Fragment/multipath ambiguity
must remain visible; no knowledge of an expected number of targets is used.
"""
import json
import re
import numpy as np
from scipy.optimize import linear_sum_assignment
from backend.app.services.model_json import model_object


def link_tracklets(evidence):
    output=[]
    for image in evidence['images']:
        rows={}
        for point in image['candidates']:rows.setdefault(point['sample_index'],[]).append(point)
        tracks=[];ambiguous=set()
        for row,points in sorted(rows.items()):
            active=[t for t in tracks if 0<row-t[-1]['sample_index']<=2]
            assigned=set()
            if active and points:
                cost=np.full((len(active),len(points)),1e6)
                for i,track in enumerate(active):
                    last=track[-1];gap=row-last['sample_index'];slope=0
                    if len(track)>1:
                        slope=(last['bearing_deg']-track[-2]['bearing_deg'])/(last['sample_index']-track[-2]['sample_index'])
                    predicted=last['bearing_deg']+np.clip(slope,-8,8)*gap
                    for j,p in enumerate(points):
                        delta=abs(p['bearing_deg']-predicted)
                        if delta<=max(4,8*gap):cost[i,j]=delta
                ii,jj=linear_sum_assignment(cost)
                for i,j in zip(ii,jj):
                    if cost[i,j]<1e6:
                        alternatives=[cost[i,k] for k in range(len(points)) if k!=j]+[cost[k,j] for k in range(len(active)) if k!=i]
                        if cost[i,j]>4 or alternatives and min(alternatives)-cost[i,j]<1.:
                            ambiguous.update((active[i][-1]['id'],points[j]['id']))
                        active[i].append(points[j]);assigned.add(j)
            for j,p in enumerate(points):
                if j not in assigned:tracks.append([p])
        credible=[t for t in tracks if len(t)>=3 and t[-1]['sample_index']-t[0]['sample_index']>=2]
        for index,t in enumerate(credible):
            output.append({'id':f"L{image['image_index']}.{index}",'image_index':image['image_index'],
                'platform_id':image['platform_id'],'array':image['array'],'candidate_ids':[p['id'] for p in t],
                'rows':[p['sample_index'] for p in t],'support':len(t),
                'ambiguous_candidate_ids':[p['id'] for p in t if p['id'] in ambiguous],
                'ambiguity':'交叉、断段、杂波可能导致拆分或错误连接，不能直接算目标'})
    return output


def count_hypothesis(tracklets):
    per_image={}
    for t in tracklets:per_image.setdefault(t['image_index'],[]).append(t)
    lower=0;upper=0;details=[]
    for image,tracks in per_image.items():
        rows={r:sum(r in t['rows'] for t in tracks) for t in tracks for r in t['rows']}
        # Coexisting persistent traces are stronger evidence than total fragments.
        peak=max(rows.values(),default=0)
        lower=max(lower,peak);upper=max(upper,len(tracks))
        details.append({'image_index':image,'coexisting_tracklets':peak,'tracklet_fragments':len(tracks)})
    return {'min':lower,'max':max(lower,upper),'status':'unverified_image_hypothesis','per_image':details,
            'notes':['这是持续亮带数量线索，不是准确目标数；多径和碎片可能高估，弱目标漏检可能低估。',
                     '两阵数量不相加，空间镜像不计为新增目标。']}


def audit_count(answer,evidence,tracklets,hypothesis,observations,*,isolate_invalid_refs=False,isolate_shared_evidence=False):
    doc=model_object(answer,'candidate_pairs');inventory=doc.get('target_inventory',[])
    if not isinstance(inventory,list) or len(inventory)>50:raise ValueError('目标清单格式无效')
    lookup={p['id']:p for im in evidence['images'] for p in im['candidates']}
    identified=set(t['id'] for t in observations['tracks']);used={p[k] for t in observations['tracks'] for p in t['points'] for k in ('hull_candidate_id','tow_candidate_id')}
    paired_evidence={target:{p[k] for t in observations['tracks'] if t['id']==target for p in t['points']
                             for k in ('hull_candidate_id','tow_candidate_id')} for target in identified}
    claims={}
    for target,ids in paired_evidence.items():
        for cid in ids:claims.setdefault(cid,set()).add(target)
    for item in inventory:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise ValueError('目标清单ID无效')
        ids=item.get('evidence_ids',[])
        if not isinstance(ids,list):raise ValueError('目标清单必须有真实候选编号作为证据')
        for cid in ids:
            if isinstance(cid,str) and cid in lookup:claims.setdefault(cid,set()).add(item['id'])
    shared={cid for cid,owners in claims.items() if len(owners)>1}
    conflicts=[{'candidate_id':cid,'target_ids':sorted(claims[cid]),
                'policy':'excluded_from_all_quantity_evidence'} for cid in sorted(shared)]
    if shared and not isolate_shared_evidence:raise ValueError('同一候选不能重复作为不同目标的数量证据')
    unsupported=[];supported=set();supplemented=[];invalid_inventory_refs=[];clutter_reviews=[]
    inventory_ids=set()
    for item in inventory:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not 1<=len(item['id'])<=80:raise ValueError('目标清单ID无效')
        if item['id'] in inventory_ids:raise ValueError('目标清单存在重复ID')
        inventory_ids.add(item['id'])
        ids=item.get('evidence_ids',[])
        if not isinstance(ids,list) or len(ids)<2 or len(ids)>1000:raise ValueError('目标清单必须有真实候选编号作为证据')
        if any(not isinstance(i,str) or i not in lookup for i in ids):
            if not isolate_invalid_refs:raise ValueError('目标清单必须有真实候选编号作为证据')
            invalid_inventory_refs.append({'model_id':item['id'],'invalid_ids':[i[:80] if isinstance(i,str) else None for i in ids if not isinstance(i,str) or i not in lookup]})
            ids=[i for i in ids if isinstance(i,str) and i in lookup]
        expanded=set(ids)|paired_evidence.get(item['id'],set())
        item['excluded_shared_evidence_ids']=sorted(expanded&shared)
        expanded-=shared
        ids=[cid for cid in ids if cid not in shared]
        if len(set(lookup[i]['t'] for i in expanded))<2:
            unsupported.append(item['id'])
            item['evidence_support']='single_time_unconfirmed'
        else:
            supported.add(item['id']);item['evidence_support']='multiple_times'
            if len(set(lookup[i]['t'] for i in ids))<2:
                supplemented.append(item['id']);item['evidence_support']='existing_validated_pairs_same_id'
        item['validated_evidence_ids']=sorted(expanded)
        if item.get('status') not in ('associated','unpaired','ambiguous','clutter'):raise ValueError('目标清单状态无效')
        if not isinstance(item.get('reason',''),str) or len(item.get('reason',''))>1000:raise ValueError('目标清单原因过长')
        # There is no independently validated clutter classifier in this pipeline.
        # A model's label/reason alone cannot remove a cross-time candidate from
        # the inventory. Do not turn the retained hypothesis into a paired target.
        item['model_status']=item['status']
        if item['status']=='clutter':
            item['status']='ambiguous'
            item['classification_review']='unverified_model_clutter'
            clutter_reviews.append({'id':item['id'],'model_status':'clutter','review_status':'ambiguous',
                                    'model_reason':item.get('reason',''),
                                    'cross_time_evidence':item['id'] in supported,
                                    'validated_evidence_ids':sorted(expanded)})
    uncovered=[]
    for t in tracklets:
        used_fraction=len(set(t['candidate_ids'])&used)/len(t['candidate_ids'])
        if used_fraction<.5:uncovered.append({'id':t['id'],'array':t['array'],'support':t['support'],
                                           'paired_fraction':round(used_fraction,3)})
    suspected={x['id'] for x in inventory if x['status']!='clutter'}|identified
    warnings=[]
    if conflicts:
        warnings.append(f'有{len(conflicts)}个候选被多个ID共享，已从所有相关ID的独立数量证据中排除；不选择先出现者，不修改合法轨迹配对，数量与身份仍待复核。')
    if clutter_reviews:
        warnings.append('以下模型杂波判定未经独立验证，已保留为待复核候选，而非排除或确认为目标：'+', '.join(x['id'] for x in clutter_reviews))
    invalid_pairs=(observations.get('candidate_validation') or {}).get('rejected_pair_count',0)
    if invalid_pairs:warnings.append(f'已隔离{invalid_pairs}组无效或证据不足的模型配对；当前为部分观测，不代表完整识别。')
    if invalid_inventory_refs:warnings.append('目标清单包含不存在的候选引用；已排除这些证据，未猜测替代编号。')
    if unsupported:warnings.append('清单中以下ID在排除无效或共享证据后不足两个独立时刻，保持未确认，不阻断其他有效轨迹定位：'+', '.join(unsupported))
    identity_conflicts=[]
    for fragment in tracklets:
        owners=[target for target,ids in paired_evidence.items() if len(ids&set(fragment['candidate_ids']))>=2]
        if len(owners)>1:identity_conflicts.append({'tracklet':fragment['id'],'target_ids':sorted(owners)})
    if identity_conflicts:warnings.append('同一持续亮带片段分配到了多个目标ID，可能是身份断裂或交叉关联错误；不能据ID个数确认目标数。')
    if len(identified)<hypothesis['min']:warnings.append('模型关联数量少于持续亮带数量线索，需要复核漏检、交叉配对或杂波。')
    if uncovered:warnings.append(f'有{len(uncovered)}条持续亮带片段未获得充分双阵配对，不能宣称完整恢复。')
    if not inventory:warnings.append('模型未返回独立目标清单，当前数量仅来自已配对目标ID。')
    return {'status':'needs_review' if warnings else 'consistent_but_unverified',
            'image_hypothesis':hypothesis,'associated_target_count':len(identified),
            'inventory_target_count':len(suspected),'inventory_available':bool(inventory),
            'supported_inventory_target_count':len({x['id'] for x in inventory if x['status']!='clutter'}&supported),
            'unconfirmed_inventory_ids':unsupported,'supplemented_from_existing_pairs':supplemented,
            'invalid_inventory_references':invalid_inventory_refs,
            'shared_inventory_evidence':conflicts,
            'clutter_classification_reviews':clutter_reviews,
            'candidate_validation':observations.get('candidate_validation'),
            'identity_conflicts':identity_conflicts,
            'targets':[{'id':target,'paired_observations':sum(len(t['points']) for t in observations['tracks'] if t['id']==target)} for target in sorted(suspected)],
            'inventory':inventory,'uncovered_tracklets':uncovered,'warnings':warnings,
            'localized_target_count':None,'unique_target_count_confirmed':False}


def finalize_count(report,document,identity_report=None):
    result={**report,'targets':[dict(t) for t in report['targets']]}
    if identity_report is not None:
        result['model_targets']=result['targets']
        result['identity_association']=identity_report
        result['continuous_identity_count']=identity_report['continuous_identity_count']
        result['targets']=[{'id':i['id'],'model_ids':i['model_ids'],'paired_observations':i['paired_observations']}
                           for i in identity_report['identities']]
        result['unpaired_inventory_ids']=[t['id'] for t in result['model_targets'] if not t['paired_observations']]
        result['warnings']=[*result['warnings'],*identity_report['warnings']]
        result['warnings'].append('模型ID计数和原身份冲突为修正前记录；空间图使用程序H身份假设，映射见身份复核报告，不代表真实目标数。')
        result['status']='needs_review'
    localized={t['id'] for t in (document or {}).get('tracks',[])}
    result['localized_target_count']=len(localized)
    for target in result['targets']:
        target['spatial_status']='candidate_localized' if target['id'] in localized else 'not_localized'
        if target['id'] not in localized:
            target['reason']='尚无可靠双阵同步配对' if not target['paired_observations'] else '没有通过最终几何、连续性与重投影质检的空间段'
    result['unlocalized_ids']=[t['id'] for t in result['targets'] if t['id'] not in localized]
    if result['unlocalized_ids']:
        result['status']='needs_review'
        result['warnings']=[*result['warnings'],'部分已识别目标未能稳定定位；不能用空间分支数量替代目标数量。']
    return result
