import asyncio
import copy
import json
import random
import time
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.api.routers import live_tracking as api
from backend.app.models.live_tracking import PrepareLive, AuthorizeLive
from backend.app.models.model_settings import ModelSettings
from backend.app.repositories.live_observation_repository import LiveRepository, LiveConflict
from backend.app.services.association.live_tracking import prepare_live, validate_association, validate_prediction
from backend.app.services.association.incremental_evidence import evidence
from backend.app.services.evaluation.live_tracking_metrics import evaluate_memberships
from backend.app.services.jobs.live_analysis_jobs import execute, events


def point(i, t=None, array='hull', bearing=None):
    return {'source_observation_id': str(i), 'event_time': i if t is None else t,
            'platform_id': 'A', 'array': array, 'bearing_deg': 30+i if bearing is None else bearing}


def ingest(repo, sid, rows, key='ingest', source='sensor'):
    return repo.ingest(sid, dict(idempotency_key=key, source_id=source, timeline_id='clock', observations=rows))


def prepare(repo, sid, **kwargs):
    return prepare_live(repo, sid, PrepareLive(**kwargs).model_dump())


def reply(snapshot, groups=None, unresolved=None, targets=None):
    if groups is None:
        groups = [{'group_ref':'G1', 'point_indices':list(range(1,len(snapshot['points'])+1)), 'reason_code':'tentative'}]
    return json.dumps({'format':'association-compact-v2', 'snapshot_id':snapshot['snapshot_id'],
                      'base_state_revision':snapshot['base_state_revision'], 'stream_groups':groups,
                      'target_hypotheses':targets or [], 'unresolved_groups':unresolved or []})


