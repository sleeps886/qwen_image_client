import copy
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.services.association.mixed_prediction import create_mixed, public_mixed
from backend.app.services.association.incremental_prediction import save_state


def dataset(times):
    return {'candidates':[{'candidate_id':f'{a}{i}','t':t,'platform_id':'A','array':a,'bearing_deg':45+i%100}
        for i,t in enumerate(times) for a in ('hull','tow')]}


class Gateway:
    def __init__(self,bad=False,fail=False):
        self.contexts=[];self.active=0;self.peak=0;self.lock=threading.Lock();self.bad=bad;self.fail=fail
    def invoke(self,images,prompt,*args):
        c=json.loads(prompt.split('\n',1)[1])
        with self.lock:self.contexts.append(c);self.active+=1;self.peak=max(self.peak,self.active)
        time.sleep(.15)
        with self.lock:self.active-=1
        if self.fail:raise RuntimeError('fixture upstream failure')
        if 'focus_unit_id' in c:
            return {'answer':json.dumps({'snapshot_version':c['snapshot_version'],'unit_id':c['focus_unit_id'],
                'target_ref':'new' if not c['snapshot']['targets'] else c['snapshot']['targets'][0],'reason':'test model decision'})}
        rows=[{'unit_id':u['unit_id'],'target_ref':c['snapshot']['targets'][0] if c['snapshot']['targets'] else 'N1','reason':'test model merge'} for u in c['batch_units']]
        return {'answer':json.dumps({'snapshot_version':c['snapshot_version'],'assignments':rows[:-1] if self.bad else rows,'revisions':[]})}


def prepared(path,times,size=2,concurrency=50):
    s=create_mixed(dataset(times),max_calls=1000)
    units=[[f'{a}{i}' for a in ('hull','tow')] for i in range(len(times))]
    spec=seq.preview(s,units,order='received',call_limit=1000)
    spec.pop('context_digest');spec.pop('model_calls')
    batch.configure(spec,s,batch.MODE,size,concurrency);spec.update(context_digest=seq.digest(spec),model_calls=0)
    run=seq.create_run(path,s,spec,'batch-test')
    s.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(path,s)
    return s,run


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
SETTINGS=ModelSettings('offline','http://invalid.local','offline')


def test_parallel_snapshots_middle_insertion_and_tail(tmp_path):
    path=tmp_path/'state.json';s,run=prepared(path,[10,30,20,40]);gateway=Gateway()
    batch.execute(path,run['id'],0,SETTINGS,gateway,read,save_state)
    a=seq.read_run(path,run['id']);assert a['cursor']==2 and a['calls']==3 and gateway.peak==2
    assert len(batch.snapshots(path,run['id']))==1
    # Future measurements absent; simultaneous workers share exact same snapshot.
    contexts=gateway.contexts[:2];assert contexts[0]['snapshot']==contexts[1]['snapshot']
    assert all(p['t'] in (10,30) for c in contexts for p in c['observations'])
    batch.execute(path,run['id'],2,SETTINGS,gateway,read,save_state)
    b=seq.read_run(path,run['id']);assert b['cursor']==4 and b['calls']==6
    assert b['status']=='completed_awaiting_review' and len(batch.snapshots(path,run['id']))==2
    assert all([p['t'] for p in tr['history']]==[10,20,30,40] for tr in b['working']['tracks'].values())
    edges=b['working']['continuation_graph']['edges'];lookup={p['candidate_id']:p for p in b['working']['points']}
    assert not any(lookup[e['from_id']]['t']==10 and lookup[e['to_id']]['t']==30 for e in edges)
    assert read(path)['tracks']=={} # provisional only
    root=seq.review_run(path,run['id'],True,read,save_state)
    assert len(root['batch_assignments'])==8 and public_mixed(root)['association_audit']['assigned_points']==8
    assert len(seq.public_run(b)['working_view']['units'])==4


def test_invalid_merge_never_partially_commits_and_no_retry(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,[10,20]);g=Gateway(bad=True)
    batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    r=seq.read_run(path,run['id']);assert r['status']=='failed' and r['cursor']==0
    assert r['working']['tracks']=={} and batch.snapshots(path,run['id'])==[]
    assert len(g.contexts)==3 and r['steps'][0]['answer']
    with pytest.raises(ValueError):batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    assert len(g.contexts)==3


def test_upstream_unknown_retains_all_worker_results_without_merge(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,[10,20]);g=Gateway(fail=True)
    batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    r=seq.read_run(path,run['id']);assert r['status']=='result_unknown' and r['calls']==2 and r['cursor']==0
    assert len(r['steps'][0]['worker_results'])==2


def test_snapshot_budget_and_strict_complete_assignments(tmp_path):
    s=create_mixed(dataset([10,20]),max_calls=2);spec={'queue':[{},{}],'call_limit':2}
    with pytest.raises(ValueError,match='3次'):batch.configure(spec,s,batch.MODE,50,50)
    s,run=prepared(tmp_path/'state.json',[10,20]);units,c=batch.context_for(run)
    valid={'assignments':[{'unit_id':u['unit_id'],'target_ref':'N1','reason':'test'} for u in units]}
    for field,value in [('unit_id','unknown'),('target_ref','B999999')]:
        obj=copy.deepcopy(valid);obj['assignments'][0][field]=value
        with pytest.raises(ValueError):batch.validate_merge(s,units,obj)
    obj=copy.deepcopy(valid);obj['revisions']=[{'observation_ids':['O999999'],'target_ref':None,'reason':'bad'}]
    with pytest.raises(ValueError):batch.validate_merge(s,units,obj)


