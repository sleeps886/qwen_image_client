import copy
import json
import unittest
from fastapi.testclient import TestClient
from backend.tests.test_mixed_prediction import dataset,apply
from backend.tests import test_mixed_prediction as mixed_tests
from backend.tests.model_plot_fixture import plot_answer
from backend.app.services.association.mixed_prediction import create_mixed,review_mixed
from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot,normalize_review_requests
from backend.app.api.routers import incremental as api
from backend.app.main import app


class ModelPlotTests(unittest.TestCase):
    def setUp(self):
        s=apply(create_mixed(dataset()),['A038_0','A038_1'],[('N1',['A038_0','A038_1'])])
        self.state=review_mixed(s,s['version'],True);self.context,self.prompt=prepare_model_plot(self.state)
    def result(self):return plot_answer(self.context)
    def test_confirmation_does_not_predict(self):
        self.assertEqual(self.state['plot_status'],'awaiting_model');self.assertFalse(self.state['prediction_ledger']);self.assertFalse(self.state['tracks']['T001']['prediction'])
    def test_input_has_quality_not_directory_or_raw_ids(self):
        self.assertNotIn('A038',self.prompt);self.assertNotIn('A031',self.prompt);self.assertIn('sigma_deg',self.prompt)
        self.assertIn('unassigned',self.context);self.assertIn('numerical_checks',self.context)
    def test_model_values_preserved_not_replaced_by_linear_backend(self):
        r=self.result();r['tracks'][0]['prediction']=[{'t':30,'bearing_deg':35.123}]
        new=apply_model_plot(self.state,self.context,json.dumps(r));self.assertEqual(new['tracks']['T001']['prediction'][0]['bearing_deg'],35.123)
        self.assertEqual(new['prediction_ledger'][-1]['source'],'model');self.assertEqual(new['tracks']['T001']['plot_source'],'model')
        self.assertEqual(new['plot_status'],'awaiting_spatial');self.assertFalse(new['rounds'][-1]['model_plot']['spatial_result']['tracks'])
    def test_unconfirmed_reference_cannot_be_used_to_fit(self):
        r=self.result();r['tracks'][0]['segments'][0]['observation_ids'].append('O999999')
        with self.assertRaises(ValueError):apply_model_plot(self.state,self.context,json.dumps(r))
    def test_cannot_change_identity_or_omit_track(self):
        for tracks in ([],[{**self.result()['tracks'][0],'track_id':'T999'}]):
            r=self.result();r['tracks']=tracks
            with self.assertRaises(ValueError):apply_model_plot(self.state,self.context,json.dumps(r))
    def test_historical_curve_not_fake_observations_and_time_bounded(self):
        r=self.result();r['tracks'][0]['segments'][0]['curve'][0]['t']=-1
        with self.assertRaises(ValueError):apply_model_plot(self.state,self.context,json.dumps(r))
    def test_future_must_be_strictly_future(self):
        for t in (self.context['knowledge_cutoff'],self.context['prediction_end']+1):
            r=self.result();r['tracks'][0]['prediction']=[{'t':t,'bearing_deg':31}]
            with self.assertRaises(ValueError):apply_model_plot(self.state,self.context,json.dumps(r))
    def test_latest_observation_at_cutoff_does_not_forbid_future_prediction(self):
        self.assertIn('正常的预测起点',self.prompt)
        result=apply_model_plot(self.state,self.context,json.dumps(self.result()))
        self.assertTrue(result['tracks']['T001']['prediction'])
    def test_large_gaps_cannot_be_connected(self):
        data=dataset();data['candidates'].append({'candidate_id':'far','t':300,'bearing_deg':60,'array':'hull','platform_id':'P'})
        s=apply(create_mixed(data),['A038_0','A038_1','far'],[('N1',['A038_0','A038_1','far'])]);s=review_mixed(s,s['version'],True);ctx,_=prepare_model_plot(s);r=plot_answer(ctx)
        history=ctx['tracks']['T001']['history'];r['tracks'][0]['segments']=[{'observation_ids':[p['observation_id'] for p in history],'curve':[{'t':p['t'],'bearing_deg':p['bearing_deg']} for p in history]}]
        result=apply_model_plot(s,ctx,json.dumps(r));self.assertEqual(len(result['tracks']['T001']['model_segments']),2);self.assertTrue(result['rounds'][-1]['model_plot']['warnings'])
    def test_singleton_no_directional_prediction(self):
        s=apply(create_mixed(dataset()),['A038_0'],[('N1',['A038_0'])]);s=review_mixed(s,s['version'],True);ctx,_=prepare_model_plot(s);r=plot_answer(ctx)
        r['tracks'][0].update(decision='predict',prediction=[{'t':20,'bearing_deg':31}])
        new=apply_model_plot(s,ctx,json.dumps(r));self.assertFalse(new['tracks']['T001']['prediction']);self.assertTrue(new['rounds'][-1]['model_plot']['warnings'])
    def test_bad_speed_does_not_replace_model_by_linear_prediction(self):
        r=self.result();r['tracks'][0]['prediction']=[{'t':10.001,'bearing_deg':170}]
        new=apply_model_plot(self.state,self.context,json.dumps(r));self.assertFalse(new['tracks']['T001']['prediction']);self.assertTrue(new['rounds'][-1]['model_plot']['warnings'])
    def test_empty_forecast_is_valid_explained_model_decision(self):
        r=self.result();r['tracks'][0].update(decision='insufficient_evidence',prediction=[])
        new=apply_model_plot(self.state,self.context,json.dumps(r));self.assertFalse(new['tracks']['T001']['prediction'])
    def test_no_generation_for_unassigned_suggestion(self):
        r=self.result();r['unassigned_assessment']=[{'observation_id':self.context['tracks']['T001']['history'][0]['observation_id'],'reason':'非法挪用已归属点'}]
        with self.assertRaises(ValueError):apply_model_plot(self.state,self.context,json.dumps(r))

    def test_fragment_review_works_without_any_physical_target(self):
        before=copy.deepcopy(self.state);r=self.result()
        r['review_requests']=[{'fragment_id':'F001','reason':'复核早期未归属点'}]
        new=apply_model_plot(self.state,self.context,json.dumps(r))
        self.assertEqual(new['plot_status'],'awaiting_association_recheck')
        self.assertEqual(new['association_recheck_requests'][0]['fragment_id'],'F001')
        self.assertEqual(new['tracks']['T001']['history'],before['tracks']['T001']['history'])
        self.assertEqual(self.state,before)

    def test_unambiguous_legacy_target_field_is_only_a_fragment_review(self):
        r=self.result();r['review_requests']=[{'target_id':'T001','reason':'旧回复实际上请求复核片段'}]
        new=apply_model_plot(self.state,self.context,json.dumps(r))
        request=new['rounds'][-1]['model_plot']['review_requests'][0]
        self.assertEqual(request['fragment_id'],'F001');self.assertNotIn('target_id',request)
        self.assertTrue(any(w.get('code')=='legacy_fragment_review_reference' for w in new['rounds'][-1]['model_plot']['warnings']))

    def test_target_and_fragment_same_number_are_explicit_separate_scopes(self):
        ctx=copy.deepcopy(self.context);ctx['target_hypotheses']={'T001':{}}
        requests,targets,fragments,_=normalize_review_requests([{'target_id':'T001','reason':'目标复核'}],ctx)
        self.assertEqual(targets,{'T001'});self.assertFalse(fragments);self.assertEqual(requests[0]['scope'],'target')
        requests,targets,fragments,_=normalize_review_requests([{'fragment_id':'F001','reason':'片段复核'}],ctx)
        self.assertFalse(targets);self.assertEqual(fragments,{'T001'})

    def test_unknown_ambiguous_or_duplicate_review_references_remain_rejected(self):
        for requests in ([{'target_id':'T999','reason':'不存在'}],[{'fragment_id':'F999','reason':'不存在'}],
                         [{'target_id':'T001','fragment_id':'F001','reason':'范围不明确'}],
                         [{'target_id':'T001','reason':'旧别名'},{'fragment_id':'F001','reason':'重复'}]):
            with self.subTest(requests=requests),self.assertRaises(ValueError):normalize_review_requests(requests,self.context)