def authorize(repo, sid, snap, key='auth', **kwargs):
    body = AuthorizeLive(idempotency_key=key,snapshot_id=snap['snapshot_id'],
                         expected_state_revision=snap['base_state_revision'],context_digest=snap['context_digest'],consent=True,**kwargs).model_dump()
    return repo.authorize(sid,body,snap['kind'],{'model':'fake'})


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=LiveRepository(Path(self.temp.name)/'ledger.db')
        self.sid=self.repo.create('clock','test',{})['session_id']

    def test_empty_single_partial_and_conflict_are_durable(self):
        self.assertEqual(self.repo.state(self.sid)['accepted_count'],0)
        rows=[point(1),{'invalid':True},point(1),point(1,bearing=90)]
        receipt=ingest(self.repo,self.sid,rows)
        self.assertEqual([x['status'] for x in receipt['results']],['accepted','quarantined','duplicate','conflict'])
        self.assertEqual(ingest(self.repo,self.sid,rows),receipt)
        with self.assertRaises(LiveConflict):ingest(self.repo,self.sid,[point(2)])
        reopened=LiveRepository(self.repo.path)
        self.assertEqual(reopened.observations(self.sid)[0]['bearing_deg'],31)
        self.assertEqual(reopened.state(self.sid)['receipt_seq'],4)
        self.assertEqual(reopened.state(self.sid)['observation_revision'],1)
        self.assertEqual(len(reopened.export(self.sid)['receipts']),4)

    def test_nonfinite_row_quarantine_does_not_drop_valid_row(self):
        result=ingest(self.repo,self.sid,[point(1,bearing=float('nan')),point(2)])
        self.assertEqual([r['status'] for r in result['results']],['quarantined','accepted'])
        self.assertEqual(self.repo.state(self.sid)['accepted_count'],1)

    def test_timeline_mismatch_quarantined_not_pseudosynchronous(self):
        request=dict(idempotency_key='x',source_id='s',timeline_id='other',observations=[point(1)])
        self.assertEqual(self.repo.ingest(self.sid,request)['results'][0]['status'],'quarantined')
        self.assertEqual(self.repo.observations(self.sid),[])

    def test_batch_permutations_and_sizes_preserve_measurements(self):
        rows=[point(i,array='tow' if i%2 else 'hull') for i in range(20)]
        expected=None
        for seed in range(5):
            sid=self.repo.create('clock','order',{})['session_id'];local=rows.copy();random.Random(seed).shuffle(local)
            size=seed+1
            for start in range(0,len(local),size):ingest(self.repo,sid,local[start:start+size],str(start))
            values=sorted((p['source_observation_id'],p['event_time'],p['bearing_deg']) for p in self.repo.observations(sid))
            if expected is None:expected=values
            self.assertEqual(values,expected)
            snap=prepare(self.repo,sid)
            self.assertEqual(len(snap['points']),20)
            self.assertEqual(snap['observability']['strict_synchronous_groups'],0)

    def test_concurrent_idempotency(self):
        with ThreadPoolExecutor(4) as pool:
            values=list(pool.map(lambda _:ingest(self.repo,self.sid,[point(1)]),range(8)))
        self.assertTrue(all(v==values[0] for v in values))
        self.assertEqual(len(self.repo.observations(self.sid)),1)

    def test_snapshot_immutable_and_free_of_truth_source_metadata(self):
        row={**point(1),'target_id':'secret-label','truth':{'x':42},'file_name':'target_001.zip','api_key':'credential-not-to-persist'}
        ingest(self.repo,self.sid,[row]);snapshot=prepare(self.repo,self.sid)
        ingest(self.repo,self.sid,[point(2)],'next')
        self.assertEqual(self.repo.get(self.sid,'snapshot',snapshot['snapshot_id']),snapshot)
        for word in ('secret-label','target_001.zip','source_observation_id','source_id'):
            self.assertNotIn(word,json.dumps(snapshot['context']))
        self.assertEqual(len(self.repo.observations(self.sid)),2)
        self.assertNotIn('credential-not-to-persist',json.dumps(self.repo.export(self.sid)))

    def test_causal_double_cutoff_and_future_projection_excluded(self):
        ingest(self.repo,self.sid,[point(8),point(2),point(4)])
        snap=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=5,knowledge_cutoff_seq=2)
        self.assertEqual([p['event_time'] for p in snap['points']],[2])
        self.assertFalse(snap['prediction_allowed'])
        self.assertEqual(snap['knowledge_cutoff_seq'],2)
        with self.assertRaises(Exception):prepare(self.repo,self.sid,knowledge_cutoff_seq=999)
        # Future-dependent adopted identity cannot be projected into a past forecast.
        full=prepare(self.repo,self.sid);self.adopt(full)
        past=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=5)
        self.assertTrue(past['context']['future_projection_excluded'])
        self.assertEqual(past['context']['existing_stream_groups'],[])

    def adopt(self,snap,groups=None,targets=None,key='a'):
        run,_=authorize(self.repo,self.sid,snap,key)
        proposal=validate_association(reply(snap,groups,targets=targets),snap)
        self.repo.update_run(self.sid,run['run_id'],'awaiting_review',proposal=proposal,scope_ids=snap['scope_ids'],event_range=snap['event_range'])
        self.repo.review(self.sid,run['run_id'],dict(expected_state_revision=snap['base_state_revision'],decision='adopt',reason=''))
        return run

    def test_arrival_during_review_does_not_enter_old_scope(self):
        ingest(self.repo,self.sid,[point(1)]);snap=prepare(self.repo,self.sid)
        ingest(self.repo,self.sid,[point(2)],'next');self.adopt(snap)
        state=self.repo.state(self.sid)
        self.assertEqual(state['unprocessed_count'],1)
        self.assertEqual(state['projection']['scope_ids'],snap['scope_ids'])
        next_snap=prepare(self.repo,self.sid,observation_ids=[self.repo.observations(self.sid)[-1]['observation_id']])
        self.assertEqual(len(next_snap['scope_ids']),2)

    def test_late_points_replay_and_old_prediction_immutable(self):
        ingest(self.repo,self.sid,[point(10),point(12),point(14)]);snap=prepare(self.repo,self.sid);self.adopt(snap)
        pred=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=14)
        run,_=authorize(self.repo,self.sid,pred,'p')
        value={'curves':[],'capability':'bearing_only','spatial_qualified':False}
        old=self.repo.publish_prediction(self.sid,run['run_id'],value)
        ingest(self.repo,self.sid,[point(3),point(7)],'late')
        replay=prepare(self.repo,self.sid)
        self.assertTrue(replay['replay_plan']['required'])
        self.assertIn(old['prediction_id'],replay['replay_plan']['affected_predictions'])
        self.assertEqual(self.repo.get(self.sid,'prediction',old['prediction_id']),old)

    def test_authorization_idempotency_digest_and_unknown_fence(self):
        ingest(self.repo,self.sid,[point(1)]);snap=prepare(self.repo,self.sid)
        run,created=authorize(self.repo,self.sid,snap)
        self.assertTrue(created);self.assertFalse(authorize(self.repo,self.sid,snap)[1])
        self.assertTrue(self.repo.claim(self.sid,run['run_id']));self.assertFalse(self.repo.claim(self.sid,run['run_id']))
        self.repo.recover_interrupted();self.assertEqual(self.repo.get(self.sid,'run',run['run_id'])['status'],'unknown')
        new_snap=prepare(self.repo,self.sid)
        with self.assertRaises(LiveConflict):authorize(self.repo,self.sid,new_snap,'bypass')
        retry,created=authorize(self.repo,self.sid,new_snap,'manual',rerun_of=run['run_id'],duplicate_fee_ack=True)
        self.assertTrue(created)
        with self.assertRaises(LiveConflict):authorize(self.repo,self.sid,new_snap,'double',rerun_of=run['run_id'],duplicate_fee_ack=True)
        self.repo.update_run(self.sid,retry['run_id'],'invalid_reply')
        # Acknowledged ancestors must not permanently block a retry chain.
        leaf,created=authorize(self.repo,self.sid,new_snap,'leaf',rerun_of=retry['run_id'],duplicate_fee_ack=True)
        self.assertTrue(created)
        self.repo.update_run(self.sid,leaf['run_id'],'completed')
        later=prepare(self.repo,self.sid)
        self.assertTrue(authorize(self.repo,self.sid,later,'next-round')[1])

    def test_stale_configuration_and_review_cas(self):
        ingest(self.repo,self.sid,[point(1)]);snap=prepare(self.repo,self.sid)
        self.repo.configure(self.sid,0,{'algorithm':{'window_times':12}})
        with self.assertRaises(LiveConflict):authorize(self.repo,self.sid,snap)
        snap=prepare(self.repo,self.sid);run,_=authorize(self.repo,self.sid,snap)
        self.repo.update_run(self.sid,run['run_id'],'awaiting_review',proposal=validate_association(reply(snap),snap),scope_ids=snap['scope_ids'],event_range=snap['event_range'])
        with self.assertRaises(LiveConflict):self.repo.review(self.sid,run['run_id'],dict(expected_state_revision=0,decision='adopt',reason=''))

    def test_multi_fragment_chain_and_illegal_occupancy(self):
        ingest(self.repo,self.sid,[point(i) for i in range(12)]);snap=prepare(self.repo,self.sid)
        refs=snap['evidence']['fragments']
        self.assertTrue(refs)
        groups=[{'group_ref':'G1','fragment_refs':[r['reference_id'] for r in refs],'reason_code':'continuity'}]
        covered={x for r in refs for x in r['observation_ids']}
        unresolved=[{'point_indices':[i+1 for i,x in enumerate(snap['scope_ids']) if x not in covered],'reason_code':'insufficient'}] if len(covered)<12 else []
        proposal=validate_association(reply(snap,groups,unresolved),snap)
        self.assertEqual(len(proposal['stream_groups']),1)
        bad=json.loads(reply(snap));bad['stream_groups'].append({'group_ref':'G2','point_indices':[1],'reason_code':'tentative'})
        with self.assertRaises(ValueError):validate_association(json.dumps(bad),snap)
        bad=json.loads(reply(snap));bad['stream_groups'][0]['point_indices']=[999]
        with self.assertRaises(ValueError):validate_association(json.dumps(bad),snap)
        with self.assertRaises(ValueError):validate_association(reply(snap,groups=[]),snap)

    def test_same_time_candidates_and_cross_array_not_forced(self):
        ingest(self.repo,self.sid,[point(1,t=0),point(2,t=0),point(3,t=0,array='tow')]);snap=prepare(self.repo,self.sid)
        self.assertEqual(snap['observability']['strict_synchronous_groups'],1)
        self.assertFalse(snap['observability']['spatial_qualified'])
        self.assertEqual(len(snap['evidence']['conflict_sets']),1)
        with self.assertRaises(ValueError):validate_association(reply(snap),snap)
        proposal=validate_association(reply(snap,groups=[],unresolved=[{'point_indices':[1,2,3],'reason_code':'competing'}]),snap)
        self.assertEqual(proposal['quality']['coverage'],0)

    def test_rolling_windows_cover_whole_time_span(self):
        ingest(self.repo,self.sid,[point(i,bearing=20+.1*i) for i in range(45)]);snap=prepare(self.repo,self.sid)
        windows=snap['evidence']['windows']
        self.assertGreater(len(windows),3)
        self.assertEqual(windows[0]['event_range'][0],0)
        self.assertEqual(windows[-1]['event_range'][-1],44)
        self.assertEqual(snap['evidence']['coverage']['candidate_covered'],45)

    def test_prediction_support_and_time_boundary(self):
        ingest(self.repo,self.sid,[point(i) for i in range(4)]);snap=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=3)
        value={'format':'bearing-prediction-v1','snapshot_id':snap['snapshot_id'],'purpose':snap['purpose'],
               'curves':[{'support_indices':[1,2,3,4],'reason_code':'tentative','samples':[{'event_time':4,'bearing_deg':35,'uncertainty_deg':2}]}]}
        self.assertEqual(validate_prediction(json.dumps(value),snap)['capability'],'bearing_only')
        value['curves'][0]['samples'][0]['event_time']=2
        with self.assertRaises(ValueError):validate_prediction(json.dumps(value),snap)
        value['curves'][0]['samples'][0]['event_time']=4;value['curves'][0]['samples'][0]['east_m']=99
        with self.assertRaises(ValueError):validate_prediction(json.dumps(value),snap)

    def test_lineage_stable_under_reference_renaming(self):
        ingest(self.repo,self.sid,[point(1),point(2)])
        snap=prepare(self.repo,self.sid);target=[{'target_ref':'H1','stream_group_refs':['G1'],'reason_code':'tentative'}];self.adopt(snap,targets=target)
        before=copy.deepcopy(self.repo.state(self.sid)['projection'])
        snap=prepare(self.repo,self.sid);groups=[{'group_ref':'renamed','point_indices':[1,2],'reason_code':'continuity'}]
        self.adopt(snap,groups=groups,targets=[{'target_ref':'H9','stream_group_refs':['renamed'],'reason_code':'continuity'}],key='again')
        after=self.repo.state(self.sid)['projection']
        self.assertEqual(before['stream_groups'][0]['stable_id'],after['stream_groups'][0]['stable_id'])
        self.assertEqual(before['target_hypotheses'][0]['stable_id'],after['target_hypotheses'][0]['stable_id'])

    def test_extension_split_merge_retraction_and_archive(self):
        ingest(self.repo,self.sid,[point(1),point(2)])
        self.adopt(prepare(self.repo,self.sid))
        original=self.repo.state(self.sid)['projection']['stream_groups'][0]['stable_id']
        ingest(self.repo,self.sid,[point(3)],'more');self.adopt(prepare(self.repo,self.sid),key='extend')
        group=self.repo.state(self.sid)['projection']['stream_groups'][0]
        self.assertEqual(group['stable_id'],original);self.assertEqual(group['transition'],'extension')
        groups=[{'group_ref':'G1','point_indices':[1],'reason_code':'tentative'}, {'group_ref':'G2','point_indices':[2,3],'reason_code':'tentative'}]
        self.adopt(prepare(self.repo,self.sid),groups,key='split')
        children=self.repo.state(self.sid)['projection']['stream_groups']
        self.assertTrue(all(g['stable_id']!=original and original in g['parent_ids'] for g in children))
        self.adopt(prepare(self.repo,self.sid),key='merge')
        state=self.repo.state(self.sid);merged=state['projection']['stream_groups'][0]
        self.assertEqual(merged['transition'],'merge');self.assertEqual(len(merged['parent_ids']),2)
        before=self.repo.export(self.sid)
        self.repo.retract(self.sid,state['state_revision'],[merged['stable_id']],'核对后撤回')
        state=self.repo.state(self.sid)
        self.assertEqual(state['projection']['stream_groups'],[]);self.assertEqual(state['projection']['quality']['waiting_evidence'],3)
        self.assertEqual(len(self.repo.observations(self.sid)),3)
        self.repo.archive_legacy(self.sid,{'prediction_ledger':[{'legacy':True}],'api_key':'not-archived'})
        archive=self.repo.items(self.sid,'legacy_archive')[0]
        self.assertNotIn('api_key',json.dumps(archive));self.assertTrue(archive['evaluation_or_archive_only'])

    def test_historical_configuration_knowledge_cutoff(self):
        ingest(self.repo,self.sid,[point(i) for i in range(5)])
        old_config=self.repo.state(self.sid)['configuration_id']
        self.repo.configure(self.sid,0,{'algorithm':{'window_times':12}})
        snap=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=4,knowledge_cutoff_seq=5)
        self.assertEqual(snap['evidence_configuration_id'],old_config)
        self.assertEqual(snap['context']['configuration']['algorithm']['window_times'],8)

    def test_known_historical_projection_can_support_past_forecast(self):
        ingest(self.repo,self.sid,[point(i) for i in range(3)])
        self.adopt(prepare(self.repo,self.sid))
        cutoff=self.repo.state(self.sid)['receipt_seq']
        ingest(self.repo,self.sid,[point(5),point(6)],'later')
        self.adopt(prepare(self.repo,self.sid),key='future-decision')
        snap=prepare(self.repo,self.sid,purpose='causal_forecast',forecast_origin_time=2,knowledge_cutoff_seq=cutoff)
        self.assertTrue(snap['context']['future_projection_excluded'])
        self.assertTrue(snap['context']['historical_projection_used'])
        self.assertEqual(len(snap['context']['existing_stream_groups'][0]['point_indices']),3)

    def test_bad_continuation_is_not_adopted_as_single_chain(self):
        ingest(self.repo,self.sid,[point(1,t=1,bearing=10),point(2,t=1.01,bearing=170)])
        snap=prepare(self.repo,self.sid)
        with self.assertRaises(ValueError):validate_association(reply(snap),snap)

    def test_large_selection_preserves_all_rows_without_256_cap(self):
        ingest(self.repo,self.sid,[point(i,bearing=40+i*.01) for i in range(301)])
        snap=prepare(self.repo,self.sid)
        self.assertEqual(len(snap['points']),301);self.assertEqual(len(snap['context']['rows']),301)
        self.assertEqual(len(snap['context']['point_roles']['unprocessed']),301)
        self.assertEqual(snap['model_calls'],0)

    def test_competing_proposals_are_invalidated_atomically_on_adoption(self):
        ingest(self.repo,self.sid,[point(1)])
        first=prepare(self.repo,self.sid);run1,_=authorize(self.repo,self.sid,first,'first')
        self.repo.complete_analysis(self.sid,run1['run_id'],validate_association(reply(first),first),first)
        second=prepare(self.repo,self.sid);run2,_=authorize(self.repo,self.sid,second,'second')
        self.repo.complete_analysis(self.sid,run2['run_id'],validate_association(reply(second),second),second)
        self.repo.review(self.sid,run1['run_id'],dict(expected_state_revision=0,decision='adopt',reason=''))
        self.assertEqual(self.repo.get(self.sid,'run',run2['run_id'])['status'],'stale')
        with self.assertRaises(LiveConflict):self.repo.review(self.sid,run2['run_id'],dict(expected_state_revision=1,decision='adopt',reason=''))

    def test_evaluation_aligns_ids_and_counts_unresolved(self):
        points=[{'observation_id':str(i),'platform_id':'A','array':'hull','event_time':i} for i in range(4)]
        truth={'0':'target-a','1':'target-a','2':'target-b','3':'target-b'}
        result=evaluate_memberships(points,truth,{'0':'opaque-x','1':'opaque-x','2':'opaque-y','3':'opaque-y'})
        self.assertEqual(result['aligned_accuracy_all_points'],1)
        result=evaluate_memberships(points,truth,{'0':'one','1':'two','2':'one'})
        self.assertEqual(result['unresolved'],1);self.assertEqual(result['merge_excess'],1);self.assertEqual(result['split_excess'],1)


class LiveJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=LiveRepository(self.root/'ledger.db');self.sid=self.repo.create('clock','jobs',{})['session_id']
        ingest(self.repo,self.sid,[point(1),point(2),point(3)])
        self.snap=prepare(self.repo,self.sid);self.run,_=authorize(self.repo,self.sid,self.snap)
        self.settings=ModelSettings('fake','http://invalid.local','fake')

    async def test_stream_append_and_arrival_while_running(self):
        calls=[];started=asyncio.Event();release=asyncio.Event();answer=reply(self.snap)
        class Fake:
            async def stream(self,*args):
                calls.append(1);started.set();await release.wait()
                for i in range(0,len(answer),20):yield {'event':'delta','text':answer[i:i+20]}
                yield {'event':'model_complete','result':{'answer':answer}}
        task=asyncio.create_task(execute(self.repo,self.sid,self.run['run_id'],Fake(),self.settings,self.root))
        await started.wait()
        observer=events(self.repo,self.sid,self.run['run_id']);await anext(observer);await observer.aclose()
        self.assertFalse(task.done())
        ingest(self.repo,self.sid,[point(4)],'during');release.set();await task
        run=self.repo.get(self.sid,'run',self.run['run_id']);self.assertEqual(run['status'],'awaiting_review');self.assertEqual(run['body'],answer)
        stream1=[x async for x in events(self.repo,self.sid,self.run['run_id'])]
        last=self.repo.events(self.sid,self.run['run_id'])[-1]['seq']
        stream2=[x async for x in events(self.repo,self.sid,self.run['run_id'],last)]
        self.assertEqual(stream2,[]);self.assertEqual(len(calls),1);self.assertGreater(len(stream1),4)
        self.assertEqual(len(self.repo.observations(self.sid)),4)

    async def test_invalid_reply_retains_exact_body_no_retry(self):
        class Fake:
            def invoke(self,*args):return {'answer':'{"invalid":true}'}
        await execute(self.repo,self.sid,self.run['run_id'],Fake(),self.settings,self.root)
        run=self.repo.get(self.sid,'run',self.run['run_id'])
        self.assertEqual(run['status'],'invalid_reply');self.assertEqual(run['body'],'{"invalid":true}')

    async def test_upstream_failure_unknown_and_no_second_claim(self):
        calls=[]
        class Fake:
            async def stream(self,*args):
                calls.append(1);yield {'event':'delta','text':'partial'};raise RuntimeError('private provider text')
        await execute(self.repo,self.sid,self.run['run_id'],Fake(),self.settings,self.root)
        await execute(self.repo,self.sid,self.run['run_id'],Fake(),self.settings,self.root)
        run=self.repo.get(self.sid,'run',self.run['run_id'])
        self.assertEqual(run['status'],'unknown');self.assertEqual(run['body'],'partial');self.assertEqual(calls,[1]);self.assertNotIn('private',json.dumps(run))

    async def test_cancellation_and_truncation_are_durable(self):
        class Cancelled:
            async def stream(self,*args):
                yield {'event':'delta','text':'partial'}
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await execute(self.repo,self.sid,self.run['run_id'],Cancelled(),self.settings,self.root)
        self.assertEqual(self.repo.get(self.sid,'run',self.run['run_id'])['status'],'unknown')
        rerun,_=authorize(self.repo,self.sid,self.snap,'retry',rerun_of=self.run['run_id'],duplicate_fee_ack=True)
        class Truncated:
            async def stream(self,*args):
                yield {'event':'delta','text':'not complete'}
                yield {'event':'model_complete','result':{'answer':'not complete','finish_reason':'length'}}
        await execute(self.repo,self.sid,rerun['run_id'],Truncated(),self.settings,self.root)
        self.assertEqual(self.repo.get(self.sid,'run',rerun['run_id'])['status'],'output_truncated')

    async def test_configuration_change_during_model_marks_result_stale(self):
        repo,sid=self.repo,self.sid;answer=reply(self.snap)
        class Fake:
            async def stream(self,*args):
                repo.configure(sid,0,{'algorithm':{'window_times':12}})
                yield {'event':'delta','text':answer}
                yield {'event':'model_complete','result':{'answer':answer}}
        await execute(repo,sid,self.run['run_id'],Fake(),self.settings,self.root)
        self.assertEqual(repo.get(sid,'run',self.run['run_id'])['status'],'stale')
        self.assertEqual(repo.state(sid)['projection'],{})


