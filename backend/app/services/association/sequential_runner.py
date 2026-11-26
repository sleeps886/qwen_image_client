"""Durable sequential association. Future queue facts never enter a step frame.

Working proposals are NOT user confirmations. Model credentials are never stored.
All mutation is serialized by the caller's existing prediction lock, except pause
and streamed text, which use CONTROL. No model retry is performed here.
"""
import asyncio
import copy
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from backend.app.services.association.mixed_prediction import prepare_mixed,validate_mixed,update_mixed,QUALITY
from backend.app.services.association import tentative_continuation as tentative
from backend.app.services.association.sequential_spatial_preview import spatial_preview

CONTROL=threading.RLock()
ACTIVE=set()
PROTOCOL='sequential-prefix-v1'


@contextmanager
def connection(path):
    db=sqlite3.connect(Path(path).parent/'observations.sqlite3',timeout=15)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS sequential_runs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
            yield db
    finally:db.close()


def read_run(path,rid):
    if not re.fullmatch(r'[a-f0-9]{32}',rid):raise ValueError('逐组运行编号无效')
    with connection(path) as db:row=db.execute('SELECT payload FROM sequential_runs WHERE id=?',(rid,)).fetchone()
    if not row:raise ValueError('逐组运行不存在')
    return json.loads(row[0])


def write_run(path,run):
    with connection(path) as db:db.execute('INSERT INTO sequential_runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
        (run['id'],run['request_key'],json.dumps(run,ensure_ascii=False,allow_nan=False)))


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def preview(state,units,order='event_time',call_limit=20,model_fingerprint=None,model_label=None,question='',target_binding=None,revision_window_units=3,continuation_policy=tentative.POLICY,review_point_limit=80,long_gap_seconds=180.):
    if state['status']!='ready':raise ValueError('当前会话有未结束的操作，请先确认或处理')
    if order not in ('event_time','received'):raise ValueError('队列顺序无效')
    if type(call_limit) is not int or call_limit<0:raise ValueError('调用预算无效')
    if type(revision_window_units) is not int or not 0<=revision_window_units<=50:raise ValueError('暂定历史复核窗口无效')
    if not isinstance(question,str) or len(question)>2000:raise ValueError('问题过长或格式无效')
    if target_binding is not None:
        if not isinstance(target_binding,dict) or target_binding.get('entity_type')!='physical_target' or target_binding.get('revision')!=state['version']:raise ValueError('目标绑定类型或版本无效')
        target=state.get('targets',{}).get(target_binding.get('target_id'))
        if not target or target.get('review_status')!='adopted':raise ValueError('只能绑定当前版本已由用户确认的物理目标；片段编号不能冒充目标')
    lookup={p['candidate_id']:p for p in state['points']};seen=set();queue=[]
    if not isinstance(units,list) or not units:raise ValueError('请明确选择输入单元')
    for index,ids in enumerate(units):
        if not isinstance(ids,list) or not 1<=len(ids)<=2 or any(not isinstance(cid,str) or cid not in lookup for cid in ids):raise ValueError('每组须含1至2条已有观测')
        if len(set(ids))!=len(ids) or seen.intersection(ids):raise ValueError('输入单元重复占用观测')
        seen.update(ids);rows=[lookup[cid] for cid in ids]
        if len({(p['platform_id'],p['t']) for p in rows})!=1 or len({p['array'] for p in rows})!=len(rows):raise ValueError('双阵单元须同平台同时间且各阵最多一条')
        if set(ids)<=set(state['submitted_ids']):continue
        queue.append({'unit_id':f'U{index+1:06d}','candidate_ids':ids,'t':rows[0]['t'],'platform_id':rows[0]['platform_id'],'received_index':index,
                      'pairing_status':'provided_unverified' if len(rows)==2 else 'incomplete'})
    if not queue:raise ValueError('所选输入单元已处理，没有新增观测')
    # Opaque tie breaker, not candidate-ID/source-prefix lexicographic ordering.
    # Real arrival order remains explicitly available through order='received'.
    if order=='event_time':queue.sort(key=lambda u:(u['t'],digest(sorted(u['candidate_ids']))))
    free_seed=not state['submitted_ids'] and not state.get('unassigned_ids') and not state.get('ambiguities') and not any(t.get('history') for t in state['tracks'].values())
    upper=max(0,len(queue)-int(free_seed))
    if upper>call_limit or upper>state['max_calls']-state['calls']:raise ValueError(f'本队列最多需要{upper}次关联调用，超过授权预算或会话剩余调用数；未调用模型')
    spec={'protocol':PROTOCOL,'base_version':state['version'],'queue':queue,'order':order,'call_limit':call_limit,'model_call_upper_bound':upper,'free_initial_seed':free_seed,'visibility_policy':'processed_prefix_only','model_fingerprint':model_fingerprint,'model_label':model_label,'question':question,'target_binding':target_binding,'revision_window_units':revision_window_units}
    if continuation_policy not in (tentative.POLICY,'legacy-fixed-window'):raise ValueError('接续策略无效')
    if type(review_point_limit) is not int or not 1<=review_point_limit<=500:raise ValueError('局部复核点数上限无效')
    if isinstance(long_gap_seconds,bool) or not isinstance(long_gap_seconds,(int,float)) or not 1<=long_gap_seconds<=86400:raise ValueError('缺口提示阈值无效')
    spec.update(continuation_policy=continuation_policy,review_limits={**tentative.DEFAULTS,'max_review_points':review_point_limit,'long_gap_seconds':long_gap_seconds})
    return {**spec,'context_digest':digest(spec),'model_calls':0}


