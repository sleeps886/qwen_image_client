import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.services.association import sequential_runner as seq
from backend.app.models.model_settings import ModelSettings
from backend.tests.test_sequential_runner import data,FakeGateway


class SequentialApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.patch=patch.object(api,'MIXED_ROOT',Path(self.temp.name));self.patch.start();self.addCleanup(self.patch.stop)
        self.settings=patch.object(api,'model_settings',return_value=ModelSettings('fake','http://invalid.local','fake'));self.settings.start();self.addCleanup(self.settings.stop)
        self.gateway=FakeGateway();app.dependency_overrides[api.get_prediction_gateway]=lambda:self.gateway
        self.addCleanup(lambda:app.dependency_overrides.pop(api.get_prediction_gateway,None))
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)
        result=self.client.post('/api/incremental/mixed-sessions',json={'dataset':data(),'max_calls':30})
        self.assertEqual(result.status_code,200,result.text)
        self.sid=result.json()['session_id'];self.base=f'/api/incremental/mixed-sessions/{self.sid}'
        self.body={'version':0,'units':[[f'h{i}',f'w{i}'] for i in range(6)],'call_limit':6}
        preview=self.client.post(self.base+'/runs/preview',json=self.body);self.assertEqual(preview.status_code,200,preview.text)
        self.spec=preview.json();created=self.client.post(self.base+'/runs',json={**self.body,'request_key':'api-test','context_digest':self.spec['context_digest']})
        self.assertEqual(created.status_code,200,created.text);self.rid=created.json()['id'];self.run_url=self.base+'/runs/'+self.rid

    def dispatch(self,cursor,consent=True):return {'expected_cursor':cursor,'context_digest':self.spec['context_digest'],'consent':consent}
    def wait(self,predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            result=self.client.get(self.run_url).json()
            if predicate(result):return result
            time.sleep(.02)
        self.fail('worker did not settle')

    def test_continuous_confirmation_and_duplicate_requests(self):
        response=self.client.post(self.run_url+'/start',json=self.dispatch(0,False));self.assertEqual(response.status_code,400)
        self.assertEqual(self.gateway.contexts,[])
        response=self.client.post(self.run_url+'/start',json=self.dispatch(0));self.assertEqual(response.status_code,200,response.text)
        finished=self.wait(lambda r:r['status']=='completed_awaiting_review' and not r['worker_active'])
        self.assertEqual(finished['cursor'],6);self.assertEqual(finished['calls'],5)
        self.assertEqual(self.client.get(self.base).json()['tracks'],{})
        response=self.client.post(self.run_url+'/next',json=self.dispatch(0));self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.gateway.contexts),5)
        adopted=self.client.post(self.run_url+'/review',json={'adopt':True});self.assertEqual(adopted.status_code,200,adopted.text)
        self.assertEqual(adopted.json()['model_calls'],0);self.assertEqual(adopted.json()['calls'],5)
        self.assertEqual(len(adopted.json()['submitted_ids']),12)

    def test_pause_does_not_cancel_or_repeat_inflight_call(self):
        entered=threading.Event();release=threading.Event();original=self.gateway.invoke
        def slow(*args):entered.set();release.wait(5);return original(*args)
        self.gateway.invoke=slow
        try:
            response=self.client.post(self.run_url+'/start',json=self.dispatch(0));self.assertEqual(response.status_code,200)
            self.assertTrue(entered.wait(3))
            paused=self.client.post(self.run_url+'/pause',json={});self.assertEqual(paused.status_code,200,paused.text)
            # Reject/adopt cannot race a live call.
            self.assertEqual(self.client.post(self.run_url+'/review',json={'adopt':False}).status_code,429)
        finally:release.set()
        result=self.wait(lambda r:not r['worker_active'])
        self.assertEqual(result['cursor'],2);self.assertEqual(result['calls'],1);self.assertTrue(result['pause_requested'])
        self.assertEqual(self.client.post(self.run_url+'/next',json=self.dispatch(1)).status_code,400)
        self.assertEqual(self.client.post(self.run_url+'/next',json=self.dispatch(2)).status_code,200)
        result=self.wait(lambda r:not r['worker_active']);self.assertEqual(result['cursor'],3);self.assertEqual(result['calls'],2)

    def test_unknown_call_blocks_restart_and_requires_risk_ack(self):
        def fail(*args):raise TimeoutError('upstream test')
        self.gateway.invoke=fail
        self.client.post(self.run_url+'/start',json=self.dispatch(0))
        result=self.wait(lambda r:not r['worker_active']);self.assertEqual(result['status'],'result_unknown')
        self.assertEqual(self.client.post(self.run_url+'/start',json=self.dispatch(1)).status_code,400)
        self.assertEqual(self.client.post(self.run_url+'/review',json={'adopt':False}).status_code,400)
        response=self.client.post(self.run_url+'/review',json={'adopt':False,'acknowledge_unknown':True});self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['calls'],1)

    def test_free_create_retry_same_key_same_run(self):
        result=self.client.post(self.base+'/runs',json={**self.body,'request_key':'api-test','context_digest':self.spec['context_digest']})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['id'],self.rid)
        self.assertEqual(self.gateway.contexts,[])

    def test_explicit_review_preparation_invalidates_old_consent_digest(self):
        self.client.post(self.run_url+'/start',json=self.dispatch(0))
        self.wait(lambda r:r['status']=='completed_awaiting_review' and not r['worker_active'])
        path=api.mixed_path(self.sid);saved=seq.read_run(path,self.rid)
        saved['working']['pending_review_requests']=[{'candidate_ids':['h0'],'reason':'test requested review'}]
        seq.write_run(path,saved);calls=len(self.gateway.contexts)
        response=self.client.post(self.run_url+'/extend-review')
        self.assertEqual(response.status_code,200,response.text)
        prepared=response.json();self.assertEqual(len(self.gateway.contexts),calls)
        self.assertNotEqual(prepared['spec']['context_digest'],self.spec['context_digest'])
        self.assertEqual(self.client.post(self.run_url+'/next',json=self.dispatch(6)).status_code,400)
        dispatch={'expected_cursor':6,'context_digest':prepared['spec']['context_digest'],'consent':True}
        self.assertEqual(self.client.post(self.run_url+'/next',json=dispatch).status_code,200)
        done=self.wait(lambda r:r['cursor']==7 and not r['worker_active'])
        self.assertEqual(done['calls'],6)
        self.assertEqual(done['steps'][-1]['context']['sequential_step']['new_unit_count'],0)
