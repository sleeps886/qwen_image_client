"""Durable human interrupt: proposals may be reviewed, never automatically retried."""
import copy


def interrupt_for_review(before, proposal):
    proposal['_review_checkpoint']=copy.deepcopy(before)
    proposal['_review_resume_status']=proposal['status']
    proposal['status']='awaiting_review'
    proposal['rounds'][-1]['review_status']='pending'
    proposal['interrupt']={'node':'trajectory_review','message':'是否采用虚线候选？采用后定稿，或填写原因打回。'}
    return proposal


def review_proposal(state, version, adopt, reason=''):
    if state['version']!=version:raise ValueError('版本已变化，请重新加载，不能重复确认')
    if state.get('status')!='awaiting_review':raise ValueError('当前没有待确认的候选')
    if not adopt and not reason.strip():raise ValueError('打回时请填写原因')
    audit=copy.deepcopy(state.get('review_history',[]))
    audit.append({'proposal_version':version,'adopted':adopt,'reason':reason.strip(),
                  'round':copy.deepcopy(state['rounds'][-1])})
    if adopt:
        new=copy.deepcopy(state)
        new['status']=new.pop('_review_resume_status')
        new['rounds'][-1]['review_status']='adopted'
    else:
        new=copy.deepcopy(state['_review_checkpoint'])
        # Cloud cost and the monotonically increasing version must never roll back.
        new['calls']=state['calls']
    new.pop('_review_checkpoint',None);new.pop('interrupt',None)
    new['version']=version+1;new['review_history']=audit
    return new


def all_point_proposal(state):
    from backend.app.services.localization.incremental_spatial_plan import build_spatial_plan
    pool=state['points']+state.get('companion_points',[])
    times=sorted({p['t'] for p in pool})
    if len(times)>500:raise ValueError('全部点位试绘最多500个时刻，请拆分批次；未截断数据')
    primary=state['history'][0]['array'];groups=[];history=[];ambiguous=[];missing=[]
    for t in times:
        group={'t':t,'platform_id':state['history'][0]['platform_id'],
               **{array:[p for p in pool if p['t']==t and p['array']==array] for array in ('hull','tow')}}
        groups.append(group)
        if len(group[primary])==1:history.extend(group[primary])
        elif len(group[primary])>1:ambiguous.append(t)
        else:missing.append(t)
    if not history:raise ValueError('所有主阵时刻都有竞争峰，不能强行串成单目标')
    new=copy.deepcopy(state);new['history']=history
    context={'step_id':1,'base_state_version':state['version'],'visible_until':times[-1],
             'prediction_end':times[-1],'platform_id':history[0]['platform_id'],'array':primary,
             'selected_time_groups':groups,'history':history,'new_candidates':[],
             'baseline_prediction':[],'previous_prediction_evaluation':[]}
    new.update(version=state['version']+1,visible_until=times[-1],initialized=True,status='completed',prediction=[])
    new['rounds']=[{'input':context,'model_answer':{'reason':'全量离线试绘，不调用模型；多峰时刻不强行关联。',
        'decision':'end','accepted_candidate_ids':[],'prediction':[]},'offline_all_points':True,
        'excluded_ambiguous_times':ambiguous,'missing_primary_times':missing}]
    new['rounds'][-1]['spatial_plan']=build_spatial_plan(new)
    return interrupt_for_review(state,new)
