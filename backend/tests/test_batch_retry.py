import copy
import json
import threading
from collections import Counter
from unittest.mock import patch
import pytest
from backend.app.services.association import batch_snapshot as batch,batch_protocol as protocol,batch_execution as execution,sequential_runner as seq
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.incremental_prediction import save_state
from backend.tests.test_batch_snapshot import dataset,read,SETTINGS


class Gateway:
    def __init__(self,plans=None,unassigned=False):
        self.plans=plans or {};self.calls=Counter();self.inputs=[];self.lock=threading.Lock();self.unassigned=unassigned
    def invoke(self,images,prompt,*args):
        wire=json.loads(prompt.split('\n',1)[1]);context=protocol.decode(wire) if wire.get('wire_format')==protocol.FORMAT else wire
        key=context.get('focus_unit_id','merge')
        with self.lock:
            n=self.calls[key];self.calls[key]+=1;self.inputs.append(wire)
        plan=self.plans.get(key,[]);mode=plan[min(n,len(plan)-1)] if plan else 'ok'
        if mode=='unknown':raise RuntimeError('upstream result unknown')
        if mode=='bad_json':return {'answer':'{"reason":"无关词串一直重复'}
        if key!='merge':
            answer={'snapshot_version':context['snapshot_version'],'target_ref':'unassigned' if self.unassigned else 'new','reason_code':'insufficient' if self.unassigned else 'continuity'}
        else:
            answer={'snapshot_version':context['snapshot_version'],'assignments':[
                {'unit_id':u['unit_id'],'target_ref':None if self.unassigned else next(iter(context['snapshot']['targets']),'N1'),'reason_code':'insufficient' if self.unassigned else 'continuity'} for u in context['batch_units']],'revisions':[]}
            if mode=='missing':answer['assignments']=answer['assignments'][:-1]
        if mode=='bad_code':answer['reason_code']='nonsense'
        return {'answer':json.dumps(answer),'usage':{'fake':True}}


def prepared(path,count=2,budget=2,limit=2,compact=True,policy=True):
    state=create_mixed(dataset(list(range(count))),max_calls=1000)
    units=[[f'hull{i}',f'tow{i}'] for i in range(count)]
    spec=seq.preview(state,units,call_limit=1000)
    spec.pop('context_digest');spec.pop('model_calls')
    batch.configure(spec,state,batch.MODE,2,50,limit if policy else 0,budget if policy else 0,compact if policy else False)
    spec.update(context_digest=seq.digest(spec),model_calls=0)
    run=seq.create_run(path,state,spec,'retry-test')
    state.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(path,state)
    return run


def execute(path,run,gateway):
    with patch.object(execution,'wait_retry',lambda seconds:None):batch.execute(path,run['id'],run['cursor'],SETTINGS,gateway,read,save_state)
    return seq.read_run(path,run['id'])