class ModelPlotApiTests(mixed_tests.MixedApiTests):
    # Inherit fixtures and existing API invariants; add actual SSE phase checks.
    def create_pending(self):
        sid=self.client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()}).json()['session_id'];url=f'/api/incremental/mixed-sessions/{sid}'
        owner=self
        class FakeStream:
            def invoke(self,*args):raise AssertionError('SSE must not call invoke')
            async def stream(self,images,prompt,settings,directory,mode):
                owner.calls+=1;ctx=json.loads(prompt.split('\n',1)[1])
                if ctx.get('task')=='model_bearing_plot':r=plot_answer(ctx)
                else:r={'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[{'track_ref':'N1','observation_ids':[p['observation_id'] for p in ctx['observations']],'reason':'比较时间方位连续性'}]}
                answer=json.dumps(r);yield {'event':'reasoning','text':'PRIVATE_PROVIDER_REASONING'}
                for i in range(0,len(answer),40):yield {'event':'delta','text':answer[i:i+40]}
                yield {'event':'model_complete','result':{'answer':answer,'usage':{'fake':True}}}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:FakeStream()
        b={'version':0,'selected_ids':['A038_0','A038_1'],'consent':True};b['context_digest']=self.client.post(url+'/preview',json=b).json()['context_digest']
        response=self.client.post(url+'/step?stream=true',json=b);self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('PRIVATE_PROVIDER_REASONING',response.text);events=[json.loads(x[6:]) for x in response.text.splitlines() if x.startswith('data: ')]
        self.assertIn('delta',[e['event'] for e in events]);self.assertEqual(events[-1]['event'],'result');self.assertEqual(self.calls,1)
        return sid,url
    def test_two_streamed_calls_separate_consent_digest_and_no_duplicate(self):
        sid,url=self.create_pending();confirmed=self.client.post(url+'/review',json={'version':1,'adopt':True}).json()
        self.assertFalse(confirmed['prediction_ledger']);self.assertEqual(self.calls,1)
        version=confirmed['version'];preview=self.client.post(url+'/plot-preview',json={'version':version}).json();self.assertEqual(self.calls,1)
        b={'version':version,'context_digest':preview['context_digest']}
        self.assertEqual(self.client.post(url+'/plot-step?stream=true',json=b).status_code,400);self.assertEqual(self.calls,1)
        b.update(consent=True,context_digest='0'*64);self.assertEqual(self.client.post(url+'/plot-step?stream=true',json=b).status_code,400);self.assertEqual(self.calls,1)
        b['context_digest']=preview['context_digest'];r=self.client.post(url+'/plot-step?stream=true',json=b)
        self.assertEqual(r.status_code,200,r.text);self.assertNotIn('PRIVATE_PROVIDER_REASONING',r.text)
        events=[json.loads(x[6:]) for x in r.text.splitlines() if x.startswith('data: ')];self.assertEqual(events[-1]['event'],'result')
        new=events[-1]['result'];self.assertEqual(self.calls,2);self.assertEqual(new['calls'],2);self.assertEqual(new['plot_status'],'awaiting_spatial')
        self.assertEqual(new['tracks']['T001']['plot_source'],'model')
        self.assertEqual(self.client.post(url+'/plot-step?stream=true',json=b).status_code,400);self.assertEqual(self.calls,2)
    def test_stream_failure_unknown_no_local_fallback(self):
        sid,url=self.create_pending();s=self.client.post(url+'/review',json={'version':1,'adopt':True}).json()
        class Broken:
            async def stream(self,*args):
                yield {'event':'delta','text':'{"partial":'}
                raise RuntimeError('simulated disconnect')
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Broken()
        b={'version':s['version'],'consent':True};b['context_digest']=self.client.post(url+'/plot-preview',json={'version':s['version']}).json()['context_digest']
        r=self.client.post(url+'/plot-step?stream=true',json=b);self.assertIn('"event": "error"',r.text)
        new=self.client.get(url).json();self.assertEqual(new['status'],'plot_call_unknown');self.assertFalse(new['tracks']['T001']['prediction']);self.assertFalse(new['prediction_ledger'])
        self.assertEqual(self.client.post(url+'/plot-preview',json={'version':new['version']}).status_code,400)

    def test_validation_node_retains_specific_public_error(self):
        sid,url=self.create_pending();s=self.client.post(url+'/review',json={'version':1,'adopt':True}).json()
        class Invalid:
            async def stream(self,images,prompt,*args):
                ctx=json.loads(prompt.split('\n',1)[1]);r=plot_answer(ctx)
                r['review_requests']=[{'fragment_id':'F999','reason':'不存在的片段'}]
                yield {'event':'model_complete','result':{'answer':json.dumps(r)}}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Invalid()
        b={'version':s['version'],'consent':True};b['context_digest']=self.client.post(url+'/plot-preview',json={'version':s['version']}).json()['context_digest']
        response=self.client.post(url+'/plot-step?stream=true',json=b)
        self.assertIn('"event": "error"',response.text)
        trace=self.client.get(url+'/execution').json();node=next(n for n in trace['nodes'] if n['node']=='plot_validation')
        self.assertEqual(node['error_type'],'ValueError');self.assertIn('不存在的片段',node['error_detail'])
        self.assertEqual(self.client.get(url).json()['calls'],2)
