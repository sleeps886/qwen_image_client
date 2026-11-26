import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.api.routers import incremental as api
from backend.app.main import app
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association.incremental_prediction import create_state,prepare_round,apply_answer,public_state
from backend.app.services.association.incremental_prediction import save_state


def dataset():
    return {'candidates':[{'candidate_id':f'P{i}','t':i*10,'bearing_deg':30+i,
                           'array':'hull','platform_id':'A'} for i in range(10)]}


def answer(context,decision='extend',ids=None):
    return json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],
        'decision':decision,'accepted_candidate_ids':ids if ids is not None else [p['candidate_id'] for p in context['new_candidates']],
        'prediction':[{'t':context['visible_until']+10,'bearing_deg':40}], 'reason':'点位趋势支持'})


class PredictionTests(unittest.TestCase):
    def test_complete_json_fence_accepted_but_constraints_still_enforced(self):
        state=create_state(dataset(),['P0','P1','P2']);context,_=prepare_round(state)
        for fence in ('```json','```JSON','```'):
            result=apply_answer(state,context,fence+'\n'+answer(context)+'\n```')
            self.assertEqual(result['version'],1)
        doc=json.loads(answer(context));doc['accepted_candidate_ids']=['P9']
        with self.assertRaises(ValueError):apply_answer(state,context,'```json\n'+json.dumps(doc)+'\n```')
        for text in ('说明：'+answer(context),'```json\n'+answer(context)+'\n```\n其他文字',
                     '```python\n'+answer(context)+'\n```'):
            with self.assertRaises(ValueError):apply_answer(state,context,text)

    def test_single_point_has_no_invented_prediction_then_accumulates(self):
        state=create_state(dataset(),['P0']);context,prompt=prepare_round(state)
        self.assertEqual(context['baseline_prediction'],[])
        self.assertFalse(context['evidence_support']['trend_available'])
        self.assertNotIn('P1',prompt)
        with self.assertRaises(ValueError):apply_answer(state,context,answer(context))
        doc=json.loads(answer(context,'ambiguous',[]));doc['prediction']=[]
        state=apply_answer(state,context,json.dumps(doc))
        self.assertEqual(state['status'],'ambiguous')
        context,_=prepare_round(state)
        self.assertEqual([p['candidate_id'] for p in context['new_candidates']],['P1'])
        state=apply_answer(state,context,answer(context))
        context,_=prepare_round(state)
        self.assertTrue(context['evidence_support']['trend_available'])
        self.assertEqual([p['t'] for p in context['baseline_prediction']],[30,40])
        self.assertEqual(len(state['history']),2)

    def test_reference_fit_uses_multiple_past_points_not_future(self):
        data=dataset();data['candidates'][2]['bearing_deg']=33
        state=create_state(data,['P0','P1','P2'])
        context,_=prepare_round(state)
        self.assertEqual(context['evidence_support']['reference_method'],'recent_4_point_linear_fit')
        data['candidates'][-1]['bearing_deg']=170
        other,_=prepare_round(create_state(data,['P0','P1','P2']))
        self.assertEqual(context['baseline_prediction'],other['baseline_prediction'])

    def test_unconfirmed_past_evidence_survives_without_future_leak(self):
        state=create_state(dataset(),['P0'])
        for _ in range(2):
            context,_=prepare_round(state)
            doc=json.loads(answer(context,'ambiguous',[]));doc['prediction']=[]
            state=apply_answer(state,context,json.dumps(doc))
        context,prompt=prepare_round(state)
        self.assertEqual(context['recent_unconfirmed_observations'][0]['candidates'][0]['candidate_id'],'P1')
        self.assertEqual(len(context['history']),1)
        self.assertEqual(context['new_candidates'][0]['candidate_id'],'P2')
        self.assertNotIn('P3',prompt)

    def test_atomic_save_retries_transient_windows_reader_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'execution.json';replace=Path.replace;attempts=[]
            def flaky(source,target):
                attempts.append(1)
                if len(attempts)==1:raise PermissionError('temporary reader lock')
                return replace(source,target)
            with patch.object(Path,'replace',flaky):save_state(path,{'status':'completed'})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'],'completed')
            self.assertEqual(len(attempts),2)
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
        self.assertIn('进入新版：纯点位增量预测',self.client.get('/').text)
        self.assertEqual(self.client.get('/incremental').status_code,200)
        self.assertEqual(self.client.get('/assets/incremental.js').status_code,200)
        self.assertEqual(self.client.get('/assets/workspace-cache.js').status_code,200)
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
        self.assertEqual(self.client.post(url.removesuffix('/step')+'/review',json={'version':1,'adopt':True}).status_code,200)
        response=self.client.post(url,json={'version':2,'consent':True})
        self.assertEqual(response.json()['history'][-1]['candidate_id'],'P3')
        self.assertTrue((self.root/sid/'attempt_002.json').exists())
        stored=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(stored['calls'],2);self.assertEqual(stored['version'],3)

    def test_failure_is_not_retried(self):
        class Failing:
            def invoke(self,*args):raise TimeoutError('private upstream error')
        app.dependency_overrides[api.get_prediction_gateway]=Failing
        sid=self.create();url=f'/api/incremental/sessions/{sid}/step'
        response=self.client.post(url,json={'version':0,'consent':True})
        self.assertEqual(response.status_code,502)
        self.assertNotIn('private',response.text)
        execution=self.client.get(f'/api/incremental/sessions/{sid}/execution').json()
        self.assertEqual(self.client.post(url,json={'version':0,'consent':True}).status_code,400)
        state=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(state['status'],'call_unknown');self.assertEqual(state['calls'],1)
        self.assertEqual(state['version'],0)
        self.assertEqual(execution['status'],'failed')
        events={n['node']:n for n in execution['nodes']}
        self.assertEqual(events['model_prediction']['status'],'failed')
        self.assertEqual(events['answer_validation']['status'],'skipped')
        self.assertNotIn('private',json.dumps(execution))

    def test_execution_snapshot_is_visible_during_gateway_call(self):
        entered=threading.Event();release=threading.Event();responses=[]
        class Waiting:
            def invoke(self,images,prompt,*args):
                entered.set();release.wait(5)
                context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1])
                return {'answer':answer(context)}
        app.dependency_overrides[api.get_prediction_gateway]=Waiting
        sid=self.create();url=f'/api/incremental/sessions/{sid}'
        self.assertEqual(self.client.get(url+'/execution').json()['status'],'not_started')
        worker=threading.Thread(target=lambda:responses.append(self.client.post(url+'/step',json={'version':0,'consent':True})))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            snapshot=self.client.get(url+'/execution').json()
            events={n['node']:n for n in snapshot['nodes']}
            self.assertEqual(events['platform_assessment']['status'],'completed')
            self.assertEqual(events['model_prediction']['status'],'running')
            self.assertEqual(events['answer_validation']['status'],'pending')
        finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(responses[0].status_code,200)
        done=self.client.get(url+'/execution').json()
        self.assertEqual(done['status'],'waiting_for_user')
        self.assertTrue(all(n['elapsed_ms']>=0 for n in done['nodes'][:-1]))
        self.assertTrue(all(n['status']=='completed' for n in done['nodes'][:-1]))
        self.assertEqual(done['nodes'][-1]['node'],'trajectory_review')

    def test_invalid_response_does_not_commit(self):
        class Invalid:
            def invoke(self,*args):return {'answer':'not JSON'}
        app.dependency_overrides[api.get_prediction_gateway]=Invalid
        sid=self.create();url=f'/api/incremental/sessions/{sid}/step'
        self.assertEqual(self.client.post(url,json={'version':0,'consent':True}).status_code,502)
        state=self.client.get(f'/api/incremental/sessions/{sid}').json()
        self.assertEqual(state['status'],'invalid_response');self.assertEqual(len(state['history']),3)

    def test_chat_preview_question_and_stale_digest(self):
        calls=[]
        class ChatGateway:
            def invoke(self,images,prompt,settings,output_root,mode):
                calls.append(prompt)
                context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1])
                return {'answer':answer(context)}
        app.dependency_overrides[api.get_prediction_gateway]=ChatGateway
        sid=self.create();url=f'/api/incremental/sessions/{sid}'
        preview=self.client.get(url+'/preview')
        self.assertEqual(preview.status_code,200)
        self.assertNotIn('P3',preview.text);self.assertEqual(calls,[])
        self.assertEqual(self.client.post(url+'/step',json={'version':0,'consent':True,'question':'为什么这样延续？','context_digest':'0'*64}).status_code,400)
        self.assertEqual(calls,[])
        reply=self.client.post(url+'/step',json={'version':0,'consent':True,'question':'为什么这样延续？','context_digest':preview.json()['context_digest']})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertIn('为什么这样延续',calls[0]);self.assertNotIn('P3',calls[0])
        self.assertEqual(reply.json()['rounds'][0]['user_question'],'为什么这样延续？')
        self.assertEqual(self.client.get(url+'/preview').status_code,400)
        self.client.post(url+'/review',json={'version':1,'adopt':True})
        next_preview=self.client.get(url+'/preview').json()
        self.assertEqual(next_preview['context']['new_candidates'][0]['candidate_id'],'P3')
        self.assertNotIn('P4',json.dumps(next_preview))

    def test_chat_observations_are_used_and_validated_before_model(self):
        data=dataset();data['candidates'].append(dict(data['candidates'][3],candidate_id='Q3',bearing_deg=60))
        response=self.client.post('/api/incremental/sessions',json={'dataset':data,'seed_ids':['P0','P1','P2']})
        sid=response.json()['session_id'];url=f'/api/incremental/sessions/{sid}';contexts=[]
        class Fake:
            def invoke(self,images,prompt,settings,output_root,mode):
                context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1]);contexts.append(context)
                return {'answer':answer(context)}
        app.dependency_overrides[api.get_prediction_gateway]=Fake
        self.assertEqual(self.client.post(url+'/step',json={'version':0,'consent':True}).status_code,200)
        self.client.post(url+'/review',json={'version':1,'adopt':True})
        point={'candidate_id':'Q3','t':30,'bearing_deg':60,'array':'hull','platform_id':'A'}
        for wrong in (dict(point,bearing_deg=61),dict(point,candidate_id='P9'),dict(point,t=True)):
            reply=self.client.post(url+'/step',json={'version':2,'consent':True,'observations':[wrong]})
            self.assertEqual(reply.status_code,400,reply.text)
        self.assertEqual(len(contexts),1)
        preview=self.client.get(url+'/preview').json()
        reply=self.client.post(url+'/step',json={'version':2,'consent':True,'observations':[point],'context_digest':preview['context_digest']})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual([p['candidate_id'] for p in contexts[-1]['new_candidates']],['Q3'])
        self.assertEqual(reply.json()['history'][-1]['candidate_id'],'Q3')

    def test_selected_time_groups_include_both_arrays_and_only_selected_times(self):
        data=dataset();data['candidates'] += [dict(p,candidate_id='T'+p['candidate_id'][1:],array='tow',bearing_deg=p['bearing_deg']+5) for p in list(data['candidates'])]
        created=self.client.post('/api/incremental/sessions',json={'dataset':data,'seed_ids':['P0','P1','P2']}).json()
        self.assertNotIn('T9',json.dumps(created));self.assertNotIn('companion_points',created)
        url=f"/api/incremental/sessions/{created['session_id']}";contexts=[]
        class Both:
            def invoke(self,images,prompt,*args):
                self_context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1]);contexts.append(self_context)
                return {'answer':answer(self_context)}
        app.dependency_overrides[api.get_prediction_gateway]=Both
        times=[0,10,20,40]
        preview=self.client.get(url+'/preview',params={'times':json.dumps(times)})
        self.assertEqual(preview.status_code,200,preview.text)
        context=preview.json()['context']
        self.assertEqual(context['selected_time_groups'][-1]['tow'][0]['candidate_id'],'T4')
        self.assertNotIn('T5',preview.text);self.assertNotIn('P3',preview.text)
        result=self.client.post(url+'/step',json={'version':0,'consent':True,'selected_times':times,'context_digest':preview.json()['context_digest']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(contexts[0]['visible_until'],40)
        self.assertEqual(contexts[0]['new_candidates'][0]['candidate_id'],'P4')
        self.assertEqual(contexts[0]['selected_time_groups'][-1]['tow'][0]['candidate_id'],'T4')
        self.assertEqual(self.client.post(url+'/step',json={'version':1,'consent':True,'selected_times':[40]}).status_code,400)
        self.assertEqual(self.client.post(url+'/step',json={'version':1,'consent':True,'selected_times':[999]}).status_code,400)
        self.assertEqual(len(contexts),1)

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