def create_run(path,state,spec,request_key):
    if not isinstance(request_key,str) or not 1<=len(request_key)<=100:raise ValueError('幂等请求编号无效')
    with CONTROL:
        with connection(path) as db:old=db.execute('SELECT payload FROM sequential_runs WHERE request_key=?',(request_key,)).fetchone()
        if old:
            existing=json.loads(old[0])
            if existing['spec']!=spec:raise ValueError('幂等编号已绑定另一队列')
            return existing
        if state['status']!='ready' or state['version']!=spec['base_version']:raise ValueError('会话版本或状态已变化')
        clean=copy.deepcopy(spec);clean.pop('context_digest',None);clean.pop('model_calls',None)
        if spec['context_digest']!=digest(clean):raise ValueError('队列快照摘要无效')
        run={'id':uuid.uuid4().hex,'request_key':request_key,'spec':spec,'status':'prepared','cursor':0,'calls':0,'steps':[],
             'working':copy.deepcopy(state),'baseline_submitted_ids':copy.deepcopy(state['submitted_ids']),'root_version':state['version']+1,'pause_requested':False,'model_reference':None}
        run['baseline_units']=copy.deepcopy(state.get('sequential_units',[]))
        if not run['baseline_units'] and state.get('last_sequential_run'):
            try:
                previous=read_run(path,state['last_sequential_run'])
                if previous['status']=='adopted':run['baseline_units']=copy.deepcopy(previous.get('baseline_units',[])+previous['spec']['queue'][:previous['cursor']])
            except ValueError:pass  # Legacy histories can still be displayed as individual observations.
        write_run(path,run);return run


