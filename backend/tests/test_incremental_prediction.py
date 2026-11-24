import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.api.routers import incremental as api
from backend.app.main import app
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association.incremental_prediction import create_state,prepare_round,apply_answer,public_state


def dataset():
    return {'candidates':[{'candidate_id':f'P{i}','t':i*10,'bearing_deg':30+i,
                           'array':'hull','platform_id':'A'} for i in range(10)]}


def answer(context,decision='extend',ids=None):
    return json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],
        'decision':decision,'accepted_candidate_ids':ids if ids is not None else [p['candidate_id'] for p in context['new_candidates']],
        'prediction':[{'t':context['visible_until']+10,'bearing_deg':40}], 'reason':'点位趋势支持'})


class PredictionTests(unittest.TestCase):
    def test_visibility_and_new_point_feedback(self):
        state=create_state(dataset(),['P0','P1','P2'])
        context,prompt=prepare_round(state)
        self.assertNotIn('P3',prompt)
        self.assertEqual(len(public_state(state)['visible_candidates']),3)
        state=apply_answer(state,context,answer(context))
        context,prompt=prepare_round(state)
        self.assertEqual([p['candidate_id'] for p in context['new_candidates']],['P3'])
        self.assertNotIn('P4',prompt)
        self.assertEqual(context['previous_prediction_evaluation'][0]['absolute_error_deg'],7)
        updated=apply_answer(state,context,answer(context))
        self.assertEqual(updated['history'][-1]['candidate_id'],'P3')
        self.assertEqual(state['visible_until'],20)
        self.assertEqual(state['rounds'][0]['model_answer']['prediction'][0]['bearing_deg'],40)

    def test_reject_invalid_seeds_and_points(self):
        for seeds in (['P0','P0','P1'],['P0','P1','missing']):
            with self.assertRaises(ValueError):create_state(dataset(),seeds)
        bad=dataset();bad['candidates'][0]['t']=float('nan')
        with self.assertRaises(ValueError):create_state(bad,['P0','P1','P2'])
        bad=dataset();bad['candidates'][1]['array']='tow'
        with self.assertRaises(ValueError):create_state(bad,['P0','P1','P2'])

    def test_reject_future_reference_version_and_bad_prediction(self):
        state=create_state(dataset(),['P0','P1','P2']);context,_=prepare_round(state)
        for changes in ({'accepted_candidate_ids':['P9']},{'step_id':True},
                        {'prediction':[{'t':20,'bearing_deg':30}]},
                        {'prediction':[{'t':30,'bearing_deg':181}]},
                        {'prediction':[{'t':30,'bearing_deg':float('nan')}]},
                        {'prediction':[{'t':999,'bearing_deg':30}]}):
            doc=json.loads(answer(context));doc.update(changes)
            with self.assertRaises(ValueError):apply_answer(state,context,json.dumps(doc))

    def test_competing_points_same_time_not_both_accepted(self):
        data=dataset();data['candidates'].append(dict(data['candidates'][3],candidate_id='Q3'))
        state=create_state(data,['P0','P1','P2']);context,_=prepare_round(state)
        state=apply_answer(state,context,answer(context));context,_=prepare_round(state)
        with self.assertRaises(ValueError):apply_answer(state,context,answer(context))
        updated=apply_answer(state,context,answer(context,'ambiguous',[]))
        self.assertEqual(updated['status'],'ambiguous');self.assertEqual(len(updated['history']),3)

    def test_evidence_import_and_budget(self):
        data=dataset();evidence={'images':[{'array':'hull','platform_id':'A','candidates':[
            {'id':p['candidate_id'],'t':p['t'],'bearing_deg':p['bearing_deg']} for p in data['candidates']]}]}
        state=create_state(evidence,['P0','P1','P2']);state['calls']=state['max_calls']
        with self.assertRaises(ValueError):prepare_round(state)

    def test_stride_missing_and_end(self):
        state=create_state(dataset(),['P0','P1','P2'],stride=2)
        context,_=prepare_round(state);state=apply_answer(state,context,answer(context))
        context,_=prepare_round(state)
        self.assertEqual([p['candidate_id'] for p in context['new_candidates']],['P3','P4'])
        state=apply_answer(state,context,answer(context,'missed',[]))
        self.assertEqual(state['visible_until'],40);self.assertEqual(len(state['history']),3)
        context,_=prepare_round(state);doc=json.loads(answer(context,'end',[]));doc['prediction']=[]
        state=apply_answer(state,context,json.dumps(doc))
        self.assertEqual(state['status'],'ended')
        with self.assertRaises(ValueError):prepare_round(state)


class PredictionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.patches=[patch.object(api,'ROOT',self.root),patch.object(api,'model_settings',return_value=ModelSettings(
            model='test',base_url='https://example.invalid',api_key='fake',api_format='openai'))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)
        self.addCleanup(app.dependency_overrides.clear)

    def create(self):
        response=self.client.post('/api/incremental/sessions',json={'dataset':dataset(),'seed_ids':['P0','P1','P2']})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['session_id']

    def test_page_assets_and_free_create(self):
        self.assertEqual(self.client.get('/incremental').status_code,200)
        self.assertEqual(self.client.get('/assets/incremental.js').status_code,200)
        sid=self.create();result=self.client.get('/api/incremental/sessions/'+sid).json()
        self.assertEqual(result['calls'],0);self.assertNotIn('points',result)
        self.assertNotIn('P9',json.dumps(result))

    def test_steps_are_text_only_persistent_and_stale_safe(self):
        calls=[]
        class Fake:
            def invoke(self,images,prompt,settings,output_root,mode):
                calls.append((images,mode,prompt))
                context=json.loads(prompt.split('\n',1)[1])
                return {'answer':answer(context),'usage':{'total_tokens':10}}
        app.dependency_overrides[api.get_prediction_gateway]=Fake
        sid=self.create();url=f'/api/incremental/sessions/{sid}/step'
        self.assertEqual(self.client.post(url,json={'version':0}).status_code,400)
        response=self.client.post(url,json={'version':0,'consent':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(calls[0][:2],([], 'data'))
        self.assertNotIn('P3',calls[0][2])
        self.assertEqual(self.client.post(url,json={'version':0,'consent':True}).status_code,400)
        self.assertEqual(len(calls),1)
        response=self.client.post(url,json={'version':1,'consent':True})
        self.assertEqual(response.json()['history'][-1]['candidate_id'],'P3')
        self.assertTrue((self.root/sid/'attempt_002.json').exists())
        stored=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(stored['calls'],2);self.assertEqual(stored['version'],2)

    def test_failure_is_not_retried(self):
        class Failing:
            def invoke(self,*args):raise TimeoutError('private upstream error')
        app.dependency_overrides[api.get_prediction_gateway]=Failing
        sid=self.create();url=f'/api/incremental/sessions/{sid}/step'
        response=self.client.post(url,json={'version':0,'consent':True})
        self.assertEqual(response.status_code,502)
        self.assertNotIn('private',response.text)
        self.assertEqual(self.client.post(url,json={'version':0,'consent':True}).status_code,400)
        state=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(state['status'],'call_unknown');self.assertEqual(state['calls'],1)
        self.assertEqual(state['version'],0)

    def test_invalid_response_does_not_commit(self):
        class Invalid:
            def invoke(self,*args):return {'answer':'not JSON'}
        app.dependency_overrides[api.get_prediction_gateway]=Invalid
        sid=self.create();url=f'/api/incremental/sessions/{sid}/step'
        self.assertEqual(self.client.post(url,json={'version':0,'consent':True}).status_code,502)
        state=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(state['status'],'invalid_response');self.assertEqual(len(state['history']),3)

    def test_pending_budget_and_connection_override_never_call(self):
        class Forbidden:
            def invoke(self,*args):raise AssertionError('must not call provider')
        app.dependency_overrides[api.get_prediction_gateway]=Forbidden
        sid=self.create();path=self.root/sid/'state.json'
        for changes in ({'status':'pending'},{'status':'ready','calls':20}):
            state=json.loads(path.read_text(encoding='utf-8'));state.update(changes)
            path.write_text(json.dumps(state),encoding='utf-8')
            response=self.client.post(f'/api/incremental/sessions/{sid}/step',json={'version':0,'consent':True})
            self.assertEqual(response.status_code,400)
        response=self.client.post('/api/incremental/sessions',json={'dataset':dataset(),'seed_ids':['P0','P1','P2'],'api_key':'not-allowed'})
        self.assertEqual(response.status_code,422)


if __name__=='__main__':unittest.main()