def test_fifty_workers_concurrent_and_exact_483_budget(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,list(range(50)),size=50);g=Gateway()
    batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    r=seq.read_run(path,run['id']);assert r['cursor']==50 and r['calls']==51 and g.peak>1
    assert len(batch.snapshots(path,run['id'])[0]['observations'])==100
    s=create_mixed(dataset(list(range(483))),max_calls=1000)
    spec={'queue':[{}]*483,'call_limit':493};batch.configure(spec,s,batch.MODE,50,50)
    assert spec['model_call_upper_bound']==493


def test_saved_advisory_names_recover_without_resending_workers(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,[10,20]);g=Gateway()
    batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    r=seq.read_run(path,run['id'])
    # Reconstruct a previous-version field-validation failure, before merge.
    r['working']=copy.deepcopy(run['working']);r['cursor']=0;r['calls']=2;r['status']='failed'
    step=r['steps'][0];step['answer']='';step['status']='failed';step['worker_results'][0]['status']='invalid_response'
    obj=json.loads(step['worker_results'][0]['answer']);obj['target_ref']='LOCAL_TARGET_NAME'
    step['worker_results'][0]['answer']=json.dumps(obj)
    with seq.connection(path) as db:db.execute('DELETE FROM batch_snapshots WHERE run_id=?',(run['id'],))
    seq.write_run(path,r);state=read(path);state['calls']=2;save_state(path,state)
    recovered=batch.recover_saved_proposals(path,run['id'],read)
    assert recovered['resume_merge'] and recovered['calls']==2
    before=len(g.contexts);batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    final=seq.read_run(path,run['id']);assert final['calls']==3 and final['cursor']==2
    assert len(g.contexts)==before+1 and 'focus_unit_id' not in g.contexts[-1]


def test_history_revision_removes_old_edges_and_reassigns_only_explicit_points(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,[10,20]);g=Gateway()
    batch.execute(path,run['id'],0,SETTINGS,g,read,save_state)
    work=seq.read_run(path,run['id'])['working'];before=copy.deepcopy(work)
    ids=[work['aliases'][c] for c in ('hull0','tow0')]
    updated,revision=batch.apply(work,[],{'assignments':[],'revisions':[
        {'observation_ids':ids,'target_ref':'N1','reason':'explicit model split'}]},run['id'],{},None)
    assert len(updated['targets'])==2 and len(revision['changes'])==2 and len(revision['withdrawn_edges'])==2
    assert updated['batch_assignments']['hull1']==before['batch_assignments']['hull1'] and work==before


def test_current_geometry_names_win_over_older_run_unit_ids(tmp_path):
    _,run=prepared(tmp_path/'state.json',[10,20,30,40])
    work=run['working'];work['submitted_ids']=['hull0','tow0']
    old=copy.deepcopy(run['spec']['queue'][0]);new=copy.deepcopy(run['spec']['queue'][2]);new['unit_id']=old['unit_id']
    run['baseline_units']=[old];run['spec']['queue']=[new]
    _,context=batch.context_for(run)
    assert context['unit_observation_ids'][new['unit_id']]==[work['aliases'][c] for c in new['candidate_ids']]
    assert len(context['unit_observation_ids'])==2
    allowed={work['aliases'][c] for c in old['candidate_ids']+new['candidate_ids']}
    assert {oid for ids in context['unit_observation_ids'].values() for oid in ids}==allowed


def test_worker_echo_is_advisory_not_an_assignment_identifier():
    answer=json.dumps({'snapshot_version':0,'unit_id':'U00041','target_ref':'new','reason':'model evidence'})
    proposal=batch.worker_proposal(answer,0,'U000041')
    assert proposal['unit_id']=='U000041' and proposal['model_echoed_unit_id']=='U00041'
    assert proposal['needs_focus_verification'] and batch.proposal_warnings(proposal)
    assert json.loads(answer)['unit_id']=='U00041' # raw text unchanged
    no_echo=batch.worker_proposal(json.dumps({'snapshot_version':0,'target_ref':'new','reason':'no echo'}),0,'U000041')
    assert no_echo['unit_id']=='U000041' and not batch.proposal_warnings(no_echo)
    with pytest.raises(ValueError):batch.worker_proposal(answer,1,'U000041')


def test_echo_recovery_keeps_raw_and_final_assignment_ids_still_strict(tmp_path):
    path=tmp_path/'state.json';_,run=prepared(path,[10]);units,_=batch.context_for(run)
    obj={'assignments':[{'unit_id':'U00001','target_ref':'N1','reason':'not actual unit'}]}
    with pytest.raises(ValueError,match='组号'):batch.validate_merge(run['working'],units,obj)


def test_worker_echo_mismatch_reaches_model_merge_with_audit_warning(tmp_path):
    class EchoGateway(Gateway):
        def invoke(self,images,prompt,*args):
            reply=super().invoke(images,prompt,*args)
            context=json.loads(prompt.split('\n',1)[1])
            if context.get('focus_unit_id')=='U000001':
                obj=json.loads(reply['answer']);obj['unit_id']='U0001';reply['answer']=json.dumps(obj)
            return reply
    path=tmp_path/'state.json';_,run=prepared(path,[10,20]);gateway=EchoGateway()
    batch.execute(path,run['id'],0,SETTINGS,gateway,read,save_state)
    saved=seq.read_run(path,run['id'])
    assert saved['cursor']==2 and saved['calls']==3 and len(saved['steps'][0]['warnings'])==1
    row=next(r for r in saved['steps'][0]['worker_results'] if r['unit_id']=='U000001')
    assert json.loads(row['answer'])['unit_id']=='U0001'
    proposal=next(p for p in gateway.contexts[-1]['worker_proposals'] if p['unit_id']=='U000001')
    assert proposal['needs_focus_verification'] and proposal['model_echoed_unit_id']=='U0001'