def frame_for_step(run):
    index=run['cursor'];unit=run['spec']['queue'][index];work=copy.deepcopy(run['working'])
    allowed=set(work['submitted_ids'])|set(unit['candidate_ids'])|set(work.get('unassigned_ids',[]))|{a['candidate_id'] for a in work.get('ambiguities',[])}|{p['candidate_id'] for t in work['tracks'].values() for p in t['history']}
    # This explicit allow-list also works when all imported units share one receipt.
    work['points']=[p for p in work['points'] if p['candidate_id'] in allowed]
    work['aliases']={cid:oid for cid,oid in work['aliases'].items() if cid in allowed}
    work['browse_catalog']=[];work.pop('active_sequential_run',None);work['status']='ready'
    window=run['spec'].get('revision_window_units',3) if not run['spec'].get('target_binding') else 0
    revisited=run['spec']['queue'][max(0,index-window):index]
    ids=list(dict.fromkeys([*unit['candidate_ids'],*(cid for u in revisited for cid in u['candidate_ids'])]))
    local_policy=run['spec'].get('continuation_policy')==tentative.POLICY
    if local_policy:
        binding=run['spec'].get('target_binding')
        permitted=set(work['targets'][binding['target_id']]['legacy_track_ids']) if binding else None
        context,prompt,review=tentative.prepare(work,unit['candidate_ids'],run['spec']['review_limits'],
            lambda s,chosen:prepare_mixed(s,chosen,question=run['spec'].get('question','')),permitted)
        revisited=[u for u in run['spec']['queue'][:index] if set(u['candidate_ids'])&set(review['editable_ids'])]
    else:context,prompt=prepare_mixed(work,ids,question=run['spec'].get('question',''),recheck=bool(revisited))
    lookup={p['candidate_id']:p for p in work['points']}
    context['current_unit_observations']=[{'observation_id':work['aliases'][cid],**{k:lookup[cid][k] for k in ('t','platform_id','array','bearing_deg',*QUALITY) if k in lookup[cid]}} for cid in unit['candidate_ids']]
    streams={}
    for point in context['observations']:
        streams.setdefault((point['platform_id'],point['array']),[]).append(point['observation_id'])
    context['fragment_stream_groups']=[{'platform_id':platform,'array':array,'observation_ids':oids}
                                     for (platform,array),oids in sorted(streams.items())]
    baseline={work['aliases'][cid] for cid in run.get('baseline_submitted_ids',[]) if cid in work['aliases']}
    context['sequential_step']={'protocol':PROTOCOL,'step_index':index+1,
        'unit_observation_ids':[work['aliases'][cid] for cid in unit['candidate_ids']],
        'new_observation_ids':[work['aliases'][cid] for cid in unit['candidate_ids'] if cid not in work['submitted_ids']],
        'new_unit_count':0 if unit.get('review_only') else 1,'processed_unit_count':index,'visibility_policy':'processed_prefix_only','working_results_are_provisional':True,
        'target_binding':run['spec'].get('target_binding'),'revisited_provisional_units':len(revisited),
        'pairing_status':unit.get('pairing_status','provided_unverified'),
        'confirmed_history_revisit_count':sum(p['observation_id'] in baseline for p in context['observations'])}
    instructions=('逐组输入的一组hull＋tow只是到达容器，不是一条单阵片段，也不是已经确认的同目标配对。'
                  'tracks每项只能引用fragment_stream_groups中同平台同阵列的点；hull与tow必须分别提案，不能合放N1。'
                  '跨阵身份仅通过target_hypotheses及既有几何校验表达；证据不足保留单阵暂定片段或unassigned，不要求每步建立物理目标。')
    if local_policy:
        instructions+=('采用先暂接再局部修订策略：无明显矛盾时，稀疏点优先低置信接续已有F片段，不因跨阵身份条件不足而放弃单阵接续。'
                       '有多个候选或明显矛盾时不硬接；只允许修改editable_observation_ids及当前组。其余tracks历史只读。'
                       '用已有F编号接续以保持稳定；拆分可用N编号。允许局部撤回、改接或未归属，不能越权重建只读轨迹。'
                       'continuation_policy中的候选和门限是辅助证据，不是目标身份答案；仍须你结合两阵和全部已提供历史判断。')
        instructions+=('若只读历史也需修改，返回review_requests:[{"observation_ids":["原样复制历史O编号"],"reason":"依据"}]，本步不得越权改写。'
                       '请求将在下一组调用中复核；队列结束仍有请求须另行免费准备并授权，不会自动新增调用。')
        instructions+=('对本步疑点可返回issue_resolutions:[{"issue_id":"D1","decision":"retain或revised或unresolved","reason":"保持/已修订/待决的证据"}]。'
                       '省略处理结果的疑点仍保留警告；retain只确认工作暂接可保留，不是物理目标身份确认。')
        instructions+=('优先用point_indices或observation_ids明确引用本步观测。candidate_ref只能原样复制multi_time_references中实际提供的reference_id；'
                       '候选列表为空时禁止使用candidate_ref，不能根据观测行号自行编造R005等编号。')
        instructions+=('同阵前后接续应在tracks中接入已有F片段，或在授权范围内明确修订同一条片段；'
                       '不能把两个hull片段或两个tow片段放入一个target_hypotheses来表示接续。双片段目标只能同平台一条hull加一条tow。')
    prompt=prompt.split('\n',1)[0]+instructions+'\n'+json.dumps(context,ensure_ascii=False,separators=(',',':'))
    return work,context,prompt


