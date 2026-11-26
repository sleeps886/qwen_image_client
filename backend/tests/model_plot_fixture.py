"""Deterministic fake drawing output for tests only; production never calls this."""
def plot_answer(context):
    tracks=[]
    for tid,tr in context['tracks'].items():
        history=tr['history'];groups=[]
        for point in history:
            if not groups or point['t']-groups[-1][-1]['t']>tr['max_connected_gap_s']:groups.append([])
            groups[-1].append(point)
        segments=[{'observation_ids':[p['observation_id'] for p in group],
                   'curve':[{'t':p['t'],'bearing_deg':p['bearing_deg']} for p in group] if len(group)>1 else []} for group in groups]
        prediction=[];competing=any(tid in a['possible_tracks'] for a in context['ambiguities'])
        if len(history)>=2 and not competing and context['knowledge_cutoff']-history[-1]['t']<=context['prediction_end']-context['knowledge_cutoff']:
            a,b=history[-2:];slope=(b['bearing_deg']-a['bearing_deg'])/(b['t']-a['t']);t=context['prediction_end'];bearing=b['bearing_deg']+slope*(t-b['t'])
            if 0<=bearing<=180:prediction=[{'t':t,'bearing_deg':bearing}]
        tracks.append({'track_id':tid,'decision':'predict' if prediction else 'ambiguous' if competing else 'insufficient_evidence',
                       'reason':'假网关绘图判断，仅验证接口与前端，不验证真实模型精度','segments':segments,'prediction':prediction})
    return {'step_id':context['step_id'],'base_state_version':context['base_state_version'],'summary':'假网关已生成分段曲线与预测，非真实模型效果验收','tracks':tracks,'unassigned_assessment':[],
            'spatial_result':{'coordinate_system':'local_xy_m','tracks':[{'source_candidate_key':r['candidate_key'],'points':[{'t':p['t'],'x':p['x'],'y':p['y']} for p in r['points']]} for r in context.get('spatial_references',{}).get('candidates',[])],
                              'unavailable_reason':'假网关资料没有可定位的同步双阵几何，不补造空间距离'}}
