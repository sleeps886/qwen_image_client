"""Bounded stage-specific retries. Returned invalid output != unknown request."""
import copy
import json
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from backend.app.services.association import batch_snapshot as batch,batch_protocol as protocol,sequential_runner as seq
from backend.app.services.association import spatial_card_protocol as spatial


class BudgetBlocked(Exception):pass
class RetryPaused(Exception):pass


def used_retries(run):
    count=0
    for step in run['steps']:
        counts=Counter((a['kind'],a.get('unit_id')) for a in step.get('attempts',[]))
        count+=sum(max(0,n-1) for n in counts.values())
    return count


def wait_retry(seconds):
    time.sleep(seconds+random.uniform(0,.4))


def error_code(error):
    text=str(error)
    if 'JSON' in text:return 'invalid_json'
    if '漏判' in text:return 'missing_units'
    if 'reason' in text:return 'invalid_reason_code'
    return 'invalid_schema_or_reference'


def execute(path,rid,cursor,settings,gateway,read_state,save_state):
    with seq.CONTROL:
        run=seq.read_run(path,rid);root=read_state(path)
        if (run['cursor']!=cursor or run['status'] not in ('prepared','paused') or root.get('active_sequential_run')!=rid or root['version']!=run['root_version']):raise ValueError('批次版本或状态已变化，未调用模型')
        if run.get('pause_requested'):raise ValueError('已请求暂停')
        ref=seq.model_reference(settings)
        if not settings.api_key or run['spec'].get('model_fingerprint') not in (None,ref) or run.get('model_reference') not in (None,ref):raise ValueError('模型配置缺失或已改变')
        units,context=batch.context_for(run);base=copy.deepcopy(run['working'])
        resume=bool(run.get('resume_batch') or run.get('resume_merge'))
        if resume:
            step=run['steps'][-1]
            if seq.digest(context)!=seq.digest(step['context']):raise ValueError('冻结快照已变化，不复用旧回复')
            step.update(status='running');step.pop('error',None)
        else:
            if min(run['spec']['call_limit']-run['calls'],root['max_calls']-root['calls'])<len(units)+1:raise ValueError('批次正常流程预算不足，未调用模型')
            step={'index':len(run['steps'])+1,'status':'running','source':'parallel_batch_model','context':context,'worker_results':[],'answer':'','warnings':[]}
            run['steps'].append(step)
        for key in ('resume_batch','resume_merge','last_error'):run.pop(key,None)
        run.update(status='running',model_reference=ref);seq.write_run(path,run)
        root['status']='sequential_running';save_state(path,root)
        policy=run['spec'].get('retry_policy',{});strict=run['spec'].get('output_protocol')==protocol.FORMAT
        previous={r['unit_id']:r for r in step.get('worker_results',[])}
    retry_slots=threading.BoundedSemaphore(min(5,max(1,run['spec']['concurrency'])))

    def call(kind,payload,unit=None):
        feedback=None
        while True:
            with seq.CONTROL:
                current=seq.read_run(path,rid);entry=current['steps'][-1]
                attempts=[a for a in entry.get('attempts',[]) if a['kind']==kind and a.get('unit_id')==unit]
                n=len(attempts);is_retry=n>0
                if is_retry and n>policy.get('max_retries',0):
                    return {'unit_id':unit,'status':'invalid_response','error':'已达到该环节最大重试次数','error_code':'retry_exhausted'}
                if is_retry and current.get('pause_requested'):
                    return {'unit_id':unit,'status':'retry_pending','error':'已暂停，未发起下一次重试'}
                if is_retry and used_retries(current)>=policy.get('budget',0):
                    return {'unit_id':unit,'status':'budget_blocked','error':'额外重试预算已用完，未发出请求'}
                if is_retry:
                    last=attempts[-1];feedback={'error_code':last.get('error_code','invalid_schema_or_reference'),
                        'requirement':'只返回完整JSON及允许的reason_code，覆盖全部明确组号；不要复述上次无效正文'}
                    delay=policy.get('backoff_seconds',[2,5])[min(n-1,1)]
                    last['next_retry_at']=time.time()+delay
                    entry['retry_state']={'kind':kind,'unit_id':unit,'retry':n,'maximum':policy.get('max_retries',0),'next_retry_at':last['next_retry_at']}
                    seq.write_run(path,current)
            if is_retry:wait_retry(delay)
            acquired=False
            try:
                if is_retry:retry_slots.acquire();acquired=True
                cards = 'spatial_frame' in context
                wire=spatial.wire_for(payload,unit) if cards else protocol.encode(payload) if strict else copy.deepcopy(payload)
                if feedback:wire['retry_feedback']=feedback
                with seq.CONTROL:
                    current=seq.read_run(path,rid);state=read_state(path);entry=current['steps'][-1]
                    if is_retry and current.get('pause_requested'):raise RetryPaused('暂停已生效，未发起下一次重试')
                    if state.get('active_sequential_run')!=rid or state['version']!=current['root_version'] or current['working']['version']!=context['snapshot_version']:raise BudgetBlocked('状态版本已变化，未发送')
                    if current['calls']>=current['spec']['call_limit'] or state['calls']>=state['max_calls']:raise BudgetBlocked('调用次数总上限不足')
                    if is_retry and used_retries(current)>=policy.get('budget',0):raise BudgetBlocked('额外重试预算已用完')
                    current['calls']+=1;state['calls']+=1
                    attempt={'kind':kind,'unit_id':unit,'attempt':n+1,'is_retry':is_retry,'status':'dispatched',
                             'input_hash':seq.digest(wire),'input_chars':len(json.dumps(wire,ensure_ascii=False,separators=(',',':'))),'feedback':feedback,'started_at':time.time()}
                    entry.setdefault('attempts',[]).append(attempt);index=len(entry['attempts'])-1
                    if strict:
                        if kind=='merge':entry['merge_wire_input']=spatial.wire_for(payload) if cards else protocol.encode(payload)
                        elif 'worker_wire_context' not in entry:entry['worker_wire_context']=spatial.wire_for(context) if cards else protocol.encode(context)
                    current['retry_calls']=used_retries(current)
                    seq.write_run(path,current);save_state(path,state)
                instruction=batch.WORKER_INSTRUCTION if kind=='worker' else batch.MERGE_INSTRUCTION
                if cards:instruction=spatial.WORKER if kind=='worker' else spatial.MERGE
                elif strict:instruction=protocol.instruction(kind,instruction)
                try:
                    response=gateway.invoke([],instruction.replace('\n',' ')+'\n'+json.dumps(wire,ensure_ascii=False,separators=(',',':')),settings,path.parent/'model_calls','data')
                except Exception as error:
                    with seq.CONTROL:
                        current=seq.read_run(path,rid);a=current['steps'][-1]['attempts'][index]
                        a.update(status='result_unknown',error_type=type(error).__name__,finished_at=time.time());seq.write_run(path,current)
                        if getattr(error,'transport_diagnostic',None):
                            a['transport_diagnostic']=error.transport_diagnostic;seq.write_run(path,current)
                    return {'unit_id':unit,'status':'result_unknown','error_type':type(error).__name__,'error':'请求结果未知，可能已计费；未自动重试'}
                answer=response.get('answer','')
                try:
                    if cards:
                        obj=batch.parse(answer,context['snapshot_version'])
                        obj=spatial.worker_proposal(obj,context,unit) if kind=='worker' else spatial.expand_merge(obj,context)
                    else:obj=batch.worker_proposal(answer,context['snapshot_version'],unit,strict) if kind=='worker' else batch.parse(answer,context['snapshot_version'])
                    if kind=='worker' and strict and not cards and obj['target_ref'] not in set(context['snapshot']['targets'])|{'new','unassigned'}:raise ValueError('单组target_ref须为已有目标、new或unassigned')
                    if kind=='merge':batch.validate_merge(base,units,obj,strict)
                    status='returned';error=None
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    obj=None;status='invalid_response';error=exc
                with seq.CONTROL:
                    current=seq.read_run(path,rid);entry=current['steps'][-1];a=entry['attempts'][index]
                    a.update(status=status,answer=answer,usage=response.get('usage'),finished_at=time.time())
                    if error:a.update(error=str(error)[:600],error_code=error_code(error))
                    elif entry.get('retry_state',{}).get('kind')==kind and entry.get('retry_state',{}).get('unit_id')==unit:entry.pop('retry_state',None)
                    if kind=='merge':entry.update(answer=answer,usage=response.get('usage'))
                    seq.write_run(path,current)
                if status=='returned':
                    return {'unit_id':unit,'status':'returned','answer':answer,'proposal':obj,'usage':response.get('usage'),
                            'warnings':batch.proposal_warnings(obj) if kind=='worker' else []}
                # Persist all invalid originals; never trim or patch their JSON.
                if n>=policy.get('max_retries',0):
                    return {'unit_id':unit,'status':'invalid_response','answer':answer,'error':str(error)[:600],'error_code':error_code(error)}
            except BudgetBlocked as error:
                return {'unit_id':unit,'status':'budget_blocked','error':str(error)}
            except RetryPaused as error:
                return {'unit_id':unit,'status':'retry_pending','error':str(error)}
            finally:
                if acquired:retry_slots.release()

    try:
        def job(unit):
            old=previous.get(unit['unit_id'])
            if old and old.get('status')=='returned':return old
            return call('worker',{**context,'focus_unit_id':unit['unit_id']},unit['unit_id'])
        with ThreadPoolExecutor(max_workers=run['spec']['concurrency']) as pool:
            futures=[pool.submit(job,u) for u in units]
            for future in as_completed(futures):
                result=future.result()
                with seq.CONTROL:
                    current=seq.read_run(path,rid);entry=current['steps'][-1]
                    entry['worker_results']=[r for r in entry['worker_results'] if r['unit_id']!=result['unit_id']]+[result]
                    entry['warnings']=[w for r in entry['worker_results'] for w in r.get('warnings',[])]
                    seq.write_run(path,current)
        current=seq.read_run(path,rid);results=current['steps'][-1]['worker_results']
        failures=[r for r in results if r['status']!='returned']
        if failures:
            unknown=any(r['status']=='result_unknown' for r in failures)
            state='result_unknown' if unknown else 'paused' if any(r['status']=='retry_pending' for r in failures) else 'failed'
            with seq.CONTROL:
                current=seq.read_run(path,rid);current.update(status=state,last_error=f'批内{len(failures)}组未通过；成功单组已保留，只恢复失败环节，不整批重发')
                if state=='paused':current['resume_batch']=True
                current['steps'][-1].update(status=state,error=current['last_error']);seq.write_run(path,current)
            return
        proposals=[r['proposal'] for r in results]
        payload={**context,'worker_proposals':sorted(proposals,key=lambda p:p['unit_id'])}
        with seq.CONTROL:
            current=seq.read_run(path,rid);current['steps'][-1]['merge_input']=payload;current['steps'][-1].pop('retry_state',None);seq.write_run(path,current)
        result=call('merge',payload)
        if result['status']!='returned':
            with seq.CONTROL:
                current=seq.read_run(path,rid);state='result_unknown' if result['status']=='result_unknown' else 'paused' if result['status']=='retry_pending' else 'failed'
                current.update(status=state,last_error=result.get('error','归并失败；单组建议保留'))
                if state=='paused':current['resume_batch']=True
                current['steps'][-1].update(status=state,error=current['last_error']);seq.write_run(path,current)
            return
        updated,revision=batch.apply(base,units,result['proposal'],rid,context,result.get('usage'),strict)
        with seq.CONTROL:
            current=seq.read_run(path,rid);state=read_state(path)
            if state['version']!=current['root_version'] or current['working']['version']!=context['snapshot_version']:raise ValueError('快照已变化，不写入旧结果')
            current['working']=updated;current['cursor']+=len(units)
            current['steps'][-1].update(status='validated',revision=revision)
            current['steps'][-1].pop('retry_state',None)
            current['status']='completed_awaiting_review' if current['cursor']==len(current['spec']['queue']) else 'paused'
            snap=batch.snapshot(updated,None);snap.update(assignment={updated['aliases'][c]:v for c,v in updated['batch_assignments'].items()},batch_index=len(current['steps']))
            snap['digest']=seq.digest(snap)
            with seq.connection(path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS batch_snapshots (run_id TEXT,batch_index INTEGER,version INTEGER,payload TEXT,PRIMARY KEY(run_id,batch_index))')
                db.execute('INSERT INTO batch_snapshots VALUES (?,?,?,?)',(rid,len(current['steps']),updated['version'],json.dumps(snap,ensure_ascii=False)))
                db.execute('UPDATE sequential_runs SET payload=? WHERE id=?',(json.dumps(current,ensure_ascii=False,allow_nan=False),rid))
    except Exception as error:
        with seq.CONTROL:
            current=seq.read_run(path,rid);current.update(status='failed',last_error=str(error)[:600]);current['steps'][-1].update(status='failed',error=current['last_error']);seq.write_run(path,current)
    finally:
        with seq.CONTROL:
            current=seq.read_run(path,rid);state=read_state(path)
            if state.get('active_sequential_run')==rid and state['version']==current['root_version']:
                state['status']='sequential_'+current['status'];save_state(path,state)


def prepare_retry(path,rid,read_state,save_state,expected_cursor,context_digest,call_limit,retry_limit,retry_budget,consent):
    """Explicit free reauthorization; never invokes a model or retries unknown calls."""
    if not consent:raise ValueError('须明确确认重试策略及总费用调用上限；此操作不发送模型')
    if type(retry_limit) is not int or not 1<=retry_limit<=2 or type(retry_budget) is not int or not 1<=retry_budget<=1000:raise ValueError('重试次数/预算无效')
    if type(call_limit) is not int or not 1<=call_limit<=10000:raise ValueError('调用总上限无效')
    with seq.CONTROL:
        run=seq.read_run(path,rid);root=read_state(path)
        if rid in seq.ACTIVE or run['status']!='failed' or root.get('active_sequential_run')!=rid or root['version']!=run['root_version']:raise ValueError('仅能准备已停止的失败批次，不自动重试未知调用')
        if run['cursor']!=expected_cursor or run['spec']['context_digest']!=context_digest:raise ValueError('运行版本/授权已变化')
        units,context=batch.context_for(run);step=run['steps'][-1]
        if seq.digest(context)!=seq.digest(step['context']):raise ValueError('冻结快照已变化')
        rows=step['worker_results']
        if len(rows)!=len(units) or any(r['status']=='result_unknown' for r in rows):raise ValueError('存在未返回/未知单组，不自动重试')
        # A redundant echo typo may now pass free request-binding validation.
        checked=[]
        for row in rows:
            if row['status']!='returned' and row.get('answer'):
                try:
                    proposal=spatial.worker_proposal(batch.parse(row['answer'],context['snapshot_version']),context,row['unit_id']) if 'spatial_frame' in context else batch.worker_proposal(row['answer'],context['snapshot_version'],row['unit_id'],run['spec'].get('output_protocol')==protocol.FORMAT)
                    row={**row,'status':'returned','proposal':proposal,'warnings':batch.proposal_warnings(proposal)}
                except (ValueError,TypeError,KeyError,AttributeError):pass
            checked.append(row)
        rows=checked
        has_merge=any(a['kind']=='merge' for a in step.get('attempts',[]))
        if has_merge:
            try:
                obj=batch.parse(step['answer'],context['snapshot_version'])
                if 'spatial_frame' in context:obj=spatial.expand_merge(obj,context)
                batch.validate_merge(run['working'],units,obj,run['spec'].get('output_protocol')==protocol.FORMAT)
            except (ValueError,TypeError,KeyError,AttributeError):pass
            else:raise ValueError('已有完整有效归并回复，须核对存储错误；不重复付费')
        failed_rows=[r for r in rows if r['status']!='returned']
        stage_counts=Counter((a['kind'],a.get('unit_id')) for a in step.get('attempts',[]))
        exhausted=[r['unit_id'] for r in failed_rows if stage_counts[('worker',r['unit_id'])]>retry_limit]
        if has_merge and stage_counts[('merge',None)]>retry_limit:exhausted.append('归并')
        if exhausted:raise ValueError('以下环节已达到最多3次尝试，不能再次自动重试：'+','.join(exhausted)+'；请人工核对，未新增调用')
        # A batch with no remaining invalid workers still needs its first merge.
        retry_used=used_retries(run)
        if retry_budget<=retry_used:raise ValueError('新增策略的重试预算须大于已用重试次数')
        remaining=len(run['spec']['queue'])-run['cursor']-len(units)
        future=remaining+math_ceil_batches(remaining,run['spec']['batch_size'])
        minimum=run['calls']+future+(0 if has_merge else 1)+retry_budget-retry_used
        if call_limit<minimum:raise ValueError(f'保留后续正常流程并授权重试，累计调用上限至少需要{minimum}次')
        run['spec'].update(execution_revision='retry-v1',output_protocol=protocol.FORMAT,call_limit=call_limit,
            retry_policy={'max_retries':retry_limit,'budget':retry_budget,'concurrency':5,'backoff_seconds':[2,5],'retry_unknown':False},model_call_upper_bound=call_limit)
        run['spec'].setdefault('base_model_call_upper_bound',len(run['spec']['queue'])+math_ceil_batches(len(run['spec']['queue']),run['spec']['batch_size']))
        spec=copy.deepcopy(run['spec']);spec.pop('context_digest',None);spec.pop('model_calls',None)
        run['spec']['context_digest']=seq.digest(spec)
        root.setdefault('budget_changes',[]).append({'from':root['max_calls'],'to':max(root['max_calls'],root['calls']+call_limit-run['calls']),'at_version':root['version'],'reason':'explicit_stage_retry'})
        root['max_calls']=max(root['max_calls'],root['calls']+call_limit-run['calls'])
        run.update(status='paused',resume_batch=True,retry_calls=retry_used,last_error='失败环节重试已免费准备；成功单组保留。须重新授权后发送，不会整批重发')
        step['worker_results']=rows
        step['previous_validation_error']=step.get('error');step['status']='retry_ready'
        seq.write_run(path,run);save_state(path,root)
        return seq.public_run(run)


def math_ceil_batches(count,size):return (count+size-1)//size