def apply_returned(run,frame,context,answer,usage=None,source='model'):
    checked=validate_mixed(frame,context,answer)
    if context.get('revision_mode')=='scoped_replay':tentative.check_scope(frame,context,checked)
    binding=run['spec'].get('target_binding');allowed=None
    if binding:
        tid=binding['target_id'];target=checked['targets'].get(tid)
        if not target:raise ValueError('绑定目标丢失，不能自动换目标或当新目标')
        allowed=set(frame['targets'][tid]['legacy_track_ids'])|set(target['legacy_track_ids'])
        if any(key in frame['tracks'] and key not in frame['targets'][tid]['legacy_track_ids'] for key in target['legacy_track_ids']):raise ValueError('绑定模式不能借用其他已有片段')
        for key in set(frame['tracks'])|set(checked['tracks']):
            if key not in allowed and frame['tracks'].get(key)!=checked['tracks'].get(key):raise ValueError('绑定模式不能修改其他目标或偷偷接入其他轨迹')
        for key in set(frame['targets'])|set(checked['targets']):
            if key!=tid and frame['targets'].get(key)!=checked['targets'].get(key):raise ValueError('绑定模式不能创建或修改其他物理目标')
    updated=update_mixed(frame,context,checked,usage)
    if allowed is not None:
        for key,track in frame['tracks'].items():
            if key not in allowed:
                updated['tracks'][key]=copy.deepcopy(track);updated['rounds'][-1]['tracks'][key]=copy.deepcopy(track)
    updated['status']='ready';updated.pop('_checkpoint',None)
    updated['rounds'][-1].update(review_status='provisional',sequential_run_id=run['id'],sequential_source=source)
    if context.get('revision_mode')=='scoped_replay':
        graph,revision=tentative.revision(frame,updated,context,updated['version'],checked['model_answer'])
        updated['continuation_graph']=graph
        updated['continuation_revisions']=copy.deepcopy(frame.get('continuation_revisions') or [])+[revision]
        updated['pending_review_requests']=tentative.review_requests(checked['model_answer'],frame,context,run['spec']['review_limits']['max_review_points'])
        run['steps'][-1]['revision']=revision
    # Full storage remains available internally; only frame_for_step exposes facts.
    for key in ('points','aliases','browse_catalog','source_labels'):updated[key]=copy.deepcopy(run['working'][key])
    run['working']=updated;run['cursor']+=1
    run['steps'][-1].update(status='validated',warnings=checked['warnings'],answer=answer,usage=usage,source=source)
    if 'error' in run['steps'][-1]:
        run['steps'][-1]['previous_validation_error']=run['steps'][-1].pop('error')
    run['status']='completed_awaiting_review' if run['cursor']==len(run['spec']['queue']) else 'paused'


def model_reference(settings):return digest([settings.model,settings.base_url,settings.api_format])


