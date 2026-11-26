"""Same mixed workbench, same projection/plot flow, independently durable input."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.live_tracking import IngestLive
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed
from backend.app.services.inputs import workspace_observations


def p(cid,t,array='hull',bearing=None):
    return {'candidate_id':cid,'t':t,'platform_id':'A','array':array,'bearing_deg':30+t if bearing is None else bearing}


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        replacement=patch.object(api,'MIXED_ROOT',Path(self.temp.name));replacement.start();self.addCleanup(replacement.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)
        self.data={'candidates':[p('h1',1),p('h2',2),p('h3',3)]}
        self.sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':self.data,'persistent_input':True,'timeline_id':'clock'}).json()['session_id']
        self.base='/api/incremental/mixed-sessions/'+self.sid

    def append(self,rows,key='append'):
        return self.client.post(self.base+'/observations',json=dict(idempotency_key=key,source_id='workspace',timeline_id='clock',observations=rows))

    def test_existing_page_is_the_primary_entry(self):
        page=self.client.get('/incremental').text
        self.assertNotIn('href="/live"',page)
        for control in ('pointImportMode','enableWorkspaceInput','appendWorkspacePoints','workspaceInputStatus'):
            self.assertIn('id="'+control+'"',page)
        response=self.client.get('/live',follow_redirects=False)
        self.assertEqual(response.status_code,307);self.assertEqual(response.headers['location'],'/incremental')

    def test_append_keeps_session_projection_and_original_measurements(self):
        before=self.client.get(self.base).json()
        response=self.append([p('h4',4),p('h4',4),p('h1',1,bearing=90),{}])
        self.assertEqual(response.status_code,200,response.text)
        receipt=response.json()['receipt'];self.assertEqual([r['status'] for r in receipt['results']],['accepted','duplicate','conflict','quarantined'])
        self.assertEqual(self.append([p('h4',4),p('h4',4),p('h1',1,bearing=90),{}]).json(),response.json())
        state=self.client.get(self.base).json();self.assertEqual(state['session_id'],before['session_id'])
        self.assertEqual(state['version'],before['version']);self.assertEqual(state['rounds'],before['rounds']);self.assertEqual(state['calls'],0)
        self.assertEqual(state['input_summary']['accepted_count'],4)
        catalogue=self.client.get(self.base+'/catalog').json()['candidates'];self.assertEqual(len(catalogue),4)
        self.assertEqual(next(row for row in catalogue if row['candidate_id']=='h1')['bearing_deg'],31)
        self.assertEqual(len(self.client.get(self.base+'/export').json()['observation_ledger']['receipts']),7)

    def test_new_unselected_points_do_not_invalidate_frozen_context(self):
        body=dict(version=0,selected_ids=['h1','h2','h3'])
        before=self.client.post(self.base+'/preview',json=body).json()
        self.append([p('late-but-unselected',0)])
        after=self.client.post(self.base+'/preview',json=body).json()
        self.assertEqual(before['context_digest'],after['context_digest'])
        self.assertEqual(after['context']['input_snapshot']['knowledge_cutoff_seq'],3)

    def test_old_results_are_not_rewritten_by_explicit_enable(self):
        state=create_mixed(self.data);state.update(status='invalid_response',calls=1)
        directory=api.ResultRepository(api.MIXED_ROOT).create_run();api.save_state(directory/'state.json',state)
        response=self.client.post('/api/incremental/mixed-sessions/'+directory.name+'/enable-input',json={'version':0,'timeline_id':'old-clock'})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['status'],'invalid_response');self.assertEqual(result['calls'],1)
        self.assertEqual(result['rounds'],state['rounds']);self.assertEqual(result['model_calls'],0)

    def test_rolling_chain_candidates_and_same_chain_union(self):
        state=create_mixed({'candidates':[p(str(i),i) for i in range(24)]})
        path=Path(self.temp.name)/'standalone'/'state.json';path.parent.mkdir()
        state=workspace_observations.enable(path,state,'rolling-clock')
        ctx,_=prepare_mixed(state,list(state['aliases']))
        self.assertTrue(ctx['candidate_competition'])
        self.assertGreater(len({tuple(r['observed_time_window']) for r in ctx['multi_time_references']}),3)
        refs=[r['reference_id'] for r in ctx['multi_time_references']]
        answer={'format':'association-compact-v1','step_id':1,'base_state_version':0,
            'tracks':[{'track_ref':'N1','candidate_refs':refs,'reason_code':'continuity'}]}
        result=validate_mixed(state,ctx,json.dumps(answer))
        history=result['tracks']['T001']['history']
        self.assertEqual(len(history),24);self.assertEqual(len({r['candidate_id'] for r in history}),24)
        duplicate=copy.deepcopy(answer);duplicate['tracks'].append({'track_ref':'N2','candidate_ref':refs[0],'reason_code':'competing'})
        checked=validate_mixed(state,ctx,json.dumps(duplicate))
        occupied=[p['candidate_id'] for t in checked['tracks'].values() for p in t['history']]
        self.assertEqual(len(occupied),len(set(occupied)))
        self.assertTrue(checked['warnings'])
        self.assertLess(len(occupied),24)


class WorkspaceDuringModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_during_existing_model_does_not_get_overwritten(self):
        with tempfile.TemporaryDirectory() as root,patch.object(api,'MIXED_ROOT',Path(root)),patch.object(api,'model_settings',return_value=ModelSettings('fake','http://invalid.local','fake')):
            client=TestClient(app,base_url='http://127.0.0.1')
            data={'candidates':[p('h1',1),p('h2',2),p('h3',3)]}
            sid=client.post('/api/incremental/mixed-sessions',json={'dataset':data,'persistent_input':True,'timeline_id':'clock'}).json()['session_id']
            path=api.mixed_path(sid);ctx,_=prepare_mixed(api.read_workspace_state(path),['h1','h2','h3'])
            body=api.MixedSelection(version=0,selected_ids=['h1','h2','h3'],consent=True,context_digest=api.context_digest(ctx))
            gate=asyncio.Event();started=asyncio.Event();calls=[]
            class Fake:
                async def stream(self,images,prompt,*args):
                    calls.append(1);started.set();await gate.wait()
                    value={'step_id':1,'base_state_version':0,'tracks':[],'unassigned':[],'ambiguous':[]}
                    answer=json.dumps(value);yield {'event':'delta','text':answer};yield {'event':'model_complete','result':{'answer':answer}}
            response=api.stream_mixed_model(sid,body,Fake(),plot=False)
            await anext(response.body_iterator);await started.wait()
            frozen=json.loads(path.read_text(encoding='utf-8'))
            api.append_workspace_input(sid,IngestLive(idempotency_key='during',source_id='workspace',timeline_id='clock',observations=[p('h4',4)]))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),frozen)
            gate.set();chunks=[chunk async for chunk in response.body_iterator];await response.background()
            state=api.read_workspace_state(path)
            self.assertEqual(state['status'],'awaiting_review');self.assertEqual(state['calls'],1)
            self.assertEqual(len(state['points']),4);self.assertEqual(state['input_summary']['unsubmitted_count'],1)
            self.assertEqual(calls,[1]);client.close()