class LiveApiTests(unittest.TestCase):
    def test_local_api_assets_contract_and_configuration(self):
        with tempfile.TemporaryDirectory() as root,patch.object(api,'LIVE_DB',Path(root)/'ledger.db'):
            with TestClient(create_app(),base_url='http://127.0.0.1') as client:
                for path in ('/live','/assets/live.js','/assets/live.css'):
                    self.assertEqual(client.get(path).status_code,200)
                state=client.post('/api/incremental/live-sessions',json={'timeline_id':'clock'}).json();base='/api/incremental/live-sessions/'+state['session_id']
                result=client.post(base+'/observations',json=dict(idempotency_key='i',source_id='A',timeline_id='clock',observations=[point(1),{}]))
                self.assertEqual(result.status_code,200);self.assertEqual(result.json()['accepted'],1)
                snap=client.post(base+'/prepare',json={}).json();self.assertEqual(snap['model_calls'],0)
                body=dict(idempotency_key='a',snapshot_id=snap['snapshot_id'],expected_state_revision=0,context_digest=snap['context_digest'],consent=False)
                with patch.object(api,'model_settings',return_value=ModelSettings('fake','http://invalid.local','fake')):
                    self.assertEqual(client.post(base+'/analyses',json=body).status_code,400)
                self.assertEqual(client.get(base).json()['runs'],[])
                self.assertEqual(len(client.get(base+'/export').json()['receipts']),2)
                self.assertEqual(client.post(base+'/configuration',json={'expected_state_revision':0,'configuration':{'api_key':'never'}}).status_code,400)
                self.assertEqual(client.post(base+'/predictions/prepare',json={'purpose':'causal_forecast','forecast_origin_time':1}).json()['prediction_allowed'],False)

    def test_authorized_api_dispatch_is_idempotent_and_events_read_only(self):
        calls=[]
        class Fake:
            async def stream(self,images,prompt,*args):
                calls.append(1);ctx=json.loads(prompt.rsplit('\n',1)[1])
                answer=json.dumps({'format':'association-compact-v2','snapshot_id':ctx['snapshot_id'],
                    'base_state_revision':ctx['base_state_revision'],'stream_groups':[], 'target_hypotheses':[],
                    'unresolved_groups':[{'point_indices':[1],'reason_code':'insufficient'}]})
                yield {'event':'delta','text':answer}
                yield {'event':'model_complete','result':{'answer':answer}}
        with tempfile.TemporaryDirectory() as root,patch.object(api,'LIVE_DB',Path(root)/'db'),patch.object(api,'model_settings',return_value=ModelSettings('fake','http://invalid.local','private-test-key')):
            application=create_app();application.dependency_overrides[api.live_gateway]=lambda:Fake()
            with TestClient(application,base_url='http://127.0.0.1') as client:
                sid=client.post('/api/incremental/live-sessions',json={'timeline_id':'clock'}).json()['session_id'];base='/api/incremental/live-sessions/'+sid
                client.post(base+'/observations',json=dict(idempotency_key='a',source_id='sensor',timeline_id='clock',observations=[point(1)]))
                snapshot=client.post(base+'/prepare',json={}).json()
                body=dict(idempotency_key='auth',snapshot_id=snapshot['snapshot_id'],expected_state_revision=0,context_digest=snapshot['context_digest'],consent=True)
                response=client.post(base+'/analyses',json=body);self.assertEqual(response.status_code,200,response.text);rid=response.json()['run_id']
                for _ in range(100):
                    run=client.get(base+'/analyses/'+rid).json()
                    if run['status']=='awaiting_review':break
                    time.sleep(.01)
                self.assertEqual(run['status'],'awaiting_review');self.assertEqual(calls,[1])
                self.assertEqual(client.post(base+'/analyses',json=body).json()['run_id'],rid)
                stream=client.get(base+'/analyses/'+rid+'/events').text
                self.assertIn('association-compact-v2',stream);self.assertEqual(calls,[1])
                sequence=api.repository().events(sid,rid)[-1]['seq']
                self.assertEqual(client.get(base+'/analyses/'+rid+'/events',headers={'Last-Event-ID':str(sequence)}).text,'')
                self.assertNotIn('private-test-key',json.dumps(client.get(base+'/export').json()))

    def test_explicit_migration_retains_aliases_and_archive_not_evidence(self):
        with tempfile.TemporaryDirectory() as root,patch.object(api,'LIVE_DB',Path(root)/'db'):
            with TestClient(create_app(),base_url='http://127.0.0.1') as client:
                data={'timeline_id':'clock','dataset':{'candidates':[point(1)],'target_catalog':[{'truth':'do-not-use'}]},
                      'legacy_history':{'prediction_ledger':[{'old_result':True}],'tracks':{'old-target':{}},'api_key':'discard'}}
                response=client.post('/api/incremental/live-sessions/migrate',json=data)
                self.assertEqual(response.status_code,200,response.text)
                sid=response.json()['session']['session_id'];base='/api/incremental/live-sessions/'+sid
                exported=client.get(base+'/export').json();self.assertEqual(len(exported['versions']['legacy_archive']),1)
                context=client.post(base+'/prepare',json={}).json()['context']
                for text in ('old-target','do-not-use','old_result','discard'):self.assertNotIn(text,json.dumps(context))


if __name__=='__main__':unittest.main()
