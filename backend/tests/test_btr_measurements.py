import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from backend.app.services.measurements.btr_measurements import PALETTE,PROFILE,decode_relative,fit_profile,measure_pack
from backend.app.services.measurements.sonar_evidence import extract_candidates
from backend.app.services.inputs.input_service import prepare_input
from backend.app.agents.sonar_agent import SonarAgent
from backend.tests.test_evidence_agent import sonar_fixture
from unittest.mock import patch


def profile_image(center):
    bearings=np.linspace(0,180,600)
    intensity=.6+8*np.exp(-.5*((bearings-center)/3)**2)
    db=np.clip(10*np.log10(intensity),-5,16)
    index=np.clip(((db+5)/21*255).round().astype(int),0,255)
    pixels=np.zeros((360,720,3),dtype=np.uint8)
    pixels[20:320,30:630]=PALETTE[index][None,:,:]
    pixels[20:320,660:670]=PALETTE[np.linspace(255,0,300).astype(int)][:,None,:]
    stream=io.BytesIO();Image.fromarray(pixels).save(stream,format='PNG')
    return stream.getvalue()


class GaussianFitTests(unittest.TestCase):
    def test_palette_inverse_is_relative_and_marks_clipping(self):
        pixels=PALETTE[None,:,:]
        decoded=decode_relative(pixels,PROFILE)
        self.assertGreater(decoded['valid_fraction'],.99)
        self.assertTrue(decoded['lower_clipped'][0,0]);self.assertTrue(decoded['upper_clipped'][0,-1])
        self.assertAlmostEqual(float(decoded['relative'][0,-1]),10**1.6,places=4)
        self.assertIn('unknown',decoded['meaning'])
        with self.assertRaises(ValueError):decode_relative(pixels,'unknown')
        with self.assertRaises(ValueError):decode_relative(pixels.astype(float),PROFILE)

    def test_single_and_overlapping_gaussian_centers(self):
        x=np.linspace(65,105,300);rng=np.random.default_rng(18)
        y=.6+8*np.exp(-.5*((x-83.35)/3.2)**2)+rng.normal(0,.02,len(x))
        fitted=fit_profile(x,y,[82])
        self.assertEqual(fitted['status'],'fitted')
        self.assertLess(abs(fitted['components'][0]['center_deg']-83.35),.1)
        self.assertLess(abs(fitted['components'][0]['sigma_deg']-3.2),.1)
        y=.6+7*np.exp(-.5*((x-80)/2.5)**2)+5*np.exp(-.5*((x-86)/2.2)**2)
        fitted=fit_profile(x,y,[79,87])
        self.assertEqual(fitted['status'],'fitted')
        np.testing.assert_allclose([c['center_deg'] for c in fitted['components']],[80,86],atol=.1)
        self.assertFalse(fitted['statistical_confidence'])

    def test_saturated_or_invalid_measurement_is_not_fabricated(self):
        x=np.linspace(60,100,100);y=np.ones(100)
        with self.assertRaisesRegex(ValueError,'非饱和'):fit_profile(x,y,[80],upper=np.ones(100,dtype=bool))
        with self.assertRaises(ValueError):fit_profile(x,np.full(100,np.nan),[80])
        with self.assertRaises(ValueError):fit_profile(x,y,[80,81,82])
        with self.assertRaises(ValueError):fit_profile(x[::-1],y,[80])

    def test_flat_profile_cannot_be_reported_as_a_resolved_bright_band(self):
        fitted=fit_profile(np.linspace(60,100,100),np.ones(100),[80])
        self.assertEqual(fitted['status'],'needs_review')


class MeasurementWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.context,_=sonar_fixture()
        self.context.update(require_spatial=True,renderer_profile=PROFILE)
        self.paths=[self.root/f'btr_A_{a}.png' for a in ('hull','tow')]
        for path,center in zip(self.paths,(63.4,48.8)):path.write_bytes(profile_image(center))

    def test_opt_in_report_does_not_modify_measurements_or_invoke_model(self):
        def forbidden(*args,**kwargs):raise AssertionError('No cloud call allowed')
        class Gateway:
            invoke=forbidden
        workflow=SonarAgent(gateway=Gateway())
        old=[extract_candidates(p,i,self.context) for i,p in enumerate(self.paths)]
        frozen=copy.deepcopy(old)
        prompt=workflow.prepare(self.paths,'test','images',self.context)
        state=workflow.evidence_state
        self.assertEqual(state['evidence']['images'],frozen)
        self.assertFalse(state['measurement_report']['observations_modified'])
        self.assertEqual(len(state['measurement_report']['images']),2)
        self.assertTrue(all(im['fitted_groups']>0 for im in state['measurement_report']['images']))
        self.assertIn('工具配色高斯测量诊断',prompt)
        # The final candidate marker still contains only the current real table.
        json.loads(prompt.split('像素候选（角度为度、时间为秒）：\n')[1])
        out=self.root/'output';out.mkdir();workflow.persist_evidence(out)
        self.assertTrue((out/'measurement_report.json').is_file())
        self.assertTrue((out/'gaussian_0.png').is_file())
        self.assertTrue((out/'gaussian_1.png').is_file())
        self.assertTrue(any('未替换观测' in e.get('summary','') for e in workflow.node_events))

    def test_unknown_profile_and_data_mode_rejected(self):
        with self.assertRaises(ValueError):prepare_input(2,None,'test','images',renderer_profile='invalid')
        with self.assertRaises(ValueError):prepare_input(0,b'{}','test','data',renderer_profile=PROFILE)
        prepared=prepare_input(2,None,'test','images',renderer_profile=PROFILE)
        self.assertEqual(prepared.context['renderer_profile'],PROFILE)

    def test_unsupported_color_is_reported_without_changing_legacy_candidates(self):
        evidence={'images':[extract_candidates(self.paths[0],0,self.context)]}
        frozen=copy.deepcopy(evidence)
        Image.new('RGB',(720,360),'white').save(self.paths[0])
        report=measure_pack(self.paths[:1],evidence,self.context)
        self.assertEqual(report['images'][0]['status'],'unavailable')
        self.assertEqual(evidence,frozen)
        with self.assertRaises(ValueError):measure_pack([],evidence,self.context)

    def test_http_free_preflight_exports_measurements_and_blocks_arbitrary_downloads(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app.api.routers import recognition,results
        platform=json.dumps({'T_MAX':600,'DT':1,'B_MIN':0,'B_MAX':180,
                             'platforms':self.context['platforms']})
        patches=[patch.object(recognition,'OUTPUTS',self.root/'results'),
                 patch.object(results,'OUTPUTS',self.root/'results'),
                 patch.object(recognition,'UPLOADS',self.root/'uploads'),
                 patch.dict('os.environ',{'MODEL_API_KEY':'','MODEL_API_FORMAT':'openai',
                                         'MODEL_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'}),
                 patch('backend.app.infrastructure.llm.gateway.ConfiguredModelGateway.invoke',
                       side_effect=AssertionError('must not call cloud'))]
        for p in patches:p.start();self.addCleanup(p.stop)
        with TestClient(app,base_url='http://127.0.0.1') as client:
            response=client.post('/api/recognize',data={'dry_run':'true','consent':'false',
                'input_mode':'images','renderer_profile':PROFILE,'prompt':'test'},files=[
                    ('files',('btr_A_hull.png',self.paths[0].read_bytes(),'image/png')),
                    ('files',('btr_A_tow.png',self.paths[1].read_bytes(),'image/png')),
                    ('platform',('platform.json',platform,'application/json'))])
            self.assertEqual(response.status_code,200,response.text)
            result=response.json()
            self.assertTrue(result['dry_run'])
            self.assertIn('measurement_report.json',result['downloads'])
            self.assertIn('gaussian_0.png',result['downloads'])
            report=client.get(result['downloads']['measurement_report.json']).json()
            self.assertFalse(report['observations_modified'])
            self.assertEqual(client.get(result['downloads']['gaussian_0.png']).status_code,200)
            self.assertEqual(client.get(f"/api/results/{result['result_id']}/not-allowed.json").status_code,404)
            self.assertNotIn('estimated_track.png',result['downloads'])
