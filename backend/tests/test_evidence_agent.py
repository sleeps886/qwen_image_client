import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image,ImageDraw
import numpy as np
from backend.app.core.exceptions import ClientError
from backend.app.models.model_settings import ModelSettings
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.knowledge.rules import retrieve_rules
from backend.app.services.measurements.sonar_evidence import extract_candidates,resolve_pairs
from backend.app.agents.sonar_agent import build_preflight
from backend.app.agents.sonar_agent import SonarAgent


def sonar_fixture():
    context={'T_MAX':600,'DT':1,'B_MIN':0,'B_MAX':180,'image_time_axis':'bottom_to_top',
             'platforms':[{'name':'A','start_ll':[30,122],'heading_deg':0,'speed_kn':0,
                           'hull_m':[0,0],'tow_m':[-150,0]}]}
    images=[]
    for angle in (math.degrees(math.atan2(400,200)),math.degrees(math.atan2(400,350))):
        image=Image.new('RGB',(500,360),'black');draw=ImageDraw.Draw(image)
        draw.rectangle((30,20,429,319),fill=(0,50,150))
        x=30+angle/180*399;draw.line((x,20,x,319),fill=(255,190,0),width=7)
        draw.rectangle((460,20,468,319),fill=(255,60,0))
        stream=io.BytesIO();image.save(stream,format='PNG');images.append(stream.getvalue())
    return context,images


def candidate_answer(prompt):
    compact=json.loads(prompt.split('像素候选（角度为度、时间为秒）：\n')[1])
    hull=next(im for im in compact if im['array']=='hull')['candidates']
    tow=next(im for im in compact if im['array']=='tow')['candidates']
    return json.dumps({'coordinate_system':'candidate_pairs','warnings':[],
                       'tracks':[{'id':'T1','platform_id':'A','branch':'main',
                                  'pairs':[{'hull_id':h['id'],'tow_id':t['id']} for h,t in zip(hull,tow)]}]})


class EvidenceAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.context,images=sonar_fixture();self.context['require_spatial']=True
        self.paths=[self.root/'btr_A_hull.png',self.root/'btr_A_tow.png']
        for p,b in zip(self.paths,images):p.write_bytes(b)

    def state(self):
        return build_preflight().invoke({'images':[str(p) for p in self.paths],'context':self.context,'prompt':'test'})

    def test_extract_roi_excludes_colourbar_and_centers_angles(self):
        evidence=extract_candidates(self.paths[0],0,self.context)
        self.assertEqual(evidence['roi'],[30,20,429,319])
        self.assertEqual(len(evidence['candidates']),16)
        expected=math.degrees(math.atan2(400,200))
        self.assertLess(abs(evidence['candidates'][0]['bearing_deg']-expected),.5)
        self.assertGreater(evidence['candidates'][0]['y_px'],evidence['candidates'][-1]['y_px'])
        reverse=extract_candidates(self.paths[0],0,self.context|{'image_time_axis':'top_to_bottom'})
        self.assertLess(reverse['candidates'][0]['y_px'],reverse['candidates'][-1]['y_px'])

    def test_reviewed_rules_filter_and_unknown_version(self):
        rules=retrieve_rules('association')
        self.assertTrue(all(r['reviewed'] and r['version']=='btr-v1' for r in rules))
        self.assertEqual(retrieve_rules('association','unknown'),[])
        self.assertTrue(any(r['id']=='pairing-v1' for r in rules))

    def test_dense_grid_samples_actual_images_and_keeps_both_arrays_synchronous(self):
        dense=[extract_candidates(p,i,self.context,time_slices=48) for i,p in enumerate(self.paths)]
        self.assertEqual([len(im['candidates']) for im in dense],[48,48])
        self.assertEqual([p['t'] for p in dense[0]['candidates']],
                         [p['t'] for p in dense[1]['candidates']])
        for im in dense:
            self.assertEqual(im['time_slices'],48)
            self.assertEqual(len({p['t'] for p in im['candidates']}),48)
            self.assertEqual(len({p['y_px'] for p in im['candidates']}),48)
            for p in im['candidates']:
                fraction=(319-p['y_px'])/299
                self.assertLessEqual(abs(p['t']-fraction*600),600/299/2+1e-5)
        for invalid in (True,15,65,48.0):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                extract_candidates(self.paths[0],0,self.context,time_slices=invalid)

    def test_dense_workflow_requires_explicit_experimental_setting(self):
        with patch.dict('os.environ',{'SONAR_EVIDENCE_TIME_SLICES':'48'}):
            state=self.state()
            self.assertEqual(state['evidence']['time_slices'],48)
            self.assertTrue(state['evidence']['experimental_density'])
        for invalid in ('0','65','invalid'):
            with self.subTest(invalid=invalid),patch.dict('os.environ',{'SONAR_EVIDENCE_TIME_SLICES':invalid}):
                with self.assertRaisesRegex(ValueError,'SONAR_EVIDENCE_TIME_SLICES'):self.state()

    def test_random_noise_is_not_forced_to_fill_candidate_slots(self):
        rng=np.random.default_rng(42)
        pixels=np.zeros((360,500,3),dtype=np.uint8)
        pixels[20:320,30:430,0]=rng.integers(0,45,(300,400))
        pixels[20:320,30:430,1]=rng.integers(20,210,(300,400))
        pixels[20:320,30:430,2]=rng.integers(130,255,(300,400))
        Image.fromarray(pixels).save(self.paths[0])
        for slices in (16,48):
            with self.subTest(slices=slices),self.assertRaisesRegex(ValueError,'噪声过滤后'):
                extract_candidates(self.paths[0],0,self.context,time_slices=slices)

    def test_isolated_bright_patch_removed_but_weaker_continuous_band_kept(self):
        with Image.open(self.paths[0]) as source:image=source.convert('RGB')
        draw=ImageDraw.Draw(image)
        # A weaker cyan track persists in time; a strong but isolated patch does not.
        draw.line((330,20,330,319),fill=(20,165,170),width=8)
        y=round(319-(.03+8*(.94/15))*299)
        draw.rectangle((380,y-2,388,y+2),fill=(255,240,0))
        image.save(self.paths[0])
        evidence=extract_candidates(self.paths[0],0,self.context)
        self.assertTrue(any(abs(p['x_px']-330)<5 for p in evidence['candidates']))
        self.assertFalse(any(abs(p['x_px']-384)<8 for p in evidence['candidates']))
        self.assertGreater(evidence['rejected']['no_temporal_support'],0)

    def test_graph_and_pair_resolution_only_use_pixel_evidence(self):
        state=self.state();self.assertEqual(len(state['trace']),5)
        answer=candidate_answer(state['prompt'])
        observations=resolve_pairs(answer,state['evidence'])
        self.assertEqual(len(observations['tracks'][0]['points']),16)
        self.assertEqual(state['evidence']['time_slices'],16)
        self.assertLess(len(state['prompt']),50000)
        for mutate in ('unknown','wrong_time','duplicate','raw_angles'):
            d=json.loads(answer)
            if mutate=='unknown':d['tracks'][0]['pairs'][0]['hull_id']='made-up'
            elif mutate=='wrong_time':d['tracks'][0]['pairs'][0]['tow_id']='I1R1P0'
            elif mutate=='duplicate':d['tracks'].append(d['tracks'][0])
            else:d['coordinate_system']='dual_array_bearings'
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):resolve_pairs(json.dumps(d),state['evidence'])

    def test_unknown_references_isolated_without_replacement_or_gap_filling(self):
        state=self.state();d=json.loads(candidate_answer(state['prompt']))
        d['tracks'][0]['pairs'][4]['hull_id']='I0R4P999'
        result=resolve_pairs(json.dumps(d),state['evidence'],isolate_unknown=True)
        self.assertEqual(len(result['tracks'][0]['points']),15)
        self.assertEqual(result['candidate_validation']['rejected_pair_count'],1)
        self.assertNotIn('I0R4P999',[p['hull_candidate_id'] for p in result['tracks'][0]['points']])
        self.assertNotIn(4,[p['sample_index'] for p in result['tracks'][0]['points']])
        for pair in d['tracks'][0]['pairs']:pair['hull_id']='unknown'
        with self.assertRaises(ValueError):resolve_pairs(json.dumps(d),state['evidence'],isolate_unknown=True)

    def test_workflow_one_call_artifacts_and_spatial_render(self):
        calls=[]
        class FakeGateway:
            def invoke(inner,images,prompt,settings,output_root,mode):
                calls.append(prompt);run=ResultRepository(output_root).create_run()
                return {'answer':candidate_answer(prompt),'output_dir':str(run),'usage':{}}
        workflow=SonarAgent(gateway=FakeGateway())
        result=workflow.invoke(self.paths,'test',ModelSettings('fake','unused','private-do-not-store'),self.root/'out','images',self.context)
        doc=workflow.render(result['answer'],result['output_dir'],self.context)
        self.assertEqual(len(doc['warnings']),len(set(doc['warnings'])))
        self.assertEqual(len(calls),1);self.assertEqual(doc['coordinate_system'],'lon_lat')
        directory=Path(result['output_dir'])
        for name in ('candidate_evidence.json','knowledge_sources.json','quality_report.json','agent_trace.json',
                     'resolved_observations.json','evidence_0.png','selected_1.png','estimated_track.png',
                     'calibration_context.json','tracking_report.json','reprojection_report.json','reprojection_0.png','spatial_raw.json',
                     'model_observations.json','identity_report.json'):
            self.assertTrue((directory/name).is_file(),name)
        trace=json.loads((directory/'agent_trace.json').read_text(encoding='utf-8'))
        self.assertEqual(trace['engine'],'langgraph');self.assertFalse(trace['automatic_paid_retry'])
        self.assertIn('continuous_trajectory_estimation',trace['nodes'])
        self.assertIn('reprojection_quality_check',trace['nodes'])
        self.assertIn('reconcile_dual_ridge_identities',trace['nodes'])
        self.assertNotIn('private-do-not-store',json.dumps(workflow.evidence_state))

    def test_failed_validation_retains_evidence_and_no_retry(self):
        workflow=SonarAgent();workflow.prepare(self.paths,'test','images',self.context)
        run=ResultRepository(self.root/'out').create_run();workflow.persist_evidence(run)
        with self.assertRaises(ValueError):workflow.render('{}',run,self.context)
        report=json.loads((run/'quality_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'needs_review')
        self.assertTrue((run/'evidence_0.png').is_file())
        with self.assertRaises(ClientError):workflow.prepare(self.paths[:1],'test','images',self.context)


class EvidenceStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_runs_same_evidence_contract_and_preserves_artifacts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);context,images=sonar_fixture();context['require_spatial']=True
            paths=[root/'btr_A_hull.png',root/'btr_A_tow.png']
            for path,raw in zip(paths,images):path.write_bytes(raw)
            calls=[]
            class FakeGateway:
                async def stream(inner,images,prompt,settings,output_root,mode):
                    calls.append(1);run=ResultRepository(output_root).create_run()
                    answer=candidate_answer(prompt)
                    yield {'event':'delta','text':answer}
                    yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(run),'usage':{}}}
            workflow=SonarAgent(gateway=FakeGateway())
            events=[e async for e in workflow.stream(paths,'test',ModelSettings('fake','unused','secret'),root/'out','images',context)]
            self.assertEqual(events[0]['phase'],'evidence');self.assertEqual(len(calls),1)
            result=events[-1]['result']
            self.assertTrue((Path(result['output_dir'])/'evidence_0.png').is_file())
            document=workflow.render(result['answer'],result['output_dir'],context)
            self.assertEqual(document['coordinate_system'],'lon_lat')
