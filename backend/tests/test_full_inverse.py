import copy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
import threading
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from backend.app.agents.hypothesis_review import validate_review
from backend.app.services.localization.forward_observation import GeoReference,observe
from backend.app.services.measurements.btr_measurements import PALETTE
from backend.app.services.measurements.btr_field import extract_fields
from backend.app.services.jobs.inversion_control import InversionControl,InversionStopped
from backend.app.services.association.ridge_hypotheses import nearest_unfolded,ridge_hypotheses,propose_pairs
from backend.app.services.localization.batch_inverse import fit_pair,evaluate_path,select_exclusive
from backend.app.agents.forward_inverse_agent import ForwardInverseAgent
from backend.app.agents.progress import INVERSE_NODES


def inverse_fixture(count=33,kind='cv'):
    ref=GeoReference(30,122)
    platform={'name':'A','start_ll':[30,122],'heading_deg':0,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}
    context={'T_MAX':600,'DT':1,'B_MIN':0,'B_MAX':180,'image_time_axis':'bottom_to_top',
             'renderer_profile':'btr_gui_v1','REF_LAT':30,'REF_LON':122,'EARTH_R':6371000,'platforms':[platform]}
    groups={'hull':[],'tow':[]};truth={}
    for row,time in enumerate(np.linspace(12,588,count)):
        time=float(time)
        if kind=='cv':xy=(600+time*1.5,1200-time*.3)
        elif kind=='waypoint':xy=(600+time*1.5,1200-time*.3+max(0,time-300)*1.5)
        else:xy=(600+time*1.5,1200+100*math.sin(2*math.pi*time/360))
        target=ref.to_ll(xy);truth[time]=xy;observation=observe(platform,target,time,ref)
        for i,array in enumerate(('hull','tow')):
            groups[array].append({'id':f'F{i}R{row}P0','sample_index':row,'t':time,'bearing_deg':observation[array+'_deg'],
                                  'unfolded_deg':observation[array+'_deg'],'sigma_deg':3 if i==0 else 1.5,
                                  'error_deg':.1,'quality':'fitted','mixed':False,'amplitude':8})
    pair={'id':'A001','hull':{'id':'R0_1','points':groups['hull']},'tow':{'id':'R1_1','points':groups['tow']},
          'common_rows':list(range(count))}
    return context,pair,truth


def synthetic_images(context,paths,crossing=False,seed=22):
    rng=np.random.default_rng(seed);ref=GeoReference(30,122);platform=context['platforms'][0]
    x=np.linspace(0,180,600);times=np.linspace(600,0,300)
    for array,path in zip(('hull','tow'),paths):
        intensity=np.full((300,600),.6)+rng.normal(0,.025,(300,600))
        for row,time in enumerate(times):
            xy=(600+float(time)*1.5,1200-float(time)*.3)
            centers=[observe(platform,ref.to_ll(xy),float(time),ref)[array+'_deg']]
            if crossing:
                xy=(1300-float(time),600+float(time)*.5)
                centers.append(observe(platform,ref.to_ll(xy),float(time),ref)[array+'_deg'])
            for center in centers:intensity[row]+=8*np.exp(-.5*((x-center)/(3.4 if array=='hull' else 1.5))**2)
        db=np.clip(10*np.log10(np.maximum(.0001,intensity)),-5,16)
        pixels=np.zeros((360,720,3),dtype=np.uint8)
        pixels[20:320,30:630]=PALETTE[np.clip(((db+5)/21*255).round().astype(int),0,255)]
        pixels[20:320,660:670]=PALETTE[np.linspace(255,0,300).astype(int)][:,None,:]
        Image.fromarray(pixels).save(path)