def execute_one(path,rid,expected_cursor,settings,gateway,read_state,save_state):
    """Caller holds prediction lock and runtime slot across this single call."""
    with CONTROL:
        run=read_run(path,rid);state=read_state(path)
        if state.get('active_sequential_run')!=rid or state['version']!=run['root_version']:raise ValueError('运行绑定的会话版本已变化')
        if run['cursor']!=expected_cursor or run['status'] not in ('prepared','paused'):raise ValueError('步骤已执行或运行不可继续；未重复调用')
        if run['pause_requested']:raise ValueError('已请求暂停，本步未启动')
        reference=model_reference(settings)
        if run['spec'].get('model_fingerprint') and run['spec']['model_fingerprint']!=reference:raise ValueError('模型配置与准备授权不一致，未调用模型')
        if run['model_reference'] and run['model_reference']!=reference:raise ValueError('模型配置已改变，请勿沿用旧队列授权')
        frame,context,prompt=frame_for_step(run)
        local=run['cursor']==0 and run['spec']['free_initial_seed']
        if not local:
            if not settings.api_key:raise ValueError('请先配置模型API Key')
            if run['calls']>=run['spec']['call_limit'] or state['calls']>=state['max_calls']:raise ValueError('调用预算不足')
            run['model_reference']=reference;run['calls']+=1;state['calls']+=1
        run['status']='running'
        run['steps'].append({'index':expected_cursor+1,'unit_id':run['spec']['queue'][expected_cursor]['unit_id'],'status':'running',
            'context':context,'streamed_body':'','source':'local_seed' if local else 'model'})
        write_run(path,run);state['status']='sequential_running';save_state(path,state)
    response=None
    try:
        if local:
            response={'answer':json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],'tracks':[],'unassigned':[p['observation_id'] for p in context['observations']]})}
            if context.get('revision_mode')=='scoped_replay':
                response={'answer':json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],
                    'tracks':[{'track_ref':f'N{i+1}','observation_ids':[p['observation_id']],
                               'reason':'首组单阵起始暂定；不确认跨阵身份'} for i,p in enumerate(context['current_unit_observations'])]})}
        elif hasattr(gateway,'stream'):
            async def consume():
                result=None;partial='';last=0
                async for event in gateway.stream([],prompt,settings,Path(path).parent/'model_calls','data'):
                    if event.get('event')=='delta':
                        partial+=event.get('text','')
                        if len(partial)>4_000_000:raise ValueError('模型正文超过保存上限')
                        if time.monotonic()-last>.3:
                            with CONTROL:
                                current=read_run(path,rid);current['steps'][-1]['streamed_body']=partial;write_run(path,current)
                            last=time.monotonic()
                    elif event.get('event')=='model_complete':result=event['result']
                if result is None:raise ValueError('上游流未完整结束，调用结果未知')
                return result
            response=asyncio.run(consume())
        else:response=gateway.invoke([],prompt,settings,Path(path).parent/'model_calls','data')
        with CONTROL:
            run=read_run(path,rid);step=run['steps'][-1]
            step.update(answer=response.get('answer',''),usage=response.get('usage'),status='returned',streamed_body=response.get('answer',''))
            write_run(path,run)  # Keep returned original even when validation fails.
            try:apply_returned(run,frame,context,response.get('answer',''),response.get('usage'),'local_seed' if local else 'model')
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                run['status']='failed';run['steps'][-1].update(status='invalid_response',error=str(exc));write_run(path,run);raise
            write_run(path,run);state=read_state(path);state['status']='sequential_'+run['status'];save_state(path,state)
            return public_run(run)
    except BaseException as exc:
        with CONTROL:
            run=read_run(path,rid)
            if run['status']=='running':
                run['status']='result_unknown';run['steps'][-1].update(status='result_unknown',error=type(exc).__name__)
                write_run(path,run)
            state=read_state(path);state['status']='sequential_'+run['status'];save_state(path,state)
        raise


def recover_returned(path,rid,read_state,save_state):
    with CONTROL:
        run=read_run(path,rid);state=read_state(path)
        if state.get('active_sequential_run')!=rid or state['version']!=run['root_version']:raise ValueError('会话版本已变化')
        if run['status']!='failed' or not run['steps'] or run['steps'][-1]['status']!='invalid_response':raise ValueError('仅能免费复核已完整返回的失败回复')
        step=run['steps'][-1];frame,context,_=frame_for_step(run)
        # Additive prompt evidence must not invalidate a saved pre-upgrade reply.
        # All original measurements/version/selection checks remain exact.
        if 'fragment_stream_groups' not in step['context']:context.pop('fragment_stream_groups',None)
        if context!=step['context']:raise ValueError('原步骤快照已变化')
        apply_returned(run,frame,context,step['answer'],step.get('usage'),step['source']);write_run(path,run)
        state['status']='sequential_'+run['status'];save_state(path,state);return public_run(run)


