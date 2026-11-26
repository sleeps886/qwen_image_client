"""Source-blind, bounded review permissions. Heuristics propose, never assign."""
import copy
import math

POLICY='tentative-local-v1'
DEFAULTS={'max_review_points':80,'long_gap_seconds':180.,'residual_floor_deg':15.,'max_rate_deg_per_second':5.}


def stream(p):return p['platform_id'],p['array']


def plan(work,current_ids,config,permitted_tracks=None):
    lookup={p['candidate_id']:p for p in work['points']}
    issues=[];candidates=[];affected=set();new=[lookup[c] for c in current_ids]
    def issue(code,ids,tracks=()):
        issues.append({'code':code,'observation_ids':[work['aliases'][c] for c in ids],
                       'fragment_refs':['F'+t[1:] for t in tracks]});affected.update(tracks)
    requested=set()
    for request in work.get('pending_review_requests',[]) or []:
        ids=request['candidate_ids'];requested.update(ids)
        tids=[tid for tid,t in work['tracks'].items() if any(p['candidate_id'] in ids for p in t['history'])]
        if permitted_tracks is not None and set(tids)-permitted_tracks:raise ValueError('绑定模式复核请求涉及其他目标')
        issue('agent_requested_review',ids,tids)
    current_streams={stream(p) for p in new}
    for edge in (work.get('continuation_graph') or {}).get('edges',[]):
        if edge['status']=='suspect' and stream(lookup[edge['to_id']]) in current_streams:
            tid='T'+edge['fragment_id'][1:]
            if tid in work['tracks'] and (permitted_tracks is None or tid in permitted_tracks):issue('open_suspect_edge',[edge['from_id'],edge['to_id']],[tid])
    for p in new:
        plausible=[];matching=[]
        for tid,t in work['tracks'].items():
            if permitted_tracks is not None and tid not in permitted_tracks:continue
            history=t['history']
            if not history or stream(history[-1])!=stream(p):continue
            matching.append(tid);last=history[-1];dt=p['t']-last['t']
            error=max(config['residual_floor_deg'],3*math.hypot(p.get('sigma_deg',1.),last.get('sigma_deg',1.)))
            if dt<=0:
                issue('same_time_competition' if dt==0 else 'late_arrival',[last['candidate_id'],p['candidate_id']],[tid]);continue
            slope=0.
            if len(history)>1 and last['t']>history[-2]['t']:
                slope=(last['bearing_deg']-history[-2]['bearing_deg'])/(last['t']-history[-2]['t'])
            expected=last['bearing_deg']+slope*dt
            residual=abs(p['bearing_deg']-expected)
            # A lone seed has no estimated velocity; do not demand zero motion.
            limit=error if len(history)>1 else max(error,config['max_rate_deg_per_second']*dt)
            ok=residual<=limit and abs(p['bearing_deg']-last['bearing_deg'])/dt<=config['max_rate_deg_per_second']
            candidates.append({'observation_id':work['aliases'][p['candidate_id']],'fragment_ref':'F'+tid[1:],
                               'residual_deg':residual,'gate_deg':limit,'support_count':len(history),'plausible':ok})
            if ok:plausible.append(tid)
            if dt>config['long_gap_seconds']:issue('long_gap',[last['candidate_id'],p['candidate_id']],[tid])
        if len(plausible)>1:issue('competing_continuations',[p['candidate_id']],plausible)
        elif matching and not plausible:issue('continuation_conflict',[p['candidate_id']],matching)
    streams={stream(p) for p in new}
    # Previously unassigned/ambiguous facts may be reconsidered, not forgotten.
    unresolved=set(work.get('unassigned_ids',[]))|{a['candidate_id'] for a in work.get('ambiguities',[])}
    editable=requested|{c for c in unresolved if c in lookup and stream(lookup[c]) in streams}
    for tid in affected:
        # Reopen the tentative portion of the affected fragment, not an arbitrary
        # fixed number of queue groups. The hard bound below forbids truncation.
        editable.update(p['candidate_id'] for p in work['tracks'][tid]['history'])
    if len(editable)>config['max_review_points']:
        raise ValueError(f'可疑片段复核范围{len(editable)}条超过上限{config["max_review_points"]}；已暂停，未调用模型，未自动截断历史')
    for i,item in enumerate(issues,1):item['issue_id']=f'D{i}'
    return {'policy':POLICY,'issues':issues,'candidate_continuations':candidates,
            'editable_ids':sorted(editable),'current_ids':list(current_ids),
            'mode':'local_review' if issues else 'tentative_append','bounded_review_count':len(editable)}


def prepare(work,current_ids,config,prepare_mixed,permitted_tracks=None):
    review=plan(work,current_ids,config,permitted_tracks);editable=set(review['editable_ids']);selected=list(dict.fromkeys(current_ids+review['editable_ids']))
    temporary=copy.deepcopy(work)
    for t in temporary['tracks'].values():t['history']=[p for p in t['history'] if p['candidate_id'] not in editable]
    # prepare_mixed's legacy time-tail replay is deliberately not used here.
    temporary['submitted_ids']=[];temporary['unassigned_ids']=[];temporary['ambiguities']=[]
    ctx,prompt=prepare_mixed(temporary,selected)
    ctx.update(revision_mode='scoped_replay',affected_from=None,recheck_association=bool(editable))
    ctx['editable_observation_ids']=[work['aliases'][c] for c in review['editable_ids']]
    ctx['continuation_policy']={k:v for k,v in review.items() if k not in ('editable_ids','current_ids')}
    ctx['continuation_policy']['limits']=copy.deepcopy(config)
    ctx['continuation_policy']['read_only_history']=True
    return ctx,prompt,review


