import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.models.model_settings import ModelSettings
from backend.app.models.association_wire import encode_context,decode_context
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import sequential_runner as seq


def data():
    return {'target_catalog':[{'target_id':'SECRET_SOURCE','candidate_ids':[f'{a}{i}' for i in range(6) for a in ('h','w')]}],
        'candidates':[{'candidate_id':f'{a}{i}','t':i*10,'platform_id':'A','array':array,'bearing_deg':40+i,'quality':'fitted'}
                      for i in range(6) for a,array in [('h','hull'),('w','tow')]]}


class FakeGateway:
    def __init__(self):self.contexts=[]
    def invoke(self,images,prompt,*args):
        ctx=json.loads(prompt.split('\n',1)[1]);self.contexts.append(ctx);tracks=[]
        for i,array in enumerate(('hull','tow'),1):
            points=[p for p in ctx['observations'] if p['array']==array]
            if not points:continue
            old=next((tid for tid,t in ctx['tracks'].items() if t['history'] and t['history'][0]['array']==array),None)
            tracks.append({'track_ref':'F'+old[1:] if old else f'N{i}','observation_ids':[p['observation_id'] for p in points],'reason':'offline test'})
        return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':tracks})}


class SequentialRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'state.json'
        self.state=create_mixed(data(),max_calls=30);save_state(self.path,self.state)
        self.settings=ModelSettings('offline','http://invalid.local','offline');self.gateway=FakeGateway()
        self.units=[[f'h{i}',f'w{i}'] for i in range(6)]

    def read(self,path):return json.loads(Path(path).read_text(encoding='utf-8'))
    def create(self,units=None):
        spec=seq.preview(self.state,units or self.units,call_limit=30,continuation_policy='legacy-fixed-window')
        run=seq.create_run(self.path,self.state,spec,'test-request')
        root=copy.deepcopy(self.state);root.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(self.path,root)
        return run
    def step(self,run,cursor):return seq.execute_one(self.path,run['id'],cursor,self.settings,self.gateway,self.read,save_state)

    def test_prefix_only_one_new_unit_and_lossless_wire(self):
        run=self.create();original=copy.deepcopy(self.state['points'])
        for i in range(6):
            self.step(run,i);saved=seq.read_run(self.path,run['id']);context=saved['steps'][-1]['context']
            new=context['sequential_step']['new_observation_ids'];self.assertEqual(len(new),2)
            self.assertEqual(context['sequential_step']['new_unit_count'],1)
            self.assertEqual(decode_context(encode_context(context)),context)
            self.assertNotIn('SECRET_SOURCE',json.dumps(context))
            allowed={self.state['aliases'][cid] for ids in self.units[:i+1] for cid in ids}
            mentioned={p['observation_id'] for p in context['observations']}|{p['observation_id'] for t in context['tracks'].values() for p in t['history']}
            self.assertLessEqual(mentioned,allowed)
            self.assertEqual(self.read(self.path)['tracks'],{})
            self.assertTrue(all(r['review_status']=='provisional' for r in saved['working']['rounds']))
        self.assertEqual(len(self.gateway.contexts),5)
        self.assertEqual(self.read(self.path)['calls'],5)
        self.assertEqual(self.read(self.path)['points'],original)
        state=seq.review_run(self.path,run['id'],True,self.read,save_state)
        self.assertEqual(state['status'],'ready');self.assertEqual(len(state['submitted_ids']),12)
        self.assertEqual(state['calls'],5);self.assertEqual(state['plot_status'],'awaiting_model')

    def test_stale_cursor_never_repeats_a_call(self):
        run=self.create();self.step(run,0)
        with self.assertRaises(ValueError):self.step(run,0)
        self.assertEqual(self.gateway.contexts,[])

    def test_mixed_array_proposal_is_quarantined_and_next_step_can_continue(self):
        run=self.create();self.step(run,0)
        class MixedReply:
            def invoke(inner,images,prompt,*args):
                ctx=json.loads(prompt.split('\n',1)[1])
                self.assertEqual({g['array'] for g in ctx['fragment_stream_groups']},{'hull','tow'})
                self.assertIn('不能合放N1',prompt)
                return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                    'tracks':[{'track_ref':'N1','observation_ids':[p['observation_id'] for p in ctx['observations']],'reason':'invalid merged pair'}]})}
        seq.execute_one(self.path,run['id'],1,self.settings,MixedReply(),self.read,save_state)
        saved=seq.read_run(self.path,run['id'])
        self.assertEqual(saved['cursor'],2);self.assertEqual(saved['status'],'paused')
        self.assertFalse(saved['working']['tracks'])
        self.assertEqual(set(saved['working']['unassigned_ids']),set(sum(self.units[:2],[])))
        self.assertTrue(any(w['code']=='mixed_stream_fragment' for w in saved['steps'][-1]['warnings']))
        self.assertEqual(json.loads(saved['steps'][-1]['answer'])['tracks'][0]['track_ref'],'N1')
        self.step(run,2);self.assertEqual(seq.read_run(self.path,run['id'])['cursor'],3)
        self.assertEqual(self.read(self.path)['tracks'],{})

    def test_pre_upgrade_mixed_stream_reply_can_be_rechecked_without_model(self):
        run=self.create();self.step(run,0)
        saved=seq.read_run(self.path,run['id']);frame,ctx,_=seq.frame_for_step(saved)
        ctx.pop('fragment_stream_groups')
        raw=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N1','observation_ids':[p['observation_id'] for p in ctx['observations']],'reason':'old merged proposal'}]})
        saved['status']='failed';saved['calls']=1
        saved['steps'].append({'index':2,'status':'invalid_response','source':'model','context':ctx,'answer':raw,'error':'old cross-array validation'})
        seq.write_run(self.path,saved)
        root=self.read(self.path);root.update(calls=1,status='sequential_failed');save_state(self.path,root)
        result=seq.recover_returned(self.path,run['id'],self.read,save_state)
        self.assertEqual(result['cursor'],2);self.assertEqual(result['calls'],1)
        self.assertFalse(self.gateway.contexts)
        self.assertEqual(seq.read_run(self.path,run['id'])['steps'][-1]['answer'],raw)
        self.assertFalse(seq.read_run(self.path,run['id'])['working']['tracks'])

    def test_pause_between_scheduler_check_and_step_reservation_wins(self):
        run=self.create();saved=seq.read_run(self.path,run['id']);saved['pause_requested']=True;seq.write_run(self.path,saved)
        with self.assertRaises(ValueError):self.step(run,0)
        self.assertEqual(seq.read_run(self.path,run['id'])['cursor'],0)
        self.assertEqual(self.read(self.path)['calls'],0)

    def test_partial_unit_keeps_both_members_but_only_one_is_new(self):
        self.state['submitted_ids']=['h0'];self.state['unassigned_ids']=['h0']
        run=self.create([self.units[0]]);_,context,_=seq.frame_for_step(run)
        self.assertEqual(len(context['sequential_step']['unit_observation_ids']),2)
        self.assertEqual(context['sequential_step']['new_observation_ids'],[self.state['aliases']['w0']])
        self.assertEqual(len(context['current_unit_observations']),2)
        self.assertFalse(run['spec']['free_initial_seed'])

    def test_working_view_shows_seed_and_only_validated_prefix(self):
        run=self.create()
        self.assertEqual(seq.public_run(run)['working_view']['points'],[])
        self.step(run,0);saved=seq.read_run(self.path,run['id']);view=seq.public_run(saved)['working_view']
        self.assertEqual(len(view['units']),1);self.assertEqual(len(view['points']),2)
        self.assertEqual(view['units'][0]['candidate_ids'],self.units[0])
        self.assertTrue(all(m['status']=='unassigned' for m in view['membership'].values()))
        self.step(run,1)
        with patch.object(self.gateway,'invoke',return_value={'answer':'invalid response'}):
            with self.assertRaises(ValueError):self.step(run,2)
        saved=seq.read_run(self.path,run['id']);view=seq.public_run(saved)['working_view']
        self.assertEqual(saved['status'],'failed');self.assertEqual(view['validated_cursor'],2)
        self.assertEqual(len(view['units']),3);self.assertEqual(view['pending_unit_count'],1)
        self.assertEqual({p['candidate_id'] for p in view['points']},{cid for u in self.units[:3] for cid in u})
        self.assertEqual(view['membership']['h2']['status'],'pending_validation')
        self.assertEqual(sum(u['validated'] for u in view['units']),2)
        self.assertNotIn('h3',view['membership']);self.assertEqual(self.read(self.path)['tracks'],{})
        progress=seq.public_run(saved)['working_view']['progress']
        self.assertEqual((progress['validated_steps'],progress['pending_steps'],progress['unstarted_steps']),(2,1,3))
        self.assertEqual(progress['queued_raw_observations'],12)
        self.assertEqual(progress['visible_raw_observations'],6)

    def test_working_view_carries_history_across_single_group_runs(self):
        run=self.create([self.units[0]]);self.step(run,0)
        self.state=seq.review_run(self.path,run['id'],True,self.read,save_state)
        self.assertEqual(len(self.state['sequential_units']),1)
        spec=seq.preview(self.state,[self.units[1]],call_limit=1)
        second=seq.create_run(self.path,self.state,spec,'second-request')
        root=copy.deepcopy(self.state);root.update(active_sequential_run=second['id'],version=second['root_version'],status='sequential_prepared');save_state(self.path,root)
        self.step(second,0);view=seq.public_run(seq.read_run(self.path,second['id']))['working_view']
        self.assertEqual(len(view['units']),2)
        self.assertEqual(sum(u['current_run'] for u in view['units']),1)
        self.assertEqual(len(view['points']),4)
        self.assertEqual(view['membership']['h0']['fragment_id'],view['membership']['h1']['fragment_id'])
        self.assertIsNotNone(view['membership']['h0']['fragment_id'])
        self.assertEqual(len({u['unit_id'] for u in view['units']}),2)

    def test_later_group_can_revise_prior_provisional_assignment(self):
        run=self.create();self.step(run,0);self.step(run,1)
        before=seq.read_run(self.path,run['id']);frame,ctx,_=seq.frame_for_step(before)
        self.assertEqual(ctx['sequential_step']['revisited_provisional_units'],2)
        self.assertIn(self.state['aliases']['h0'],{p['observation_id'] for p in ctx['observations']})
        answer=json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
            'tracks':[{'track_ref':'N9','observation_ids':[self.state['aliases'][c] for c in ('h0','h2')],'reason':'new evidence'},
                      {'track_ref':'N10','observation_ids':[self.state['aliases']['h1']],'reason':'competing prior point'}]})
        before['steps'].append({});seq.apply_returned(before,frame,ctx,answer)
        histories=[{p['candidate_id'] for p in t['history']} for t in before['working']['tracks'].values()]
        self.assertIn({'h0','h2'},histories);self.assertIn({'h1'},histories)
        self.assertEqual(self.read(self.path)['tracks'],{})

    def test_unknown_result_never_auto_retries(self):
        run=self.create();self.step(run,0)
        with patch.object(self.gateway,'invoke',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):self.step(run,1)
        saved=seq.read_run(self.path,run['id']);self.assertEqual(saved['status'],'result_unknown')
        self.assertEqual(self.read(self.path)['calls'],1)
        with self.assertRaises(ValueError):self.step(run,1)

    def test_original_returned_reply_free_recovery(self):
        run=self.create();self.step(run,0)
        with patch.object(seq,'validate_mixed',side_effect=ValueError('local validator test failure')):
            with self.assertRaises(ValueError):self.step(run,1)
        saved=seq.read_run(self.path,run['id']);original=saved['steps'][-1]['answer']
        recovered=seq.recover_returned(self.path,run['id'],self.read,save_state)
        self.assertEqual(recovered['cursor'],2);self.assertEqual(recovered['calls'],1)
        self.assertEqual(recovered['steps'][-1]['answer'],original);self.assertEqual(len(self.gateway.contexts),1)

    def test_reject_keeps_raw_and_confirmed_state(self):
        run=self.create();self.step(run,0);self.step(run,1)
        state=seq.review_run(self.path,run['id'],False,self.read,save_state)
        self.assertEqual(state['points'],self.state['points']);self.assertEqual(state['tracks'],{})
        self.assertEqual(state['calls'],1);self.assertEqual(state['status'],'ready')

    def test_budget_group_validation_and_binding(self):
        with self.assertRaises(ValueError):seq.preview(self.state,self.units,call_limit=2)
        with self.assertRaises(ValueError):seq.preview(self.state,[['h0','w1']])
        with self.assertRaises(ValueError):seq.preview(self.state,[['h0'],['h0']])
        run=self.create();again=seq.create_run(self.path,self.state,run['spec'],'test-request');self.assertEqual(run['id'],again['id'])
        changed=seq.preview(self.state,list(reversed(self.units)),order='received',call_limit=30)
        with self.assertRaises(ValueError):seq.create_run(self.path,self.state,changed,'test-request')

    def test_new_received_unselected_facts_survive_review(self):
        run=self.create([self.units[0]]);self.step(run,0)
        root=self.read(self.path);root['points'].append({'candidate_id':'late','t':999,'platform_id':'A','array':'hull','bearing_deg':80});root['aliases']['late']='O999999';save_state(self.path,root)
        state=seq.review_run(self.path,run['id'],True,self.read,save_state)
        self.assertIn('late',state['aliases']);self.assertNotIn('late',state['submitted_ids'])

    def test_invalid_model_future_reference_is_rejected(self):
        run=self.create();self.step(run,0)
        class FutureGateway:
            def invoke(inner,images,prompt,*args):
                ctx=json.loads(prompt.split('\n',1)[1]);return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[{'track_ref':'N1','observation_ids':[self.state['aliases']['h5']],'reason':'bad'}]})}
        self.gateway=FutureGateway()
        with self.assertRaises(ValueError):self.step(run,1)
        self.assertEqual(seq.read_run(self.path,run['id'])['status'],'failed')

    def bound_state(self):
        lookup={p['candidate_id']:p for p in self.state['points']}
        self.state['version']=4;self.state['submitted_ids']=['h0','w0','h1','w1']
        self.state['tracks']={f'T{i+1:03d}':{'history':[lookup[cid]],'prediction':[{'t':999,'bearing_deg':60}],'reason':'prior','status':'hypothesis'} for i,cid in enumerate(self.state['submitted_ids'])}
        self.state['targets']={f'T{i+1:03d}':{'legacy_track_ids':[f'T{2*i+1:03d}',f'T{2*i+2:03d}'],'fragment_ids':[f'F{2*i+1:03d}',f'F{2*i+2:03d}'],'reason':'prior','review_status':'adopted','evidence':{'eligible_for_spatial':False}} for i in range(2)}
        save_state(self.path,self.state)

    def test_bound_target_cannot_modify_other_tracks(self):
        self.bound_state();binding={'entity_type':'physical_target','target_id':'T001','revision':4}
        spec=seq.preview(self.state,[self.units[2]],target_binding=binding)
        run=seq.create_run(self.path,self.state,spec,'bound-request');root=copy.deepcopy(self.state);root.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(self.path,root)
        frame,ctx,_=seq.frame_for_step(run)
        def reply(other=False):
            return json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                'tracks':[{'track_ref':'F003' if other else 'F001','observation_ids':[self.state['aliases']['h2']],'reason':'test'},
                          {'track_ref':'F002','observation_ids':[self.state['aliases']['w2']],'reason':'test'}],
                'target_hypotheses':[] if other else [{'target_ref':'T001','fragment_refs':['F001','F002'],'pairing_ref':'recompute','reason':'test'}]})
        run['steps']=[{}]
        with self.assertRaises(ValueError):seq.apply_returned(copy.deepcopy(run),frame,ctx,reply(True))
        seq.apply_returned(run,frame,ctx,reply())
        self.assertEqual(run['working']['tracks']['T003'],self.state['tracks']['T003'])
        self.assertEqual(run['working']['targets']['T002'],self.state['targets']['T002'])
        with self.assertRaises(ValueError):seq.preview(self.state,[self.units[2]],target_binding={**binding,'revision':0})
        with self.assertRaises(ValueError):seq.preview(self.state,[self.units[2]],target_binding={**binding,'entity_type':'fragment'})