def review_run(path,rid,adopt,read_state,save_state):
    with CONTROL:
        run=read_run(path,rid);state=read_state(path)
        if state.get('active_sequential_run')!=rid or state['version']!=run['root_version']:raise ValueError('会话版本已变化')
        if run['status']=='running' or (adopt and run['status']!='completed_awaiting_review'):raise ValueError('运行尚未完成，不能确认')
        if adopt:
            work=run['working']
            if work.get('pending_review_requests'):raise ValueError('仍有Agent请求复核的历史片段，不能直接确认；请先结束保留旧结果并明确准备复核')
            for key in ('tracks','targets','next_target','next_track','ambiguities','unassigned_ids','submitted_ids','rounds','joint_identity_version','continuation_graph','continuation_revisions','batch_assignments','batch_fragment_slots','next_batch_target','batch_position_choices'):
                if key in work:state[key]=copy.deepcopy(work[key])
            for row in state['rounds']:
                if row.get('sequential_run_id')==rid:row['review_status']='adopted'
            for target in state.get('targets',{}).values():target['review_status']='adopted'
            state['plot_status']='awaiting_model'
            units=run.get('baseline_units',[])+run['spec']['queue'][:run['cursor']]
            seen=set();state['sequential_units']=[]
            for unit in units:
                key=tuple(sorted(unit['candidate_ids']))
                if key not in seen:state['sequential_units'].append(copy.deepcopy(unit));seen.add(key)
        state.pop('active_sequential_run',None);state['last_sequential_run']=rid;state['status']='ready'
        state['version']=max(state['version'],run['working']['version'] if adopt else state['version'])+1
        save_state(path,state);run['status']='adopted' if adopt else 'rejected';write_run(path,run)
        return state


def extend_review(path,rid,read_state):
    """Explicit free preparation; invalidates prior digest, never dispatches."""
    with CONTROL:
        run=read_run(path,rid);state=read_state(path)
        if state.get('active_sequential_run')!=rid or state['version']!=run['root_version']:raise ValueError('会话版本已变化')
        if rid in ACTIVE or run['status']!='completed_awaiting_review' or not run['working'].get('pending_review_requests'):
            raise ValueError('只有队列结束且存在明确复核请求时才能准备追加复核')
        if run['calls']>=run['spec']['call_limit'] or state['calls']>=state['max_calls']:raise ValueError('复核调用预算已用尽，未追加调用')
        unit=copy.deepcopy(run['spec']['queue'][-1]);unit.update(unit_id='V'+uuid.uuid4().hex[:12],review_only=True)
        tentative.plan(run['working'],unit['candidate_ids'],run['spec']['review_limits'])
        run.setdefault('queue_extensions',[]).append({'previous_digest':run['spec']['context_digest'],'cursor':run['cursor'],'reason':'explicit_pending_review'})
        run['spec']['queue'].append(unit);run['spec']['model_call_upper_bound']+=1
        clean={k:v for k,v in run['spec'].items() if k not in ('context_digest','model_calls')}
        run['spec']['context_digest']=digest(clean);run['status']='paused';write_run(path,run)
        return public_run(run)


def working_view(run):
    """Read-only display projection; never reads unvalidated model proposals."""
    work=run['working'];known=set(work['submitted_ids']);lookup={p['candidate_id']:p for p in work['points']}
    pending=None
    if run['cursor']<len(run['spec']['queue']) and run['steps']:
        step=run['steps'][-1]
        if step['index']==run['cursor']+1 and step['status'] in ('running','returned','invalid_response','result_unknown'):
            pending=run['spec']['queue'][run['cursor']]
    visible=known|set(pending['candidate_ids'] if pending else [])
    ambiguous={a['candidate_id'] for a in work.get('ambiguities',[])};unassigned=set(work['unassigned_ids']);membership={}
    for cid in known:
        membership[cid]={'status':'ambiguous' if cid in ambiguous else 'unassigned','fragment_id':None,'target_id':None}
    for tid,track in work['tracks'].items():
        target=next((key for key,t in work.get('targets',{}).items() if tid in t.get('legacy_track_ids',[])),None)
        for p in track['history']:
            cid=p['candidate_id']
            if cid in known and cid not in ambiguous and cid not in unassigned:
                membership[cid]={'status':'associated','fragment_id':'F'+tid[1:] if tid.startswith('T') else tid,'target_id':target}
    units=[];used=set()
    if pending:
        units.append({'unit_id':pending['unit_id'],'candidate_ids':list(pending['candidate_ids']),'current_run':True,'validated':False});used.update(pending['candidate_ids'])
        for cid in set(pending['candidate_ids'])-known:membership[cid]={'status':'pending_validation','fragment_id':None,'target_id':None}
    # Current validated units take display priority over legacy partial containers.
    for current,rows in ((True,run['spec']['queue'][:run['cursor']]),(False,run.get('baseline_units',[]))):
        for unit in rows:
            ids=unit['candidate_ids']
            if set(ids)<=known and not used.intersection(ids):
                units.append({'unit_id':unit['unit_id'],'candidate_ids':list(ids),'current_run':current,'validated':True});used.update(ids)
    for cid in sorted(visible-used,key=lambda cid:(lookup[cid]['t'],cid)):
        units.append({'unit_id':'history:'+work['aliases'][cid],'candidate_ids':[cid],'current_run':False,'validated':True})
    unit_names=set()
    for unit in units:
        if unit['unit_id'] in unit_names:unit['unit_id']+=':'+digest(sorted(unit['candidate_ids']))[:12]
        unit_names.add(unit['unit_id'])
    return {'protocol':'working-view-v1','validated_cursor':run['cursor'],'provisional':run['status']!='adopted',
            'points':[copy.deepcopy(lookup[cid]) for cid in sorted(visible,key=lambda cid:(lookup[cid]['t'],cid))],
            'units':units,'membership':membership,'pending_unit_count':int(pending is not None),
            'continuation_graph':copy.deepcopy(work.get('continuation_graph') or {}),
            'revisions':copy.deepcopy(work.get('continuation_revisions') or []),
            'pending_review_requests':copy.deepcopy(work.get('pending_review_requests') or [])}


