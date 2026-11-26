import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.app.services.inputs.observation_diagnostics import observation_diagnostics


def point(cid,t,array='hull',platform_id='A'):
    return {'candidate_id':cid,'t':t,'array':array,'platform_id':platform_id,'bearing_deg':30}


class SparseInputPolicyTests(unittest.TestCase):
    def test_diagnostics_do_not_invent_synchronization_or_targets(self):
        data=[point('h',1),point('t',1.0001,'tow'),point('b',1,'tow','B')]
        original=json.dumps(data);stats=observation_diagnostics(data)
        self.assertEqual(stats['point_count'],3);self.assertEqual(stats['strict_dual_group_count'],0)
        self.assertFalse(stats['target_pairing_confirmed']);self.assertEqual(json.dumps(data),original)

    def test_single_sparse_and_shuffled_batches_prepare_without_paid_calls(self):
        cases=[[point('one',37,'tow')],[point('h',1),point('t',2,'tow')],
               [point('late',40),point('early',3,'tow'),point('middle',10)],
               [point('h1',0),point('h2',0),point('t1',0,'tow')]]
        with tempfile.TemporaryDirectory() as root,patch.object(api,'MIXED_ROOT',Path(root)):
            client=TestClient(app,base_url='http://127.0.0.1')
            for candidates in cases:
                with self.subTest(points=len(candidates)):
                    uploaded=client.post('/api/incremental/import-observations',files={'files':('sparse.json',json.dumps({'candidates':candidates}).encode(),'application/json')})
                    self.assertEqual(uploaded.status_code,200,uploaded.text)
                    created=client.post('/api/incremental/mixed-sessions',json={'dataset':uploaded.json()})
                    self.assertEqual(created.status_code,200,created.text);sid=created.json()['session_id']
                    response=client.post(f'/api/incremental/mixed-sessions/{sid}/preview',json={'version':0,'selected_ids':[p['candidate_id'] for p in candidates]})
                    self.assertEqual(response.status_code,200,response.text);data=response.json()
                    self.assertEqual(len(data['context']['observations']),len(candidates))
                    self.assertEqual(data['context']['input_diagnostics']['point_count'],len(candidates))
                    self.assertEqual(data['context']['input_diagnostics']['policy'],'accept-sparse-v1')
                    self.assertEqual(data['model_calls'],0)
                    self.assertEqual(client.get(f'/api/incremental/mixed-sessions/{sid}').json()['calls'],0)

    def test_single_point_association_can_complete_with_isolated_fake(self):
        calls=[]
        class Fake:
            def invoke(self,images,prompt,*args):
                calls.append(1);ctx=json.loads(prompt.split('\n',1)[1])
                return {'answer':json.dumps({'format':'association-compact-v1','step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                    'tracks':[{'track_ref':'N1','point_indices':[1],'reason_code':'tentative'}]})}
        with tempfile.TemporaryDirectory() as root,patch.object(api,'MIXED_ROOT',Path(root)), \
                patch.dict(app.dependency_overrides,{api.get_prediction_gateway:lambda:Fake()}), \
                patch.object(api,'model_settings',return_value=ModelSettings(model='offline',base_url='http://invalid.local',api_key='offline')):
            client=TestClient(app,base_url='http://127.0.0.1');p=point('one',37,'tow')
            sid=client.post('/api/incremental/mixed-sessions',json={'dataset':{'candidates':[p]}}).json()['session_id']
            url=f'/api/incremental/mixed-sessions/{sid}';body={'version':0,'selected_ids':['one']}
            prepared=client.post(url+'/preview',json=body).json();self.assertFalse(calls)
            response=client.post(url+'/step',json={**body,'consent':True,'context_digest':prepared['context_digest']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'awaiting_review');self.assertEqual(len(calls),1)
            self.assertEqual(sum(len(t['history']) for t in response.json()['tracks'].values()),1)

    def test_sparse_policy_asset_and_health_are_available(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        self.assertEqual(client.get('/api/health').json()['mixed_input_policy'],'accept-sparse-v1')
        self.assertEqual(client.get('/assets/observation-diagnostics.js').status_code,200)
