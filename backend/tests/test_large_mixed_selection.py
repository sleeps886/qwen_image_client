"""Large full-selection preparation is local; never use a real model gateway."""
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.infrastructure.llm.client import build_request,ClientError
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS


def large_dataset(count=1224):
    return {'candidates':[{'candidate_id':f'point-{i:05d}','platform_id':'P',
                          'array':'hull' if i%2==0 else 'tow','t':(i//2)%170,
                          'bearing_deg':30+(i//2)%170*.1+((i//340)%4)*15}
                         for i in range(count)]}


class LargeMixedSelectionTests(unittest.TestCase):
    def test_prepare_1224_points_without_gateway_or_truncation(self):
        with tempfile.TemporaryDirectory() as root, patch.object(api,'MIXED_ROOT',Path(root)), \
                patch.object(api,'get_prediction_gateway',side_effect=AssertionError('No gateway allowed')):
            client=TestClient(app,base_url='http://127.0.0.1')
            data=large_dataset()
            response=client.post('/api/incremental/mixed-sessions',json={'dataset':data})
            self.assertEqual(response.status_code,200,response.text)
            sid=response.json()['session_id']
            body={'version':0,'selected_ids':[p['candidate_id'] for p in data['candidates']]}
            response=client.post(f'/api/incremental/mixed-sessions/{sid}/preview',json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['context']['observations']),1224)
            self.assertEqual(response.json()['model_calls'],0)
            saved=client.get(f'/api/incremental/mixed-sessions/{sid}').json()
            self.assertEqual(saved['calls'],0)
            self.assertEqual(saved['status'],'ready')
            prompt=response.json()['context']
            text=json.dumps(prompt,ensure_ascii=False)
            self.assertGreater(len(text),50000)
            # Request assembly is also verified offline; preview success alone
            # must not hide the transport's old 50k rejection.
            payload,_=build_request([],text,'offline-test',allow_text_only=True,max_prompt_chars=PREDICTION_PROMPT_CHARS)
            self.assertEqual(payload['messages'][1]['content'][-1]['text'],text)
            with self.assertRaises(ClientError):build_request([],text,'offline-test',allow_text_only=True)

    def test_prediction_gateway_forwards_budget_to_both_transports(self):
        from backend.app.models.model_settings import ModelSettings
        import asyncio
        gateway=api.get_prediction_gateway()
        settings=ModelSettings(api_key='offline-placeholder',model='offline',base_url='http://invalid.local')
        with patch('backend.app.infrastructure.llm.gateway.client.send_images',return_value={}) as send:
            gateway.invoke([],'offline',settings,Path('.'),'data')
            self.assertEqual(send.call_args.kwargs['max_prompt_chars'],PREDICTION_PROMPT_CHARS)
        async def fake_stream(*args,**kwargs):
            self.assertEqual(kwargs['max_prompt_chars'],PREDICTION_PROMPT_CHARS)
            yield {'event':'offline'}
        async def consume():
            return [event async for event in gateway.stream([],'offline',settings,Path('.'),'data')]
        with patch('backend.app.infrastructure.llm.gateway.stream_client.stream_images',side_effect=fake_stream):
            self.assertEqual(asyncio.run(consume()),[{'event':'offline'}])

    def test_large_round_can_reach_isolated_fake_gateway_once(self):
        from backend.app.models.model_settings import ModelSettings
        contexts=[]
        class Fake:
            def invoke(self,images,prompt,settings,output_root,mode):
                context=json.loads(prompt.split('\n',1)[1]);contexts.append(context)
                return {'answer':json.dumps({'step_id':context['step_id'],
                    'base_state_version':context['base_state_version'],'tracks':[],
                    'unassigned':[],'ambiguous':[]})}
        with tempfile.TemporaryDirectory() as root, patch.object(api,'MIXED_ROOT',Path(root)), \
                patch.dict(app.dependency_overrides,{api.get_prediction_gateway:lambda:Fake()}), \
                patch.object(api,'model_settings',return_value=ModelSettings(api_key='offline',model='fake',base_url='http://invalid.local')):
            client=TestClient(app,base_url='http://127.0.0.1');data=large_dataset()
            sid=client.post('/api/incremental/mixed-sessions',json={'dataset':data}).json()['session_id']
            url=f'/api/incremental/mixed-sessions/{sid}'
            body={'version':0,'selected_ids':[p['candidate_id'] for p in data['candidates']]}
            preview=client.post(url+'/preview',json=body)
            self.assertEqual(preview.status_code,200,preview.text)
            body.update(consent=True,context_digest=preview.json()['context_digest'])
            response=client.post(url+'/step',json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(contexts),1)
            self.assertEqual(len(contexts[0]['observations']),1224)
            self.assertEqual(response.json()['status'],'awaiting_review')

    def test_selection_schema_has_no_count_cap(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        schema=client.get('/openapi.json').json()['components']['schemas']['MixedSelection']
        self.assertNotIn('maxItems',schema['properties']['selected_ids'])
        ids=[f'point-{i}' for i in range(20002)]
        self.assertEqual(api.MixedSelection(version=0,selected_ids=ids).selected_ids,ids)
        response=client.post('/api/incremental/mixed-sessions/not-real/preview',json={'version':0,'selected_ids':[]})
        self.assertEqual(response.status_code,422)

    def test_mixed_dataset_has_no_20000_point_cap(self):
        from backend.app.services.association.mixed_prediction import create_mixed
        state=create_mixed(large_dataset(20002))
        self.assertEqual(len(state['points']),20002)

    def test_prepare_over_4096_preserves_every_selected_point(self):
        from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed
        state=create_mixed(large_dataset(5000));ids=[p['candidate_id'] for p in state['points']]
        # Numerical reference behavior is covered by the real 1224-point test;
        # isolate count acceptance here without costly repeated fitting.
        with patch('backend.app.services.association.mixed_prediction.association_links',return_value=[]), \
                patch('backend.app.services.association.mixed_prediction.tracklet_references',return_value=[]):
            context,prompt=prepare_mixed(state,ids)
        self.assertEqual(len(context['observations']),5000)
        self.assertNotIn('continuation_references_truncated',context)
        self.assertEqual({p['observation_id'] for p in context['observations']},set(state['aliases'].values()))

    def test_prediction_prompt_has_no_local_character_cap(self):
        text='观' * 1000001
        body,_=build_request([],text,'offline-test',allow_text_only=True,max_prompt_chars=None)
        self.assertEqual(body['messages'][1]['content'][-1]['text'],text)
        with self.assertRaises(ClientError):build_request([],text,'offline-test',allow_text_only=True)

    def test_manual_preparation_restores_large_cancelled_round_without_model(self):
        from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed
        from backend.app.services.association.manual_rerun import prepare_manual_rerun
        state=create_mixed(large_dataset());ids=[p['candidate_id'] for p in state['points']]
        context,_=prepare_mixed(state,ids);state.update(status='call_unknown',calls=1)
        new,prepared_context,prepared=prepare_manual_rerun(state,context,acknowledge_unknown=True)
        self.assertEqual(len(prepared['selected_ids']),1224)
        self.assertEqual(len(prepared_context['observations']),1224)
        self.assertEqual(new['calls'],1)