def public_run(run):
    view=working_view(run)
    if run['spec'].get('execution_mode')=='batch-snapshot-v1' and run['steps'] and (run['status'] in ('running','failed','result_unknown') or run.get('resume_merge') or run.get('resume_batch')):
        rows=run['spec']['queue'][run['cursor']:run['cursor']+run['spec']['batch_size']]
        seen={p['candidate_id'] for p in view['points']};unit_ids={u['unit_id'] for u in view['units']};lookup={p['candidate_id']:p for p in run['working']['points']}
        for unit in rows:
            if unit['unit_id'] not in unit_ids:view['units'].append({**copy.deepcopy(unit),'validated':False,'current_run':True})
            for cid in unit['candidate_ids']:
                if cid not in seen:
                    view['points'].append(copy.deepcopy(lookup[cid]));seen.add(cid)
                    view['membership'][cid]={'status':'pending_validation','fragment_id':None,'target_id':None}
        view['pending_unit_count']=len(rows)
    if not view['continuation_graph']:
        # Legacy validated histories can supply display edges without re-running
        # the model or changing the pinned association policy/storage.
        view['continuation_graph']=tentative.graph(run['working'],run['working']['version'])
    if run['spec'].get('spatial_association_contract')=='model-selected-spatial-cards-v1':
        view['model_position_choices']=copy.deepcopy(run['working'].get('batch_position_choices',{}))
    view['spatial_preview']=spatial_preview(view,run['working'].get('platform_config'))
    queue=run['spec']['queue'];geometry=view['spatial_preview'];spatial_points=[p for tr in geometry['tracks'] for p in tr['points']]
    view['progress']={'queue_steps':len(queue),'validated_steps':run['cursor'],'pending_steps':view['pending_unit_count'],
        'unstarted_steps':max(0,len(queue)-run['cursor']-view['pending_unit_count']),
        'queued_raw_observations':len({c for u in queue for c in u['candidate_ids']}),
        'visible_raw_observations':len(view['points']),'visible_units':len(view['units']),
        'spatial_units':len({p['unit_id'] for p in spatial_points}),'spatial_points_including_mirrors':len(spatial_points),
        'unavailable_spatial_units':len(geometry['unavailable'])}
    return {k:copy.deepcopy(v) for k,v in run.items() if k not in ('working','request_key','model_reference','baseline_submitted_ids','baseline_units')}|{'working_summary':{
        'provisional':True,'fragments':len(run['working']['tracks']),'targets':len(run['working'].get('targets',{})),
        'unassigned':len(run['working']['unassigned_ids']),'ambiguous':len(run['working']['ambiguities'])},'worker_active':run['id'] in ACTIVE,'working_view':view}
