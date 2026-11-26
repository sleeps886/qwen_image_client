import copy
import json
from backend.tests import test_sequential_runner as fixtures
from backend.app.services.association import sequential_runner as seq
from backend.app.services.association import tentative_continuation as tc
from backend.app.services.association.incremental_prediction import save_state


class TentativeTests(fixtures.SequentialRunnerTests):
    # Legacy runner tests remain in their own class; only new tests are collected.
    __test__=False


class TestTentativePolicy:
    def setup_method(self):
        self.fixture=fixtures.SequentialRunnerTests();self.fixture.setUp();self.s=self.fixture
    def teardown_method(self):self.s.doCleanups()
    def create(self,limit=80):
        spec=seq.preview(self.s.state,self.s.units,call_limit=30,review_point_limit=limit)
        run=seq.create_run(self.s.path,self.s.state,spec,'new-policy')
        root=copy.deepcopy(self.s.state);root.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared')
        save_state(self.s.path,root);return run
    def read(self,r):return seq.read_run(self.s.path,r['id'])
    def test_seed_append_and_readonly_history(self):
        r=self.create();self.s.step(r,0);first=self.read(r)
        assert len(first['working']['tracks'])==2
        assert not first['working']['unassigned_ids']
        assert not self.s.gateway.contexts
        self.s.step(r,1);second=self.read(r)
        assert len(second['working']['continuation_graph']['edges'])==2
        assert {e['status'] for e in second['working']['continuation_graph']['edges']}=={'low_confidence'}
        ctx=second['steps'][-1]['context']
        assert ctx['editable_observation_ids']==[]
        assert len(ctx['observations'])==2
        assert ctx['continuation_policy']['mode']=='tentative_append'
        self.s.step(r,2);third=self.read(r)
        assert set(third['working']['tracks'])==set(first['working']['tracks'])
        assert third['steps'][-1]['context']['editable_observation_ids']==[]
        assert len(third['working']['continuation_revisions'])==3
        assert self.s.read(self.s.path)['tracks']=={}
    def suspicious(self):
        for p in self.s.state['points']:
            if p['candidate_id'][-1]=='1':p['bearing_deg']=60.
            if p['candidate_id'][-1]=='2':p['bearing_deg']=61.
        save_state(self.s.path,self.s.state)
    def test_local_revision_retracts_early_edge_without_future(self):
        self.suspicious();r=self.create();self.s.step(r,0);self.s.step(r,1)
        before=self.read(r);frame,ctx,_=seq.frame_for_step(before)
        assert ctx['continuation_policy']['mode']=='local_review'
        assert len(ctx['editable_observation_ids'])==4
        assert 'h3' not in json.dumps(ctx)
        rows=[]
        for i,(a,_) in enumerate([('h','hull'),('w','tow')]):
            rows.extend([{'track_ref':f'F00{i+1}','observation_ids':[frame['aliases'][a+'0']],'reason':'keep start'},
                         {'track_ref':f'N{i+1}','observation_ids':[frame['aliases'][a+'1'],frame['aliases'][a+'2']],'reason':'new evidence split'}])
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':rows})
        before['steps'].append({});seq.apply_returned(before,frame,ctx,raw)
        change=before['steps'][-1]['revision']
        assert len(change['withdrawn_edges'])==2
        assert len(before['working']['tracks'])==4
        assert self.s.read(self.s.path)['tracks']=={}
    def test_scope_budget_stops_before_paid_call(self):
        self.suspicious();r=self.create(limit=1);self.s.step(r,0);self.s.step(r,1)
        calls=self.s.read(self.s.path)['calls']
        import pytest
        with pytest.raises(ValueError,match='超过上限'):self.s.step(r,2)
        assert self.s.read(self.s.path)['calls']==calls
        assert self.read(r)['cursor']==2
    def test_cannot_assign_readonly_point_to_new_fragment(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N9','observation_ids':[frame['aliases']['h0'],frame['aliases']['h1']],'reason':'illegal old edit'}]})
        import pytest
        saved['steps'].append({})
        with pytest.raises(ValueError,match='未在本轮提交'):seq.apply_returned(saved,frame,ctx,raw)
    def test_request_review_never_uses_future(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        parsed=tc.review_requests({'review_requests':[{'observation_ids':[frame['aliases']['h0']],'reason':'later evidence'}]},frame,ctx,80)
        assert parsed[0]['candidate_ids']==['h0']
        frame['pending_review_requests']=parsed
        plan=tc.plan(frame,['h1','w1'],tc.DEFAULTS)
        assert frame['aliases']['h0'] in plan['issues'][0]['observation_ids']
        import pytest
        with pytest.raises(ValueError):tc.review_requests({'review_requests':[{'observation_ids':['O999999'],'reason':'future'}]},frame,ctx,80)

    def test_explicit_end_review_requires_new_digest_and_no_extra_model_call(self):
        r=self.create()
        for i in range(6):self.s.step(r,i)
        saved=self.read(r);saved['working']['pending_review_requests']=[{'candidate_ids':['h0'],'reason':'review early edge'}]
        seq.write_run(self.s.path,saved);old_digest=saved['spec']['context_digest'];calls=len(self.s.gateway.contexts)
        import pytest
        with pytest.raises(ValueError,match='仍有Agent请求'):seq.review_run(self.s.path,r['id'],True,self.s.read,save_state)
        prepared=seq.extend_review(self.s.path,r['id'],self.s.read)
        assert prepared['status']=='paused' and prepared['cursor']==6
        assert prepared['spec']['context_digest']!=old_digest
        assert len(self.s.gateway.contexts)==calls
        self.s.step(r,6);finished=self.read(r)
        assert finished['cursor']==7 and not finished['working']['pending_review_requests']
        assert finished['steps'][-1]['context']['sequential_step']['new_unit_count']==0
        assert len(self.s.gateway.contexts)==calls+1
    def test_sparse_first_pair_may_append_despite_no_identity_target(self):
        self.suspicious();r=self.create();self.s.step(r,0);self.s.step(r,1)
        w=self.read(r)['working']
        assert len(w['continuation_graph']['edges'])==2
        assert not w['targets']
        assert not w['unassigned_ids']
    def test_future_raw_source_never_enters_context_and_state_not_mutated(self):
        r=self.create();original=copy.deepcopy(self.s.state['points'])
        self.s.step(r,0);self.s.step(r,1)
        ctx=self.read(r)['steps'][-1]['context'];text=json.dumps(ctx)
        assert 'SECRET_SOURCE' not in text
        visible={self.s.state['aliases'][c] for group in self.s.units[:2] for c in group}
        assert {p['observation_id'] for p in ctx['observations']}<=visible
        assert {p['observation_id'] for t in ctx['tracks'].values() for p in t['history']}<=visible
        assert self.s.read(self.s.path)['points']==original

    def test_agent_request_is_saved_but_not_immediately_called(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[], 'review_requests':[{'observation_ids':[frame['aliases']['h0']],'reason':'history needs review'}]})
        saved['steps'].append({});seq.apply_returned(saved,frame,ctx,raw)
        assert saved['working']['pending_review_requests'][0]['candidate_ids']==['h0']
        assert not self.s.gateway.contexts
        _,following,_=seq.frame_for_step(saved)
        assert any(i['code']=='agent_requested_review' for i in following['continuation_policy']['issues'])
        assert frame['aliases']['h0'] in following['editable_observation_ids']

    def test_suspect_edges_remain_explicit_on_next_group(self):
        self.suspicious();r=self.create();self.s.step(r,0);self.s.step(r,1)
        saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        # Retain the suspicious prior edge, leave the offending new point pending.
        rows=[{'track_ref':f'F00{i+1}','observation_ids':[frame['aliases'][a+'0'],frame['aliases'][a+'1']],'reason':'keep provisional'} for i,a in enumerate(('h','w'))]
        saved['steps'].append({});seq.apply_returned(saved,frame,ctx,json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':rows}))
        assert {e['status'] for e in saved['working']['continuation_graph']['edges']}=={'suspect'}
        _,following,_=seq.frame_for_step(saved)
        assert any(i['code']=='open_suspect_edge' for i in following['continuation_policy']['issues'])

    def test_explicit_retain_resolves_doubt_without_confirming_identity(self):
        self.suspicious();r=self.create();self.s.step(r,0);self.s.step(r,1)
        saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        rows=[{'track_ref':f'F00{i+1}','observation_ids':[frame['aliases'][a+'0'],frame['aliases'][a+'1']],'reason':'retain weak edge'} for i,a in enumerate(('h','w'))]
        resolutions=[{'issue_id':i['issue_id'],'decision':'retain','reason':'reviewed, remains tentative'} for i in ctx['continuation_policy']['issues']]
        saved['steps'].append({});seq.apply_returned(saved,frame,ctx,json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':rows,'issue_resolutions':resolutions}))
        assert {e['status'] for e in saved['working']['continuation_graph']['edges']}=={'low_confidence'}
        assert not saved['working']['targets']

    def test_unknown_numeric_candidate_rejects_only_proposal_not_whole_step(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N1','candidate_ref':'R999999','reason':'invented candidate'},
                      {'track_ref':'F002','observation_ids':[frame['aliases']['w1']],'reason':'valid tow continuation'}],
            'target_hypotheses':[{'target_ref':'H1','fragment_refs':['N1','F002'],'pairing_ref':'recompute','reason':'must not fabricate target'}]})
        saved['steps'].append({});seq.apply_returned(saved,frame,ctx,raw)
        assert saved['cursor']==2 and saved['status']=='paused'
        assert 'h1' in saved['working']['unassigned_ids']
        assert [p['candidate_id'] for p in saved['working']['tracks']['T002']['history']]==['w0','w1']
        assert [p['candidate_id'] for p in saved['working']['tracks']['T001']['history']]==['h0']
        assert not saved['working']['targets']
        assert any(w['code']=='unknown_candidate_proposal' for w in saved['steps'][-1]['warnings'])
        assert saved['steps'][-1]['answer']==raw

    def test_invented_observation_is_still_strictly_rejected(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N1','observation_ids':['O999999'],'reason':'invented raw point'}]})
        import pytest
        saved['steps'].append({})
        with pytest.raises(ValueError,match='不存在'):seq.apply_returned(saved,frame,ctx,raw)

    def test_same_array_target_is_rejected_without_losing_valid_fragments(self):
        r=self.create();self.s.step(r,0);saved=self.read(r);frame,ctx,_=seq.frame_for_step(saved)
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N1','observation_ids':[frame['aliases']['h1']],'reason':'tentative fragment'},
                      {'track_ref':'N2','observation_ids':[frame['aliases']['w1']],'reason':'tentative fragment'}],
            'target_hypotheses':[{'target_ref':'H1','fragment_refs':['F001','N1'],'pairing_ref':'recompute','reason':'wrong hull+hull'},
                                 {'target_ref':'H2','fragment_refs':['F002','N2'],'pairing_ref':'recompute','reason':'wrong tow+tow'}]})
        saved['steps'].append({});seq.apply_returned(saved,frame,ctx,raw)
        assert saved['cursor']==2 and saved['status']=='paused'
        assert len(saved['working']['tracks'])==4
        assert not saved['working']['targets']
        assert len([w for w in saved['steps'][-1]['warnings'] if w['code']=='invalid_cross_array_target'])==2
        assert saved['steps'][-1]['answer']==raw