class BatchInverseTests(unittest.TestCase):
    def test_exact_cv_and_mirrors_preserve_measured_times(self):
        context,pair,truth=inverse_fixture();frozen=copy.deepcopy(pair)
        result=fit_pair(pair,context,InversionControl())
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result['variants']),2)
        ref=GeoReference(30,122);errors=[]
        for variant in result['variants']:
            positions=evaluate_path(variant['parameters'],list(truth),variant['model'],variant['t0'],variant['span'])
            expected=np.array(list(truth.values()))
            errors.append(float(np.sqrt(np.mean(np.sum((positions-expected)**2,axis=1)))))
            self.assertLess(variant['validation_rmse_deg'],.001)
        self.assertLess(min(errors),1.)
        self.assertEqual(pair,frozen)
        self.assertFalse(result['validation_is_truth'])

    def test_waypoint_model_allows_a_real_velocity_corner(self):
        context,pair,truth=inverse_fixture(kind='waypoint')
        result=fit_pair(pair,context,InversionControl())
        self.assertIsNotNone(result)
        self.assertLess(result['variants'][0]['validation_rmse_deg'],.1)
        self.assertIn(result['variants'][0]['model'],('waypoint','harmonic','quadratic'))

    def test_unfolding_continues_through_180_instead_of_switching_identity(self):
        self.assertEqual(nearest_unfolded(179,181),181)
        self.assertEqual(nearest_unfolded(2,-2),-2)
        self.assertEqual(nearest_unfolded(5,355),355)

    def test_shared_crossing_support_is_removed_from_both_not_double_counted(self):
        a=[f'p{i}' for i in range(20)];b=['p0','p1']+[f'q{i}' for i in range(18)]
        candidates=[{'id':name,'variants':[{'parameters':[0]*4}]} for name in ('A','B')]
        supports={'A':a,'B':b}
        evidence={'images':[{'array':array,'candidates':[{'id':i} for i in set(a+b) if int(i[1:])%2==index]}
                            for index,array in enumerate(('hull','tow'))]}
        with patch('backend.app.services.localization.batch_inverse.support_for',side_effect=lambda c,*args:(supports[c['id']],.05)):
            result=select_exclusive({'candidates':candidates},evidence,{},InversionControl())
        self.assertEqual(len(result['selected']),2)
        self.assertEqual(set(result['shared_crossing_ids_excluded']),{'p0','p1'})
        first,second=result['selected']
        self.assertFalse(set(first['supported_measurement_ids'])&set(second['supported_measurement_ids']))
        self.assertFalse(result['target_count_confirmed'])

    def test_cancel_and_budget_are_checked(self):
        control=InversionControl(seconds=0)
        with self.assertRaises(InversionStopped):control.check()
        control=InversionControl();control.cancelled.set()
        with self.assertRaises(InversionStopped):control.check()

    def test_review_cannot_invent_ids_coordinates_or_share_measurements(self):
        selection={'candidates':[{'id':'A','supported_measurement_ids':['p1']},{'id':'B','supported_measurement_ids':['p1']}]}
        ridges={'crossing_regions':[{}]}
        valid={'coordinate_system':'association_hypotheses','preferred_hypothesis_ids':['A'],
               'reviews':[{'region_id':'C1','decision':'keep_ambiguous','reason_codes':['merged_peak']}]}
        result=validate_review(json.dumps(valid),selection,ridges)
        self.assertFalse(result['numerical_selection_overridden'])
        for ids in (['unknown'],['A','A'],['A','B']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                validate_review(json.dumps(valid|{'preferred_hypothesis_ids':ids}),selection,ridges)
        with self.assertRaises(ValueError):validate_review('{"coordinate_system":"lon_lat"}',selection,ridges)
        invalid=copy.deepcopy(valid);invalid['reviews'][0]['coordinates']=[122,30]
        with self.assertRaises(ValueError):validate_review(json.dumps(invalid),selection,ridges)


class FullInverseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.context,_,_=inverse_fixture()
        self.paths=[self.root/'btr_A_hull.png',self.root/'btr_A_tow.png']

    def test_local_end_to_end_exports_forward_checks_without_model(self):
        synthetic_images(self.context,self.paths)
        workflow=ForwardInverseAgent();result=workflow.run(self.paths,self.context,self.root/'outputs')
        self.assertEqual(result['new_model_calls'],0)
        self.assertEqual(result['pipeline'],'forward_inverse_v1')
        statuses={e['node']:e['status'] for e in result['node_events']}
        self.assertEqual(statuses['inverse_review'],'skipped')
        self.assertTrue(all(statuses[name]=='completed' for name,_ in INVERSE_NODES if name!='inverse_review'))
        self.assertGreaterEqual(result['target_count']['localized_target_count'],1)
        directory=self.root/'outputs'/result['result_id']
        self.assertTrue((directory/'expected_btr_0.png').is_file())
        self.assertTrue((directory/'inverse_overlay_1.png').is_file())
        self.assertTrue((directory/'inverse_profile_fit.json').is_file())
        self.assertTrue((directory/'mirror_candidates.png').is_file())
        rules=json.loads((directory/'knowledge_sources.json').read_text(encoding='utf-8'))
        self.assertTrue(all(r['source']=='builtin-project-rules' for r in rules))
        quality=json.loads((directory/'inverse_quality.json').read_text(encoding='utf-8'))
        self.assertFalse(quality['independent_position_accuracy_verified'])
        doc=json.loads((directory/'tracks.json').read_text(encoding='utf-8'))
        self.assertTrue(doc['all_alternatives_retained'])
        self.assertEqual(doc['display_view'],'primary_motion_hypotheses')
        self.assertGreaterEqual(len({t['mirror_variant'] for t in doc['tracks']}),2)
        self.assertTrue(all(p['point_source']=='model_estimate_at_observed_time' for t in doc['tracks'] for s in t['observation_segments'] for p in s['points']))
        self.assertIn('identity_continuity',doc)
        self.assertTrue(all(p['point_source'] in ('model_estimate_at_observed_time','motion_model_gap_prediction') for t in doc['tracks'] for p in t['points']))

    def test_optional_review_is_one_call_and_failure_keeps_numeric_result(self):
        synthetic_images(self.context,self.paths);calls=[]
        def reviewer(*args):calls.append(args);return {'answer':'not a JSON response'}
        workflow=ForwardInverseAgent(reviewer=reviewer)
        result=workflow.run(self.paths,self.context,self.root/'review-output')
        self.assertEqual(len(calls),1);self.assertEqual(result['new_model_calls'],1)
        self.assertEqual(result['hypothesis_review']['status'],'failed')
        self.assertIn('estimated_track.png',result['downloads'])

    def test_invalid_image_or_undeclared_profile_never_creates_fake_trajectory(self):
        synthetic_images(self.context,self.paths)
        workflow=ForwardInverseAgent()
        with self.assertRaises(ValueError):workflow.run(self.paths,self.context|{'renderer_profile':'none'},self.root/'bad-output')
        self.assertFalse((workflow.directory/'estimated_track.png').exists())
        self.assertTrue((workflow.directory/'execution_report.json').exists())

    def test_refinement_deadline_keeps_candidates_and_runs_quality_and_render(self):
        from backend.app.agents.nodes import inverse as forward_inverse
        synthetic_images(self.context,self.paths)
        original=forward_inverse.refine_profiles
        def limited(*args,**kwargs):return original(*args,**kwargs,phase_seconds=0)
        with patch.object(forward_inverse,'refine_profiles',side_effect=limited):
            workflow=ForwardInverseAgent()
            result=workflow.run(self.paths,self.context,self.root/'limited-output')
        self.assertEqual(result['optimization_status'],'budget_limited')
        self.assertIn('estimated_track.png',result['downloads'])
        self.assertTrue(any('部分优化候选' in w for w in result['warnings']))
        statuses={e['node']:e['status'] for e in result['node_events']}
        self.assertEqual(statuses['inverse_quality'],'completed')
        self.assertEqual(statuses['inverse_render'],'completed')
        self.assertEqual(result['new_model_calls'],0)

    def test_crossing_pipeline_uses_no_expected_target_count(self):
        synthetic_images(self.context,self.paths,crossing=True)
        workflow=ForwardInverseAgent();result=workflow.run(self.paths,self.context,self.root/'cross-output')
        self.assertGreaterEqual(result['target_count']['localized_target_count'],2)
        selection=json.loads((workflow.directory/'inverse_selection.json').read_text(encoding='utf-8'))
        all_ids=[i for c in selection['selected'] for i in c['supported_measurement_ids']]
        self.assertEqual(len(all_ids),len(set(all_ids)))
        self.assertFalse(result['target_count']['unique_target_count_confirmed'])


class InverseApiTests(unittest.TestCase):
    def setUp(self):
        FullInverseWorkflowTests.setUp(self)
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app.api.routers import inversion,results
        self.inversion=inversion;synthetic_images(self.context,self.paths)
        self.slots=threading.BoundedSemaphore(1)
        patches=[patch.object(inversion,'OUTPUTS',self.root/'api-results'),
                 patch.object(results,'OUTPUTS',self.root/'api-results'),
                 patch.object(inversion,'UPLOADS',self.root/'api-uploads'),patch.object(inversion,'SLOTS',self.slots),
                 patch.dict('os.environ',{'MODEL_API_KEY':''}),
                 patch('backend.app.infrastructure.llm.gateway.ConfiguredModelGateway.invoke',side_effect=AssertionError('cloud forbidden'))]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)

    def post_inverse(self,path='/api/invert',extra=None):
        platform={k:v for k,v in self.context.items() if k not in ('renderer_profile',)}
        return self.client.post(path,data={'renderer_profile':'btr_gui_v1',**(extra or {})},files=[
            ('files',('btr_A_hull.png',self.paths[0].read_bytes(),'image/png')),
            ('files',('btr_A_tow.png',self.paths[1].read_bytes(),'image/png')),
            ('platform',('platform.json',json.dumps(platform),'application/json'))])

    def test_sse_local_run_has_real_nodes_downloads_and_zero_model_calls_without_key(self):
        with patch.dict('os.environ',{'MODEL_API_FORMAT':'invalid-local-does-not-need-cloud'}):
            health=self.client.get('/api/health')
            self.assertEqual(health.status_code,200)
            self.assertFalse(health.json()['model_configuration_valid'])
            response=self.post_inverse('/api/invert/stream')
        self.assertEqual(response.status_code,200,response.text)
        events=[json.loads(frame.split('data: ',1)[1]) for frame in response.text.strip().split('\n\n') if 'data: ' in frame]
        results=[e['data'] for e in events if e['event']=='result'];self.assertEqual(len(results),1)
        result=results[0];self.assertEqual(result['new_model_calls'],0)
        self.assertEqual(len({e['node'] for e in events if e['event']=='node' and e['status']=='running'}),9)
        self.assertEqual(self.client.get(result['downloads']['expected_btr_0.png']).status_code,200)
        history=self.client.get('/api/history/'+result['result_id']).json()
        self.assertEqual(history['pipeline'],'forward_inverse_v1');self.assertEqual(history['new_model_calls'],0)
        self.assertEqual(self.slots._value,1)

    def test_disconnect_retains_uploads_and_slot_until_worker_exits(self):
        from backend.app.api.routers import inversion
        released=threading.Event();closes=[]
        job=SimpleNamespace(paths=[],context={},control=InversionControl(),reviewer=None,closed=False)
        def close():
            job.closed=True;closes.append(True)
        job.close=close
        class Worker:
            def __init__(self,emit,**kwargs):self.emit=emit
            def run(self,*args):
                self.emit({'event':'node','node':'inverse_field','status':'running'})
                released.wait(3)
                return {'ok':True}
        async def scenario():
            with patch.object(inversion,'prepare_job',return_value=job),patch.object(inversion,'ForwardInverseAgent',Worker),patch.object(inversion.LOGS,'finish') as finish:
                response=inversion.invert_stream(SimpleNamespace(state=SimpleNamespace(request_id='test-cancel')),[],None)
                iterator=response.body_iterator
                await anext(iterator);await anext(iterator)
                await iterator.aclose();await response.background()
                self.assertTrue(job.control.cancelled.is_set())
                self.assertFalse(job.closed)
                released.set()
                for _ in range(100):
                    if job.closed:break
                    await asyncio.sleep(.01)
                self.assertTrue(job.closed);self.assertEqual(len(closes),1)
                finish.assert_called_once_with('test-cancel',499)
        try:asyncio.run(scenario())
        finally:released.set()

    def test_review_is_not_called_without_explicit_consent_or_key(self):
        self.assertEqual(self.post_inverse(extra={'review_model':'true'}).status_code,400)
        self.assertEqual(self.post_inverse(extra={'review_model':'true','consent':'true'}).status_code,400)
        self.assertEqual(self.slots._value,1)

    def test_model_final_requires_consent_and_key(self):
        self.assertEqual(self.post_inverse(extra={'final_model':'true'}).status_code,400)
        self.assertEqual(self.post_inverse(extra={'final_model':'true','consent':'true'}).status_code,400)
        self.assertEqual(self.post_inverse(extra={'final_model':'true','review_model':'true','consent':'true'}).status_code,400)
        self.assertEqual(self.slots._value,1)

    def test_unknown_profile_and_client_credentials_are_rejected(self):
        self.assertEqual(self.post_inverse(extra={'renderer_profile':'unknown'}).status_code,400)
        self.assertEqual(self.post_inverse(extra={'api_key':'client-key'}).status_code,400)
        self.assertEqual(self.slots._value,1)
