import json
import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.tests.test_incremental_prediction import dataset,answer


class IncrementalStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for replacement in (patch.object(api,'ROOT',Path(self.temp.name)),patch.object(api,'model_settings',return_value=ModelSettings(model='test',base_url='https://invalid.test',api_key='fake'))):
            replacement.start();self.addCleanup(replacement.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close);self.addCleanup(app.dependency_overrides.clear)
        self.sid=self.client.post('/api/incremental/sessions',json={'dataset':dataset(),'seed_ids':['P0','P1','P2']}).json()['session_id']

    def run_stream(self,gateway,**extra):
        app.dependency_overrides[api.get_prediction_gateway]=lambda:gateway
        return self.client.post(f'/api/incremental/sessions/{self.sid}/step?stream=true',json={'version':0,'consent':True,**extra})

    def test_real_deltas_then_validated_commit_one_call(self):
        calls=[]
        class Fake:
            async def stream(self,images,prompt,settings,output,mode):
                calls.append(1);self_context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1])
                text=answer(self_context)
                yield {'event':'delta','text':text[:30]}
                yield {'event':'reasoning','text':'not assistant answer'}
                yield {'event':'delta','text':text[30:]}
                yield {'event':'model_complete','result':{'answer':text,'usage':{'total_tokens':1}}}
        response=self.run_stream(Fake());self.assertEqual(response.status_code,200)
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([e['event'] for e in events],['phase','delta','delta','phase','result'])
        self.assertNotIn('not assistant answer',response.text)
        self.assertEqual(events[-1]['result']['version'],1);self.assertEqual(len(calls),1)
        nodes=events[-1]['result']['execution']['nodes'];names=[n['node'] for n in nodes]
        self.assertLess(names.index('platform_assessment'),names.index('model_prediction'))
        self.assertLess(names.index('model_prediction'),names.index('spatial_planning'))
        self.assertLess(names.index('spatial_planning'),names.index('state_commit'))
        self.assertTrue(all(n['status']=='completed' for n in nodes[:-1]))
        self.assertEqual(nodes[-1]['status'],'waiting_for_user')
        self.assertEqual(events[-1]['result']['status'],'awaiting_review')
        self.assertEqual(self.client.get(f'/api/incremental/sessions/{self.sid}').json()['calls'],1)

    def test_truncation_stops_without_commit_or_retry(self):
        class Fake:
            async def stream(self,*args):yield {'event':'delta','text':'{"reason":"partial'}
        response=self.run_stream(Fake());self.assertIn('"event": "error"',response.text)
        state=self.client.get(f'/api/incremental/sessions/{self.sid}').json()
        self.assertEqual((state['status'],state['version'],state['calls']),('call_unknown',0,1))
        self.assertEqual(state['rounds'],[])
        self.assertEqual(self.run_stream(Fake()).status_code,400)

    def test_bad_point_reference_is_not_committed(self):
        class Fake:
            async def stream(self,images,prompt,*args):
                context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1]);text=answer(context,ids=['P9'])
                yield {'event':'delta','text':text};yield {'event':'model_complete','result':{'answer':text}}
        response=self.run_stream(Fake());self.assertIn('"event": "error"',response.text)
        state=self.client.get(f'/api/incremental/sessions/{self.sid}').json()
        self.assertEqual((state['status'],state['version']),('invalid_response',0))

    def test_missing_consent_and_stale_version_never_call(self):
        class Fake:
            async def stream(self,*args):raise AssertionError('must not call');yield
        self.assertEqual(self.run_stream(Fake(),consent=False).status_code,400)
        self.assertEqual(self.run_stream(Fake(),version=9).status_code,400)

    def test_disconnect_after_start_preserves_unknown_and_releases_lock(self):
        class Fake:
            async def stream(self,*args):
                yield {'event':'delta','text':'partial'}
        async def disconnect():
            response=api.stream_prediction(self.sid,api.AdvancePrediction(version=0,consent=True),Fake())
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
            await response.background()
        asyncio.run(disconnect())
        state=self.client.get(f'/api/incremental/sessions/{self.sid}').json()
        self.assertEqual((state['status'],state['calls'],state['version']),('call_unknown',1,0))
        self.assertTrue(api.LOCK.acquire(blocking=False));api.LOCK.release()
