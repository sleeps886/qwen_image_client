"""Explicit manual rerun preparation. Never calls a gateway or resets call counts."""
import copy
from datetime import datetime,timezone


def rerun_policy(state):
    status=state['status'];phase=None
    if status in ('invalid_response','call_unknown','association_call_unknown'):phase='association'
    elif status in ('plot_invalid_response','plot_call_unknown'):phase='plot'
    elif status=='ready' and state.get('plot_status')=='awaiting_association_recheck':phase='association'
    elif status=='ready' and state.get('plot_status')=='awaiting_spatial':phase='plot'
    elif status=='ready' and state.get('rounds') and state['rounds'][-1].get('review_status')=='rejected':phase='association'
    prepared=state.get('rerun_prepared')
    if prepared and prepared.get('calls_at_prepare')==state['calls'] and status=='ready':phase=None
    unknown=status in ('call_unknown','association_call_unknown','plot_call_unknown')
    exhausted=state['calls']>=state['max_calls']
    return {'available':bool(phase) and not exhausted,'phase':phase,'unknown_result':unknown,
            'limit_reached':exhausted,'can_reassociate':phase=='plot',
            'reason':'已达到模型调用上限；旧回复仍可免费本地复核。' if exhausted else
                     '上次调用结果未知，可能已计费；请核对服务记录并明确确认重复费用风险。' if unknown else
                     '手动重新准备不调用模型，发送时需要重新授权，旧结果和调用计数保留。'}


def prepare_manual_rerun(state,source_context=None,*,phase=None,acknowledge_unknown=False):
    from backend.app.services.association.mixed_prediction import prepare_mixed
    from backend.app.services.association.model_plot import prepare_model_plot
    policy=rerun_policy(state)
    if not policy['available']:raise ValueError(policy['reason'] if policy['limit_reached'] else '当前没有可重新运行的失败轮次，或已准备；执行中和待人工确认时不能重开')
    phase=phase or policy['phase']
    if phase!=policy['phase'] and not (phase=='association' and policy['can_reassociate']):raise ValueError('当前状态不能重新运行该阶段')
    if policy['unknown_result'] and not acknowledge_unknown:raise ValueError('上次结果未知，须明确确认可能重复计费的风险后才能重新准备')
    new=copy.deepcopy(state);new['status']='ready';new['version']+=1;new.pop('rerun_prepared',None)
    selection=[];recheck=False
    question='请重新判断本轮明确提交的观测，结合已有轨迹与复核意见核对归属；证据不足保留歧义，不按固定数量凑答案。'
    if phase=='association':
        if not isinstance(source_context,dict) or not isinstance(source_context.get('observations'),list):raise ValueError('没有可恢复的本轮观测快照；请保留任务并核对记录')
        reverse={v:k for k,v in state['aliases'].items()}
        for point in source_context['observations']:
            oid=point.get('observation_id')
            if not isinstance(oid,str) or oid not in reverse:raise ValueError('保存快照引用了未知观测')
            if reverse[oid] not in selection:selection.append(reverse[oid])
        if not selection:raise ValueError('本轮没有可恢复的真实观测点；未调用模型')
        recheck=not bool(set(selection)-set(state['submitted_ids'])) or source_context.get('revision_mode')=='historical_replay'
        new['plot_status']='awaiting_association'
        context,_=prepare_mixed(new,selection,question,recheck=recheck)
    else:
        new['plot_status']='awaiting_model';context,_=prepare_model_plot(new)
    prepared={'phase':phase,'selected_ids':selection,'recheck':recheck,'question':question,
              'calls_at_prepare':state['calls'],'from_version':state['version'],'from_status':state['status']}
    new['rerun_prepared']=prepared
    new.setdefault('rerun_history',[]).append({**prepared,'prepared_at':datetime.now(timezone.utc).isoformat(),
                                             'unknown_cost_acknowledged':bool(policy['unknown_result'] and acknowledge_unknown),
                                             'model_calls':0})
    return new,context,prepared
