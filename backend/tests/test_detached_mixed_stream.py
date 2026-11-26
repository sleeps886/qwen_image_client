"""Connection cancellation tests with an offline gateway, never paid retries."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.app.services.jobs.detached_sse import detached_sse,LIVE_TASKS
from backend.app.services.association.mixed_prediction import prepare_mixed


class DetachedMixedStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for replacement in (patch.object(api,'MIXED_ROOT',Path(self.temp.name)),
                            patch.object(api,'model_settings',return_value=ModelSettings(model='offline',base_url='http://invalid.local',api_key='offline')),
                            patch.object(api,'detached_sse',side_effect=lambda source,release:detached_sse(source,release,heartbeat_seconds=.01))):
            replacement.start();self.addCleanup(replacement.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)
        points=[dict(candidate_id=f'{array}-{t}',platform_id='P',array=array,t=t,bearing_deg=30+t) for t in range(2) for array in ('hull','tow')]
        self.sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':{'candidates':points}}).json()['session_id']
        self.path=api.mixed_path(self.sid)
        state=json.loads(self.path.read_text(encoding='utf-8'));context,_=prepare_mixed(state,[p['candidate_id'] for p in points])
        self.body=api.MixedSelection(version=0,selected_ids=[p['candidate_id'] for p in points],consent=True,context_digest=api.context_digest(context))
        self.gate=asyncio.Event();self.started=asyncio.Event();self.calls=0;self.cancelled=False
        owner=self
        class Fake:
            async def stream(self,images,prompt,*args):
                owner.calls+=1;owner.started.set()
                try:await owner.gate.wait()
                except asyncio.CancelledError:owner.cancelled=True;raise
                context=json.loads(prompt.split('\n',1)[1])
                answer=json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],'tracks':[],'unassigned':[],'ambiguous':[]})
                yield {'event':'reasoning','text':'PRIVATE_PROVIDER_REASONING_NOT_FOR_UI'}
                yield {'event':'delta','text':answer}
                yield {'event':'model_complete','result':{'answer':answer}}
        self.fake=Fake()

    async def asyncTearDown(self):
        self.gate.set()
        if LIVE_TASKS:await asyncio.gather(*list(LIVE_TASKS),return_exceptions=True)

    def response(self):return api.stream_mixed_model(self.sid,self.body,self.fake,plot=False)

    async def next_data(self,response):
        # Heartbeats may legitimately arrive while CPU preparation runs.
        async with asyncio.timeout(5):
            while True:
                chunk=await anext(response.body_iterator)
                if not chunk.startswith(':'):return chunk

    async def finish(self):
        self.gate.set()
        await asyncio.wait_for(asyncio.gather(*list(LIVE_TASKS)),2)
        return json.loads(self.path.read_text(encoding='utf-8'))

    async def test_disconnect_does_not_cancel_original_call_or_release_its_lock(self):
        response=self.response()
        self.assertIn('context',await self.next_data(response));await self.started.wait()
        await response.body_iterator.aclose();await response.background()
        self.assertFalse(self.cancelled)
        self.assertTrue(api.LOCK.locked())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['status'],'pending')
        state=await self.finish()
        self.assertEqual((state['status'],state['calls']),('awaiting_review',1))
        self.assertEqual(self.calls,1);self.assertFalse(self.cancelled)
        self.assertFalse(api.LOCK.locked())

    async def test_cancel_waiting_http_observer_does_not_cancel_provider(self):
        with patch.object(api,'detached_sse',side_effect=lambda source,release:detached_sse(source,release,heartbeat_seconds=60)):
            response=self.response()
        await anext(response.body_iterator);await self.started.wait()
        observer=asyncio.create_task(anext(response.body_iterator));await asyncio.sleep(0)
        observer.cancel()
        with self.assertRaises(asyncio.CancelledError):await observer
        await response.background()
        self.assertFalse(self.cancelled);self.assertTrue(api.LOCK.locked())
        self.assertEqual((await self.finish())['status'],'awaiting_review')
        self.assertEqual(self.calls,1)

    async def test_idle_heartbeat_then_normal_result_one_call(self):
        response=self.response();await self.next_data(response);await self.started.wait()
        self.assertEqual(await anext(response.body_iterator),': keep-alive\n\n')
        self.gate.set()
        chunks=[chunk async for chunk in response.body_iterator];await response.background()
        self.assertTrue(any('"event": "result"' in chunk for chunk in chunks))
        self.assertNotIn('PRIVATE_PROVIDER_REASONING_NOT_FOR_UI',''.join(chunks))
        self.assertEqual(self.calls,1);self.assertFalse(api.LOCK.locked())

    async def test_real_asgi_disconnect_leaves_background_job_running(self):
        response=self.response();disconnected=asyncio.Event()
        async def receive():
            await disconnected.wait()
            return {'type':'http.disconnect'}
        async def send(message):pass
        observer=asyncio.create_task(response({'type':'http','asgi':{'spec_version':'2.0'}},receive,send))
        await asyncio.wait_for(self.started.wait(),2);disconnected.set()
        await asyncio.wait_for(observer,2)
        self.assertTrue(api.LOCK.locked());self.assertFalse(self.cancelled)
        self.assertEqual((await self.finish())['status'],'awaiting_review')
        self.assertEqual(self.calls,1)

    async def test_server_task_cancellation_still_marks_unknown_not_success(self):
        response=self.response();await anext(response.body_iterator);await self.started.wait()
        jobs=list(LIVE_TASKS)
        for task in jobs:task.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)
        await response.body_iterator.aclose();await response.background()
        state=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual((state['status'],state['calls']),('call_unknown',1))
        self.assertFalse(api.LOCK.locked());self.assertTrue(self.cancelled)

    async def test_unstarted_response_releases_lock_without_call(self):
        response=self.response();self.assertTrue(api.LOCK.locked())
        await response.body_iterator.aclose();await response.background()
        self.assertEqual(self.calls,0);self.assertFalse(api.LOCK.locked())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['status'],'ready')

    async def test_returned_invalid_reply_has_validation_code_not_disconnect(self):
        class Invalid:
            async def stream(self,images,prompt,*args):
                context=json.loads(prompt.split('\n',1)[1])
                text=json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],
                    'tracks':[{'track_ref':'N3','observation_ids':['O00011'],'reason':'离线测试'}]})
                yield {'event':'delta','text':text}
                yield {'event':'model_complete','result':{'answer':text}}
        response=api.stream_mixed_model(self.sid,self.body,Invalid(),plot=False)
        chunks=[chunk async for chunk in response.body_iterator];await response.background()
        events=[json.loads(line[6:]) for chunk in chunks for line in chunk.splitlines() if line.startswith('data: ')]
        error=next(e for e in events if e['event']=='error')
        self.assertEqual(error['code'],'reply_validation_failed')
        self.assertIn('O00011',error['message']);self.assertIn('N3',error['message'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['status'],'invalid_response')

    async def test_output_exhaustion_is_distinguished_and_not_retried(self):
        from backend.app.infrastructure.llm.client import ClientError
        class Exhausted:
            async def stream(self,*args):
                yield {'event':'delta','text':'{"tracks":['}
                raise ClientError('上游输出额度或上下文上限已用尽（max_tokens）；正文可能被截断')
        response=api.stream_mixed_model(self.sid,self.body,Exhausted(),plot=False)
        chunks=[chunk async for chunk in response.body_iterator];await response.background()
        events=[json.loads(line[6:]) for chunk in chunks for line in chunk.splitlines() if line.startswith('data: ')]
        self.assertEqual(next(e for e in events if e['event']=='error')['code'],'model_output_truncated')
        state=json.loads(self.path.read_text(encoding='utf-8'));self.assertEqual((state['status'],state['calls']),('call_unknown',1))
        trace=json.loads((self.path.parent/'execution.json').read_text(encoding='utf-8'))
        self.assertIn('正文被截断',next(n for n in trace['nodes'] if n['node']=='associate')['error_detail'])
