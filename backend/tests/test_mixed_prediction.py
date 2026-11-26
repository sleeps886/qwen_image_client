import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association.mixed_prediction import (
    create_mixed,prepare_mixed,validate_mixed,update_mixed,predict_mixed,review_mixed,public_mixed)


def dataset():
    return {'target_catalog':[{'target_id':'A038','candidate_ids':['A038_0','A038_1']}],
        'candidates':[{'candidate_id':f'{label}_{i}','t':i*10,'bearing_deg':base+i,
            'platform_id':'P','array':'hull','quality':'fitted','mixed':False,'sigma_deg':1.2}
            for label,base in [('A038',30),('A031',110)] for i in range(6)]}


def answer(state,ctx,tracks=(),ambiguous=(),unassigned=()):
    alias=state['aliases']
    return json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
        'tracks':[{'track_ref':tid,'observation_ids':[alias[cid] for cid in ids],'reason':'时间方位趋势一致，暂定假设'} for tid,ids in tracks],
        'ambiguous':[{'observation_id':alias[cid],'possible_tracks':refs,'reason':'交叉证据不足'} for cid,refs in ambiguous],
        'unassigned':[alias[cid] for cid in unassigned]})


def apply(state,ids,tracks=(),ambiguous=(),unassigned=()):
    ctx,_=prepare_mixed(state,ids)
    validated=validate_mixed(state,ctx,answer(state,ctx,tracks,ambiguous,unassigned))
    return update_mixed(state,ctx,validated)


from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
from backend.tests.model_plot_fixture import plot_answer


def finish_fake_plot(state):
    context,_=prepare_model_plot(state);pending=copy.deepcopy(state);pending['calls']+=1
    return apply_model_plot(pending,context,json.dumps(plot_answer(context)))


def adopt_with_fake_plot(state):
    return finish_fake_plot(review_mixed(state,state['version'],True))