def test_only_bad_worker_retries_and_raw_attempts_preserved(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway({'U000001':['bad_json','ok']});r=execute(p,run,g)
    assert r['cursor']==2 and r['calls']==4 and r['retry_calls']==1
    assert g.calls=={'U000001':2,'U000002':1,'merge':1}
    bad=next(a for a in r['steps'][0]['attempts'] if a['status']=='invalid_response')
    assert '无关词串' in bad['answer'] and 'retry_feedback' in next(x for x in g.inputs if x.get('retry_feedback'))
    assert all('无关词串' not in json.dumps(x,ensure_ascii=False) for x in g.inputs)
    assert read(p)['tracks']=={} and len(batch.snapshots(p,run['id']))==1


@pytest.mark.parametrize('mode',['bad_json','missing'])
def test_merge_retry_does_not_repeat_workers(tmp_path,mode):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway({'merge':[mode,'ok']});r=execute(p,run,g)
    assert r['cursor']==2 and r['calls']==4 and g.calls['merge']==2
    assert g.calls['U000001']==g.calls['U000002']==1
    assert len([a for a in r['steps'][0]['attempts'] if a['kind']=='merge'])==2


def test_retry_cap_and_global_reserve_both_enforced(tmp_path):
    p=tmp_path/'state.json';run=prepared(p,budget=10);g=Gateway({'merge':['bad_json']});r=execute(p,run,g)
    assert r['status']=='failed' and r['calls']==5 and g.calls['merge']==3
    assert not batch.snapshots(p,run['id'])
    p2=tmp_path/'second.json';p2.parent.mkdir(exist_ok=True)
    # Separate DB per session.
    p2=tmp_path/'second'/'state.json';p2.parent.mkdir();run=prepared(p2,budget=1);g=Gateway({'merge':['bad_json']});r=execute(p2,run,g)
    assert r['calls']==4 and g.calls['merge']==2 and r['retry_calls']==1
    assert '预算' in r['last_error']


def test_unknown_results_never_automatically_retry(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway({'U000001':['unknown']});r=execute(p,run,g)
    assert r['status']=='result_unknown' and r['calls']==2 and g.calls['U000001']==1 and not g.calls['merge']
    assert r['retry_calls']==0


def test_unassigned_is_success_not_a_retry_condition(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway(unassigned=True);r=execute(p,run,g)
    assert r['cursor']==2 and r['calls']==3 and r['retry_calls']==0
    assert len(r['working']['unassigned_ids'])==4


def test_pause_during_backoff_prevents_new_paid_call(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway({'U000001':['bad_json','ok']})
    def pause(seconds):
        with seq.CONTROL:
            r=seq.read_run(p,run['id']);r['pause_requested']=True;seq.write_run(p,r)
    with patch.object(execution,'wait_retry',pause):batch.execute(p,run['id'],0,SETTINGS,g,read,save_state)
    r=seq.read_run(p,run['id']);assert r['status']=='paused' and r['resume_batch']
    assert g.calls['U000001']==1 and not g.calls['merge']


def test_legacy_failed_merge_explicitly_reauthorized_without_worker_calls(tmp_path):
    p=tmp_path/'state.json';run=prepared(p,policy=False);g=Gateway({'merge':['bad_json','ok']});r=execute(p,run,g)
    assert r['status']=='failed' and r['calls']==3
    before=copy.deepcopy(g.calls)
    with pytest.raises(ValueError,match='明确'):execution.prepare_retry(p,run['id'],read,save_state,0,r['spec']['context_digest'],1000,2,2,False)
    prepared_run=execution.prepare_retry(p,run['id'],read,save_state,0,r['spec']['context_digest'],1000,2,2,True)
    assert prepared_run['calls']==3 and g.calls==before
    final=execute(p,seq.read_run(p,run['id']),g)
    assert final['cursor']==2 and final['calls']==4 and g.calls['U000001']==g.calls['U000002']==1
    assert final['spec']['context_digest']!=r['spec']['context_digest']


def test_codec_exact_roundtrip_presence_and_history(tmp_path):
    p=tmp_path/'state.json';run=prepared(p,count=4);g=Gateway();run=execute(p,run,g)
    _,context=batch.context_for(run)
    context['observations'][0]['nullable']=None
    # Register all duplicated copies consistently for lossless presence tests.
    oid=context['observations'][0]['observation_id']
    for row in context['current_unit_observations']:
        if row['observation_id']==oid:row['nullable']=None
    for u in context['batch_units']:
        for row in u['observations']:
            if row['observation_id']==oid:row['nullable']=None
    wire=protocol.encode(context);assert protocol.decode(wire)==context
    assert len(wire['observations_table']['rows'])==8
    assert 'observations' not in wire and 'observations' not in wire['snapshot']
    assert len(json.dumps(wire,ensure_ascii=False))<len(json.dumps(context,ensure_ascii=False))


def test_duplicate_dispatch_cannot_repeat_same_batch(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway();r=execute(p,run,g);calls=sum(g.calls.values())
    with pytest.raises(ValueError):batch.execute(p,run['id'],0,SETTINGS,g,read,save_state)
    assert sum(g.calls.values())==calls


def test_483_total_includes_explicit_retry_reserve():
    state=create_mixed(dataset(list(range(483))),max_calls=1000)
    spec={'queue':[{}]*483,'call_limit':513}
    batch.configure(spec,state,batch.MODE,50,50,2,20,True)
    assert spec['base_model_call_upper_bound']==493 and spec['model_call_upper_bound']==513
    with pytest.raises(ValueError,match='513'):batch.configure({'queue':[{}]*483,'call_limit':493},state,batch.MODE,50,50,2,20,True)


def test_state_version_change_during_backoff_blocks_retry(tmp_path):
    p=tmp_path/'state.json';run=prepared(p);g=Gateway({'merge':['bad_json','ok']})
    def change(seconds):
        state=read(p);state['version']+=1;save_state(p,state)
    with patch.object(execution,'wait_retry',change):batch.execute(p,run['id'],0,SETTINGS,g,read,save_state)
    result=seq.read_run(p,run['id']);assert result['cursor']==0 and g.calls['merge']==1
    assert '版本' in result['last_error']
