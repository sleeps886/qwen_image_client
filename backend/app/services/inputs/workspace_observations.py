"""Durable observation input inside the existing mixed workbench.

The SQLite ledger owns input facts; legacy JSON owns reviewed projections and
plots. A running model keeps its original input. Append never writes state.json,
so an old projection commit cannot overwrite newly received observations.
"""
import copy
from backend.app.repositories.live_observation_repository import LiveRepository


def repository(path):
    return LiveRepository(path.parent/'observations.sqlite3')


def enable(path, state, timeline_id):
    if state.get('workspace_input'):
        return hydrate(path,state)
    repo=repository(path)
    ledger=repo.create(timeline_id,'轨迹工作台观测账本',{})
    receipt=repo.ingest(ledger['session_id'],{'idempotency_key':'initial-copy','source_id':'workspace',
        'timeline_id':timeline_id,'observations':state['points']})
    if receipt['accepted'] != len(state['points']):
        raise ValueError('当前观测复制未完整成功，原会话未修改')
    result=copy.deepcopy(state)
    result['workspace_input']={'protocol':'workspace-ledger-v1','ledger_id':ledger['session_id'],
        'timeline_id':timeline_id,'origin_ids':{row['observation_id']:p['candidate_id']
            for row,p in zip(receipt['results'],state['points'])}}
    return hydrate(path,result)


def hydrate(path,state):
    pointer=state.get('workspace_input')
    if not pointer:
        return state
    repo=repository(path);ledger=repo.state(pointer['ledger_id']);rows=repo.observations(pointer['ledger_id'])
    result=copy.deepcopy(state)
    points={p['candidate_id']:p for p in result['points']}
    aliases=result['aliases']
    next_alias=max((int(a[1:]) for a in aliases.values()),default=0)+1
    receipts={}
    for row in rows:
        cid=pointer['origin_ids'].get(row['observation_id'],row['observation_id'])
        receipts[cid]=row['receipt_seq']
        if cid not in points:
            points[cid]={'candidate_id':cid,'t':row['event_time'],**{k:row[k] for k in
                ('platform_id','array','bearing_deg','strength','quality','mixed','sigma_deg','bearing_error_assumption_deg') if k in row}}
        if cid not in aliases:
            aliases[cid]=f'O{next_alias:06d}';next_alias+=1
    result['points']=sorted(points.values(),key=lambda p:(p['t'],p['platform_id'],p['array'],p['candidate_id']))
    result['_input_receipts']=receipts
    result['input_summary']={'protocol':'workspace-ledger-v1','accepted_count':len(rows),
        'receipt_seq':ledger['receipt_seq'],'observation_revision':ledger['observation_revision'],
        'timeline_id':pointer['timeline_id'],'unsubmitted_count':len(set(points)-set(state['submitted_ids'])),
        'receipt_counts':ledger['receipt_counts']}
    return result


def append(path,state,body):
    pointer=state.get('workspace_input')
    if not pointer:
        raise ValueError('请先在当前工作台启用持久化追加；旧结果保留，不会调用模型')
    if body['timeline_id'] != pointer['timeline_id']:
        raise ValueError('时间基准不一致，未向当前会话追加；请核对来源时钟')
    return repository(path).ingest(pointer['ledger_id'],body)


def export(path,state):
    if not state.get('workspace_input'):
        raise ValueError('当前会话尚未启用持久化追加')
    return {'format':'mixed-workspace-export-v1','session_state':state,
        'observation_ledger':repository(path).export(state['workspace_input']['ledger_id'])}