class MixedTests(unittest.TestCase):
    def setUp(self):self.state=create_mixed(dataset())
    def test_empty_initial_state_no_seeds_or_track(self):
        self.assertEqual(self.state['tracks'],{});self.assertEqual(self.state['submitted_ids'],[])
    def test_shuffle_and_directory_hidden(self):
        ids=['A038_2','A031_0','A038_0'];ctx,prompt=prepare_mixed(self.state,ids,'比较A038和A038_0')
        changed=dataset();random.Random(2).shuffle(changed['candidates'])
        other=create_mixed(changed)
        self.assertEqual((ctx,prompt),prepare_mixed(other,list(reversed(ids)),'比较A038和A038_0'))
        self.assertNotIn('A038',prompt);self.assertNotIn('A031',prompt);self.assertNotIn('target_catalog',prompt)
        self.assertIn('sigma_deg',prompt);self.assertEqual([p['t'] for p in ctx['observations']],[0,0,20])
    def test_only_explicit_selection_no_future(self):
        ctx,prompt=prepare_mixed(self.state,['A038_0'])
        self.assertEqual(len(ctx['observations']),1);self.assertNotIn('50.0',prompt)
        changed=dataset();changed['candidates'][-1]['bearing_deg']=170
        self.assertEqual(prepare_mixed(create_mixed(changed),['A038_0']), (ctx,prompt))
    def test_duplicate_selection_deduplicated(self):
        ctx,_=prepare_mixed(self.state,['A038_0','A038_0']);self.assertEqual(len(ctx['observations']),1)
    def test_singleton_no_directional_prediction(self):
        new=apply(self.state,['A038_0'],[('N9',['A038_0'])]);self.assertEqual(new['tracks']['T001']['prediction'],[])
    def test_dynamic_three_tracks_not_fixed_count(self):
        ids=['A038_0','A031_0','A038_1'];new=apply(self.state,ids,[(f'N{i}',[cid]) for i,cid in enumerate(ids)])
        self.assertEqual(len(new['tracks']),3)
    def test_all_unassigned_zero_tracks(self):
        new=apply(self.state,['A038_0','A031_0']);self.assertFalse(new['tracks']);self.assertEqual(len(new['unassigned_ids']),2)
    def test_time_gaps_use_real_time(self):
        new=apply(self.state,['A038_0','A038_4'],[('N1',['A038_4','A038_0'])]);new=adopt_with_fake_plot(new);t=new['tracks']['T001']
        self.assertEqual(len(t['history']),2);self.assertAlmostEqual(t['prediction'][-1]['bearing_deg'],36)
    def test_unassigned_reconsidered_without_future(self):
        new=apply(self.state,['A038_0']);new=adopt_with_fake_plot(new)
        ctx,_=prepare_mixed(new,['A038_1']);self.assertEqual(len(ctx['observations']),2)
    def test_occupancy_quarantine_preserves_original_state(self):
        before=copy.deepcopy(self.state);ctx,_=prepare_mixed(self.state,['A038_0'])
        checked=validate_mixed(self.state,ctx,answer(self.state,ctx,[('N1',['A038_0']),('N2',['A038_0'])]))
        self.assertFalse(checked['tracks'])
        self.assertEqual(checked['unassigned_ids'],['A038_0'])
        self.assertEqual(before,self.state)
    def test_unopened_reference_rejected(self):
        ctx,_=prepare_mixed(self.state,['A038_0'])
        with self.assertRaises(ValueError):validate_mixed(self.state,ctx,answer(self.state,ctx,[('N1',['A038_5'])]))
    def test_wrong_version_rejected(self):
        ctx,_=prepare_mixed(self.state,['A038_0']);raw=json.loads(answer(self.state,ctx));raw['step_id']=False
        with self.assertRaises(ValueError):validate_mixed(self.state,ctx,json.dumps(raw))
    def test_same_stream_same_time_conflict(self):
        ctx,_=prepare_mixed(self.state,['A038_0','A031_0'])
        result=validate_mixed(self.state,ctx,answer(self.state,ctx,[('N1',['A038_0','A031_0'])]))
        self.assertEqual(result['tracks'],{})
        self.assertEqual(set(result['unassigned_ids']),{'A038_0','A031_0'})
        self.assertEqual(len(result['warnings']),2)
    def test_synchronous_arrays_not_forced_pair(self):
        data=dataset();data['candidates'][6]['array']='tow';s=create_mixed(data);ctx,_=prepare_mixed(s,['A038_0','A031_0'])
        checked=validate_mixed(s,ctx,answer(s,ctx,[('N1',['A038_0','A031_0'])]))
        self.assertFalse(checked['tracks']);self.assertFalse(checked['targets'])
        self.assertEqual(set(checked['unassigned_ids']),{'A038_0','A031_0'})
        self.assertTrue(all(w['code']=='mixed_stream_fragment' for w in checked['warnings']))
        new=apply(s,['A038_0','A031_0'],[('N1',['A038_0']),('N2',['A031_0'])]);self.assertEqual(len(new['tracks']),2)

    def test_mixed_stream_proposal_preserves_old_track_and_disjoint_valid_row(self):
        data=dataset();data['candidates'][6]['array']='tow';s=create_mixed(data)
        s=review_mixed(apply(s,['A038_0'],[('N1',['A038_0'])]),1,True)
        s['status']='ready'
        ctx,_=prepare_mixed(s,['A031_0','A038_1'])
        before=copy.deepcopy(s)
        result=validate_mixed(s,ctx,answer(s,ctx,[('F001',['A031_0']),('N2',['A038_1'])]))
        self.assertEqual(s,before)
        self.assertEqual([p['candidate_id'] for p in result['tracks']['T001']['history']],['A038_0'])
        self.assertIn('A031_0',result['unassigned_ids'])
        self.assertTrue(any([p['candidate_id'] for p in t['history']]==['A038_1'] for t in result['tracks'].values()))
    def test_numeric_gate_cannot_be_overruled_by_reason(self):
        ids=['A038_0','A038_1','A031_2'];ctx,_=prepare_mixed(self.state,ids)
        validated=validate_mixed(self.state,ctx,answer(self.state,ctx,[('N1',ids)]))
        self.assertEqual(validated['unassigned_ids'],['A031_2'])
        self.assertEqual(len(validated['tracks']['T001']['history']),2)
        self.assertTrue(validated['warnings'])
        new=update_mixed(self.state,ctx,validated);new=adopt_with_fake_plot(new)
        self.assertTrue(new['tracks']['T001']['prediction'])
    def test_ambiguity_retained_and_competing_predictions_suppressed(self):
        s=apply(self.state,['A038_0','A038_1','A031_0','A031_1'],[('N1',['A038_0','A038_1']),('N2',['A031_0','A031_1'])]);s=adopt_with_fake_plot(s)
        new=apply(s,['A038_2'],ambiguous=[('A038_2',['T001','T002'])]);self.assertEqual(len(new['ambiguities']),1)
        self.assertFalse(new['tracks']['T001']['prediction']);self.assertNotIn('A038_2',[p['candidate_id'] for p in new['tracks']['T001']['history']])
    def test_later_evidence_can_resolve_pending_association(self):
        s=apply(self.state,['A038_0','A038_1','A031_0','A031_1'],[('N1',['A038_0','A038_1']),('N2',['A031_0','A031_1'])]);s=adopt_with_fake_plot(s)
        s=apply(s,['A038_2'],ambiguous=[('A038_2',['T001','T002'])]);s=adopt_with_fake_plot(s)
        new=apply(s,['A038_3'],[('T001',['A038_2','A038_3'])])
        self.assertFalse(new['ambiguities']);self.assertEqual(len(new['tracks']['T001']['history']),4)
        new=adopt_with_fake_plot(new);self.assertTrue(new['tracks']['T001']['prediction'])
    def test_history_replay_keeps_identity_and_unaffected_prefix(self):
        s=apply(self.state,['A038_0','A038_2','A038_4'],[('N1',['A038_0','A038_2','A038_4'])]);s=adopt_with_fake_plot(s)
        ctx,_=prepare_mixed(s,['A038_1'])
        self.assertEqual(ctx['affected_from'],10)
        self.assertEqual([p['t'] for p in ctx['tracks']['T001']['history']],[0])
        new=apply(s,['A038_1'],[('T001',['A038_1','A038_2','A038_4'])])
        self.assertEqual(list(new['tracks']),['T001']);self.assertEqual([p['t'] for p in new['tracks']['T001']['history']],[0,10,20,40])
    def test_invalid_ambiguity_reference(self):
        ctx,_=prepare_mixed(self.state,['A038_0'])
        validated=validate_mixed(self.state,ctx,answer(self.state,ctx,ambiguous=[('A038_0',['T001','T002'])]))
        self.assertEqual(validated['unassigned_ids'],['A038_0']);self.assertFalse(validated['ambiguities'])
    def test_review_rejection_restores_every_pool_and_preserves_ledger(self):
        s=apply(self.state,['A038_0','A038_1'],[('N1',['A038_0','A038_1'])]);new=review_mixed(s,s['version'],False,'错误关联')
        for key in ('tracks','submitted_ids','unassigned_ids','ambiguities','next_track'):self.assertEqual(new[key],self.state[key])
        self.assertFalse(new['prediction_ledger']);self.assertEqual(new['rounds'][-1]['review_status'],'rejected');self.assertEqual(new['version'],2)
    def test_historical_replay_keeps_old_forecast(self):
        s=apply(self.state,['A038_2','A038_4'],[('N1',['A038_2','A038_4'])]);s=adopt_with_fake_plot(s);old=copy.deepcopy(s['prediction_ledger'])
        ctx,_=prepare_mixed(s,['A038_0']);self.assertEqual(ctx['revision_mode'],'historical_replay');self.assertEqual(ctx['tracks']['T001']['history'],[])
        self.assertEqual(len(ctx['observations']),3)
        new=apply(s,['A038_0'],[('N1',['A038_0','A038_2','A038_4'])]);self.assertEqual(new['prediction_ledger'][:1],old)
        self.assertEqual(len(new['prediction_ledger']),1);self.assertEqual(new['status'],'awaiting_review')
        new=adopt_with_fake_plot(new)
        self.assertTrue(new['prediction_ledger'][-1]['historical_revision']);self.assertEqual(len(new['prediction_ledger']),2)
    def test_historical_reject_returns_prior_tracks(self):
        s=apply(self.state,['A038_2'],[('N1',['A038_2'])]);s=adopt_with_fake_plot(s)
        new=apply(s,['A038_0'],[('N1',['A038_0','A038_2'])]);new=review_mixed(new,new['version'],False,'保留旧版')
        self.assertEqual(s['tracks'],new['tracks']);self.assertEqual(len(new['prediction_ledger']),1)
    def test_repeated_submissions_and_pending_forbidden(self):
        s=apply(self.state,['A038_0']);
        with self.assertRaises(ValueError):prepare_mixed(s,['A038_1'])
        s=adopt_with_fake_plot(s)
        with self.assertRaises(ValueError):prepare_mixed(s,['A038_0'])
    def test_must_confirm_collections_before_any_prediction(self):
        s=apply(self.state,['A038_0','A038_1'],[('N1',['A038_0','A038_1'])])
        self.assertFalse(s['prediction_ledger']);self.assertFalse(s['tracks']['T001']['prediction'])
        self.assertFalse(public_mixed(s)['confirmed_tracks'])
        with self.assertRaises(ValueError):predict_mixed(s)
        confirmed=review_mixed(s,s['version'],True)
        self.assertFalse(confirmed['tracks']['T001']['prediction']);self.assertFalse(confirmed['prediction_ledger'])
        confirmed=finish_fake_plot(confirmed)
        self.assertTrue(confirmed['tracks']['T001']['prediction']);self.assertEqual(len(confirmed['prediction_ledger']),1)
        self.assertTrue(public_mixed(confirmed)['confirmed_tracks']);self.assertFalse(public_mixed(confirmed)['association_review_required'])
    def test_far_point_then_near_history_does_not_become_append_or_fake_past_forecast(self):
        data=dataset();data['candidates'].append({'candidate_id':'far','t':300,'bearing_deg':60,'platform_id':'P','array':'hull'})
        s=create_mixed(data)
        s=apply(s,['A038_0','A038_1','A038_2','far'],[('N1',['A038_0','A038_1','A038_2','far'])]);s=adopt_with_fake_plot(s)
        ledger=copy.deepcopy(s['prediction_ledger']);ctx,_=prepare_mixed(s,['A038_3'])
        self.assertEqual(ctx['revision_mode'],'historical_replay');self.assertEqual(ctx['affected_from'],30)
        self.assertEqual([p['t'] for p in ctx['tracks']['T001']['history']],[0,10,20])
        draft=apply(s,['A038_3'],[('T001',['A038_3','far'])])
        self.assertEqual(public_mixed(draft)['confirmed_tracks'],s['tracks']);self.assertEqual(draft['prediction_ledger'],ledger)
        confirmed=adopt_with_fake_plot(draft)
        self.assertEqual([p['t'] for p in confirmed['tracks']['T001']['history']],[0,10,20,30,300])
        self.assertEqual(confirmed['prediction_ledger'][:1],ledger)
        self.assertTrue(all(p['t']>300 for p in confirmed['tracks']['T001']['prediction']))
    def test_empty_ambiguity_does_not_block_other_targets(self):
        s=apply(self.state,['A038_0','A038_1','A031_0'],[('N1',['A038_0','A038_1'])],ambiguous=[('A031_0',[])])
        self.assertEqual(s['unassigned_ids'],['A031_0']);self.assertTrue(s['rounds'][-1]['warnings'])
        s=adopt_with_fake_plot(s);self.assertTrue(s['tracks']['T001']['prediction'])
    def test_rejected_second_collection_does_not_change_previous_ledger_or_graph(self):
        s=apply(self.state,['A038_0','A038_1'],[('N1',['A038_0','A038_1'])]);s=adopt_with_fake_plot(s)
        draft=apply(s,['A038_2'],[('T001',['A038_2'])])
        self.assertEqual(public_mixed(draft)['confirmed_tracks'],s['tracks'])
        rejected=review_mixed(draft,draft['version'],False,'集合关联需复核')
        self.assertEqual(rejected['prediction_ledger'],s['prediction_ledger']);self.assertEqual(rejected['tracks'],s['tracks'])
    def test_known_far_outlier_never_causes_backdated_future_predictions(self):
        data=dataset();data['candidates'].append({'candidate_id':'far_outlier','t':300,'bearing_deg':170,'platform_id':'P','array':'hull'})
        s=create_mixed(data)
        s=apply(s,['A038_0','A038_1','A038_2','far_outlier'],[('N1',['A038_0','A038_1','A038_2','far_outlier'])])
        self.assertIn('far_outlier',s['unassigned_ids'])
        s=adopt_with_fake_plot(s)
        self.assertFalse(s['tracks']['T001']['prediction']);self.assertEqual(s['tracks']['T001']['status'],'insufficient_evidence')
        self.assertEqual(s['prediction_ledger'][-1]['knowledge_cutoff'],300)
    def test_pending_revision_keeps_confirmed_unassigned_snapshot(self):
        s=apply(self.state,['A038_0','A038_1','A031_0'],[('N1',['A038_0','A038_1'])]);s=adopt_with_fake_plot(s)
        pending=apply(s,['A038_2'],[('T001',['A038_2'])])
        pending['unassigned_ids'].append('A038_0')  # Proposed revision must not rewrite the displayed confirmed version.
        view=public_mixed(pending)
        self.assertEqual(view['confirmed_unassigned_ids'],s['unassigned_ids'])
        self.assertNotIn('A038_0',view['confirmed_unassigned_ids'])
    def test_public_excludes_full_catalog(self):
        public=public_mixed(self.state)
        self.assertNotIn('points',public);self.assertNotIn('aliases',public);self.assertNotIn('source_labels',public)


class MixedApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(api,'MIXED_ROOT',Path(self.temp.name));self.root.start()
        self.client=TestClient(app,base_url="http://127.0.0.1");self.calls=0
        owner=self
        class Fake:
            def invoke(self,images,prompt,settings,directory,mode):
                owner.calls+=1;ctx=json.loads(prompt.split('\n',1)[1]);return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[],'unassigned':[],'ambiguous':[]})}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Fake()
        self.settings=patch.object(api,'model_settings',return_value=ModelSettings(api_key='test-placeholder',model='fake',base_url='http://localhost'));self.settings.start()
    def tearDown(self):
        app.dependency_overrides.clear();self.settings.stop();self.root.stop();self.temp.cleanup()
    def test_full_round_preview_digest_and_consent(self):
        r=self.client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()});self.assertEqual(r.status_code,200,r.text);sid=r.json()['session_id'];url=f'/api/incremental/mixed-sessions/{sid}'
        body={'version':0,'selected_ids':['A038_0','A031_0']};p=self.client.post(url+'/preview',json=body).json();self.assertEqual(p['model_calls'],0)
        self.assertEqual(self.client.post(url+'/step',json=body).status_code,400);self.assertEqual(self.calls,0)
        body.update(consent=True,context_digest='0'*64);self.assertEqual(self.client.post(url+'/step',json=body).status_code,400);self.assertEqual(self.calls,0)
        body['context_digest']=p['context_digest'];r=self.client.post(url+'/step',json=body);self.assertEqual(r.status_code,200,r.text);s=r.json();self.assertEqual(s['status'],'awaiting_review');self.assertEqual(self.calls,1)
        trace=self.client.get(url+'/execution').json();self.assertEqual(trace['status'],'waiting_for_user');self.assertEqual(len([n for n in trace['nodes'] if n['status']=='completed']),7)
        r=self.client.post(url+'/review',json={'version':1,'adopt':True});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'ready')
        self.assertEqual(len(self.client.get(url+'/catalog').json()['candidates']),12)
    def test_collection_confirmation_is_local_and_prediction_node_runs_only_after_confirm(self):
        owner=self
        class TrackingFake:
            def invoke(self,images,prompt,settings,directory,mode):
                owner.calls+=1;ctx=json.loads(prompt.split('\n',1)[1])
                if ctx.get('task')=='model_bearing_plot':return {'answer':json.dumps(plot_answer(ctx))}
                return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                    'tracks':[{'track_ref':'N1','observation_ids':[p['observation_id'] for p in ctx['observations']],'reason':'趋势支持'}]})}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:TrackingFake()
        sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()}).json()['session_id'];url=f'/api/incremental/mixed-sessions/{sid}'
        b={'version':0,'selected_ids':['A038_0','A038_1'],'consent':True};b['context_digest']=self.client.post(url+'/preview',json=b).json()['context_digest']
        pending=self.client.post(url+'/step',json=b).json()
        self.assertFalse(pending['tracks']['T001']['prediction']);self.assertFalse(pending['prediction_ledger']);self.assertFalse(pending['confirmed_tracks'])
        self.assertEqual(next(n for n in pending['execution']['nodes'] if n['node']=='predict')['status'],'waiting_for_user')
        self.assertEqual(self.calls,1)
        self.assertEqual(self.client.post(url+'/review',json={'version':1,'adopt':False,'reason':''}).status_code,400)
        r=self.client.post(url+'/review',json={'version':1,'adopt':True});self.assertEqual(r.status_code,200,r.text);confirmed=r.json()
        self.assertFalse(confirmed['tracks']['T001']['prediction']);self.assertEqual(len(confirmed['prediction_ledger']),0);self.assertEqual(self.calls,1)
        pb={'version':confirmed['version'],'consent':True};pb['context_digest']=self.client.post(url+'/plot-preview',json={'version':confirmed['version']}).json()['context_digest']
        r=self.client.post(url+'/plot-step',json=pb);self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.calls,2)
        self.assertEqual(r.json()['tracks']['T001']['plot_source'],'model')
        trace=self.client.get(url+'/execution').json();self.assertEqual(trace['status'],'completed')
        self.assertEqual(next(n for n in trace['nodes'] if n['node']=='plot_model')['status'],'completed')
        self.assertEqual(next(n for n in trace['nodes'] if n['node']=='plot_commit')['status'],'completed')
        self.assertEqual(self.client.post(url+'/review',json={'version':1,'adopt':True}).status_code,400)
        self.assertEqual(len(self.client.get(url).json()['prediction_ledger']),1)
    def failed_saved_reply(self,final_missing=False):
        sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()}).json()['session_id']
        path=api.mixed_path(sid);state=json.loads(path.read_text(encoding='utf-8'));ctx,_=prepare_mixed(state,['A038_0','A038_1'])
        raw=answer(state,ctx,[('N1',['A038_0','A038_1'])])
        if final_missing:
            bad=json.loads(raw);bad['tracks'][0]['observation_ids']=['O999999'];final=json.dumps(bad)
        else:final=raw
        saved=raw+'\nDraft explanation\n```json\n'+final+'\n```'
        state.update(status='invalid_response',calls=1);api.save_state(path,state)
        api.save_state(path.parent/'attempt_001.json',{'status':'returned','input':ctx,'answer':saved,'usage':{'total_tokens':12}})
        return sid,path
    def test_recover_saved_reply_does_not_call_model_or_predict(self):
        sid,path=self.failed_saved_reply();raw=(path.parent/'attempt_001.json').read_bytes()
        r=self.client.post(f'/api/incremental/mixed-sessions/{sid}/recover-association',json={'version':0})
        self.assertEqual(r.status_code,200,r.text);state=r.json()
        self.assertEqual(state['status'],'awaiting_review');self.assertEqual(state['calls'],1);self.assertEqual(self.calls,0)
        self.assertFalse(state['prediction_ledger']);self.assertFalse(state['tracks']['T001']['prediction'])
        self.assertTrue(state['rounds'][-1]['recovered_from_saved_reply'])
        self.assertEqual(state['rounds'][-1]['reply_format']['source'],'terminal_json_fence')
        self.assertEqual((path.parent/'attempt_001.json').read_bytes(),raw)
        self.assertEqual(self.client.post(f'/api/incremental/mixed-sessions/{sid}/recover-association',json={'version':0}).status_code,400)
    def test_recovery_cannot_bypass_reference_checks_or_choose_earlier_draft(self):
        sid,path=self.failed_saved_reply(final_missing=True);before=path.read_bytes()
        r=self.client.post(f'/api/incremental/mixed-sessions/{sid}/recover-association',json={'version':0})
        self.assertEqual(r.status_code,400);self.assertEqual(path.read_bytes(),before);self.assertEqual(self.calls,0)
    def test_unknown_call_is_not_recoverable(self):
        sid,path=self.failed_saved_reply();s=json.loads(path.read_text(encoding='utf-8'));s['status']='call_unknown';api.save_state(path,s)
        r=self.client.post(f'/api/incremental/mixed-sessions/{sid}/recover-association',json={'version':0})
        self.assertEqual(r.status_code,400);self.assertEqual(self.calls,0)
    def test_old_joint_page_redirects_to_same_workbench(self):
        r=self.client.get('/joint-prediction',follow_redirects=False)
        self.assertEqual(r.status_code,307);self.assertEqual(r.headers['location'],'/incremental')
    def test_failure_does_not_retry_or_commit(self):
        class Broken:
            def invoke(self,*args):raise RuntimeError('simulated timeout')
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Broken()
        sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()}).json()['session_id'];url=f'/api/incremental/mixed-sessions/{sid}'
        b={'version':0,'selected_ids':['A038_0'],'consent':True};b['context_digest']=self.client.post(url+'/preview',json=b).json()['context_digest']
        self.assertEqual(self.client.post(url+'/step',json=b).status_code,502)
        s=self.client.get(url).json();self.assertEqual(s['calls'],1);self.assertEqual(s['status'],'call_unknown');self.assertFalse(s['tracks'])
        self.assertEqual(self.client.post(url+'/preview',json=b).status_code,400)