def check_scope(work,ctx,checked):
    editable=set(ctx['editable_observation_ids'])|{p['observation_id'] for p in ctx['current_unit_observations']}
    def owners(tracks):return {work['aliases'][p['candidate_id']]:tid for tid,t in tracks.items() for p in t['history']}
    before=owners(work['tracks']);after=owners(checked['tracks'])
    if any(after.get(oid)!=tid for oid,tid in before.items() if oid not in editable):
        raise ValueError('局部修订越权：只读历史归属发生变化；未提交工作版本')
    touched={tid for owners in (before,after) for oid,tid in owners.items() if oid in editable}
    for tid in set(work.get('targets',{}))|set(checked.get('targets',{})):
        old=work.get('targets',{}).get(tid);new=checked.get('targets',{}).get(tid)
        involved=set((old or {}).get('legacy_track_ids',[]))|set((new or {}).get('legacy_track_ids',[]))
        if old!=new and not involved.intersection(touched):
            raise ValueError('局部修订越权：修改了无关物理目标')
    # Preserve unrelated pending pools; validator only sees editable observations.
    allowed={p['observation_id'] for p in ctx['observations']}
    checked['unassigned_ids']=sorted(set(checked['unassigned_ids'])|{c for c in work['unassigned_ids'] if work['aliases'][c] not in allowed})
    checked['ambiguities']+=copy.deepcopy([a for a in work['ambiguities'] if work['aliases'][a['candidate_id']] not in allowed])


def graph(work,version,issues=()):
    edges=[]
    for tid,t in work['tracks'].items():
        ps=t['history']
        for a,b in zip(ps,ps[1:]):
            if stream(a)!=stream(b) or a['t']>=b['t']:continue
            edges.append({'from_id':a['candidate_id'],'to_id':b['candidate_id'],'fragment_id':'F'+tid[1:],
                          'status':'low_confidence' if len(ps)<4 else 'supported_tentative','version':version})
    return {'version':version,'edges':edges,'issues':copy.deepcopy(list(issues))}


def revision(before,after,ctx,version,result=None):
    def owners(state):return {p['candidate_id']:tid for tid,t in state['tracks'].items() for p in t['history']}
    old=owners(before);new=owners(after)
    changes=[{'candidate_id':cid,'from_fragment':old.get(cid),'to_fragment':new.get(cid)}
             for cid in sorted(old.keys()|new.keys()) if old.get(cid)!=new.get(cid)]
    old_edges=(before.get('continuation_graph') or {}).get('edges',[])
    current=graph(after,version,ctx['continuation_policy']['issues'])
    resolutions=(result or {}).get('issue_resolutions',[])
    if not isinstance(resolutions,list):raise ValueError('疑点处理结果须为列表')
    issues={i['issue_id']:i for i in current['issues']};seen=set()
    for row in resolutions:
        if not isinstance(row,dict) or not isinstance(row.get('issue_id'),str) or row['issue_id'] not in issues or row['issue_id'] in seen:
            raise ValueError('疑点处理引用不存在或重复')
        if row.get('decision') not in ('retain','revised','unresolved') or not isinstance(row.get('reason'),str) or not row['reason'].strip() or len(row['reason'])>300:
            raise ValueError('疑点处理需要有效决定和依据')
        seen.add(row['issue_id']);issues[row['issue_id']]['resolution']=copy.deepcopy(row)
    suspect={ref for issue in current['issues'] if issue.get('resolution',{}).get('decision','unresolved')=='unresolved' for ref in issue['fragment_refs']}
    for edge in current['edges']:
        if edge['fragment_id'] in suspect:edge['status']='suspect'
    keys={(e['from_id'],e['to_id'],e['fragment_id']) for e in current['edges']}
    withdrawn=[e for e in old_edges if (e['from_id'],e['to_id'],e['fragment_id']) not in keys]
    return current,{'version':version,'mode':ctx['continuation_policy']['mode'],
                    'issues':current['issues'],'changes':changes,'withdrawn_edges':withdrawn}


def review_requests(result,work,ctx,limit):
    requests=result.get('review_requests',[])
    if not isinstance(requests,list):raise ValueError('局部复核请求须为列表')
    reverse={v:k for k,v in work['aliases'].items()}
    visible=set(work['submitted_ids'])|{reverse[p['observation_id']] for p in ctx['observations']}
    parsed=[];total=set()
    for request in requests:
        if not isinstance(request,dict):raise ValueError('局部复核请求格式无效')
        ids=request.get('observation_ids');reason=request.get('reason')
        if not isinstance(ids,list) or not ids or not isinstance(reason,str) or not reason.strip() or len(reason)>300:
            raise ValueError('局部复核请求需要观测编号和简短依据')
        if any(not isinstance(oid,str) or reverse.get(oid) not in visible for oid in ids):
            raise ValueError('局部复核请求引用不存在或未处理观测')
        cids=sorted({reverse[oid] for oid in ids});total.update(cids)
        if len(total)>limit:raise ValueError('局部复核请求超出范围预算；未自动追加调用')
        parsed.append({'candidate_ids':cids,'reason':reason})
    return parsed
