import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.services.measurements.gaussian_batch import gaussian_batch
from backend.app.services.association.incremental_prediction import create_state,prepare_round

RID='20261124_110000_1234abcd'


def field():
    return {'version':'forward-field-v1','images':[{'array':'hull','platform_id':'A',
        'measurement_method':'relative_intensity_gaussian_profiles_v1',
        'candidates':[{'id':f'F0R{i}P0','t':i*10,'bearing_deg':30+i,'sigma_deg':.7,
            'amplitude':2,'error_deg':.3,'quality':'fitted' if i!=2 else 'needs_review',
            'mixed':i==2} for i in range(5)]}]}


def diagnostic():
    return {'version':'btr-gaussian-diagnostic-v1','images':[{'image_index':0,'array':'hull','platform_id':'A',
        'groups':[{'status':'fitted','t':10,'candidate_ids':['I0R1P0','I0R1P1'],
            'components':[{'center_deg':20,'sigma_deg':1,'relative_amplitude':2},
                          {'center_deg':24,'sigma_deg':.8,'relative_amplitude':1}]},
            {'status':'needs_review','t':20,'candidate_ids':['I0R2P0']}]}]}


class GaussianBatchTests(unittest.TestCase):
    def test_full_profile_retains_ids_review_flags_and_provenance(self):
        batch=gaussian_batch(field(),RID,'inverse_fields.json')
        self.assertEqual(batch['point_count'],5);self.assertEqual(batch['review_point_count'],1)
        self.assertEqual(batch['candidates'][2]['candidate_id'],'F0R2P0')
        state=create_state(batch,['F0R0P0','F0R1P0','F0R2P0'])
        self.assertEqual(state['dataset_source']['source_result_id'],RID)
        self.assertTrue(state['history'][2]['mixed'])
        self.assertEqual(state['history'][2]['sigma_deg'],.7)
        context,prompt=prepare_round(state)
        self.assertNotIn('F0R3P0',prompt)
        self.assertIn('needs_review',prompt)
        self.assertEqual(batch['batch_id'],gaussian_batch(field(),RID,'inverse_fields.json')['batch_id'])

    def test_diagnostic_uses_fitted_centres_not_pixel_values(self):
        batch=gaussian_batch(diagnostic(),RID,'measurement_report.json')
        self.assertEqual(batch['point_count'],2);self.assertEqual(batch['excluded_diagnostic_groups'],1)
        first=batch['candidates'][0]
        self.assertEqual(first['candidate_id'],'G0K0P0')
        self.assertEqual(first['bearing_deg'],20)
        self.assertEqual(first['source_candidate_ids'],['I0R1P0','I0R1P1'])
        self.assertNotIn('bearing_error_assumption_deg',first)

    def test_pixel_fallback_malformed_and_nonfinite_rejected(self):
        for report,name in ((field(),'candidate_evidence.json'),({'version':'pixel-candidates-v3'},'inverse_fields.json')):
            with self.assertRaises(ValueError):gaussian_batch(report,RID,name)
        for key,value in (('bearing_deg',float('nan')),('sigma_deg',0),('mixed','false')):
            report=field();report['images'][0]['candidates'][0][key]=value
            with self.assertRaises(ValueError):gaussian_batch(report,RID,'inverse_fields.json')


class GaussianBatchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/RID;self.directory.mkdir()
        self.patches=[patch.object(api,'SOURCE_OUTPUTS',self.root),patch.object(api,'ROOT',self.root/'sessions')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close)

    def save(self,name,data):
        (self.directory/name).write_text(json.dumps(data),encoding='utf-8')

    def test_free_transfer_then_create_prediction_task(self):
        self.save('inverse_fields.json',field())
        with patch.object(api,'get_prediction_gateway') as gateway:
            response=self.client.get('/api/incremental/batches/'+RID)
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('attachment',response.headers['content-disposition'])
            batch=response.json()
            response=self.client.post('/api/incremental/sessions',json={'dataset':batch,
                'seed_ids':['F0R0P0','F0R1P0','F0R2P0']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['calls'],0)
            self.assertNotIn('F0R4P0',response.text)
            self.assertEqual(response.json()['dataset_source']['batch_id'],batch['batch_id'])
            gateway.assert_not_called()

    def test_debug_track_is_frozen_measured_subset_with_disclosure(self):
        data=field();template=data['images'][0]['candidates'][-1]
        data['images'][0]['candidates'].append(dict(template,id='F0R5P0',t=50))
        self.save('inverse_fields.json',data)
        self.save('inverse_ridges.json',{'tracks':[{'id':'R_test','array':'hull','platform_id':'A',
            'points':[{'id':p['id'],'t':p['t']} for p in data['images'][0]['candidates']]}]})
        response=self.client.get('/api/incremental/batches/'+RID+'/debug-track')
        self.assertEqual(response.status_code,200,response.text)
        batch=response.json();self.assertEqual(batch['point_count'],6)
        self.assertFalse(batch['debug_reference']['identity_confirmed'])
        self.assertTrue(batch['debug_reference']['uses_full_run_association'])
        original=self.client.get('/api/incremental/batches/'+RID).json()
        self.assertEqual(batch['candidates'],original['candidates'])
        state=self.client.post('/api/incremental/sessions',json={'dataset':batch,'seed_ids':['F0R0P0']})
        self.assertEqual(state.status_code,200)
        self.assertEqual(len(state.json()['history']),1)

    def test_legacy_preflight_and_no_pixel_fallback(self):
        self.save('measurement_report.json',diagnostic())
        response=self.client.get('/api/incremental/batches/'+RID)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['source_file'],'measurement_report.json')
        self.save('inverse_fields.json',{'version':'pixel-candidates-v3'})
        self.assertEqual(self.client.get('/api/incremental/batches/'+RID).status_code,400)

    def test_target_catalog_uses_only_saved_ridge_membership(self):
        self.save('inverse_fields.json',field())
        self.save('inverse_ridges.json',{'tracks':[{'id':'R1','array':'hull','platform_id':'A','points':[{'id':'F0R0P0'},{'id':'F0R1P0'}]},
            {'id':'R2','array':'hull','platform_id':'A','points':[{'id':'F0R3P0'}]}]})
        self.save('inverse_selection.json',{'selected':[{'id':'T1','source_ridges':['R1']},{'id':'T2','source_ridges':['R2']},{'id':'bad','source_ridges':['missing']}]})
        batch=self.client.get('/api/incremental/batches/'+RID).json()
        self.assertEqual(batch['point_count'],5)
        self.assertEqual([t['target_id'] for t in batch['target_catalog']],['T1','T2'])
        self.assertEqual(batch['target_catalog'][0]['candidate_ids'],['F0R0P0','F0R1P0'])
        self.assertFalse(batch['target_catalog'][0]['identity_confirmed'])
        self.assertEqual(len(batch['target_catalog'][0]['dataset_id']),64)

    def test_platform_metadata_export_and_old_session_display_backfill(self):
        self.save('inverse_fields.json',field())
        config={'platforms':[{'name':'A','start_ll':[30,122],'heading_deg':30,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}]}
        self.save('calibration_context.json',config)
        batch=self.client.get('/api/incremental/batches/'+RID).json()
        self.assertEqual(batch['platform_config']['platforms'][0]['speed_kn'],0)
        batch.pop('platform_config')
        created=self.client.post('/api/incremental/sessions',json={'dataset':batch,'seed_ids':['F0R0P0']}).json()
        self.assertEqual(created['platform_motion']['points'][-1]['latitude'],30)
        path=self.root/'sessions'/created['session_id']/'state.json'
        state=json.loads(path.read_text(encoding='utf-8'));state.pop('platform_config');path.write_text(json.dumps(state),encoding='utf-8');before=path.read_bytes()
        displayed=self.client.get('/api/incremental/sessions/'+created['session_id']).json()
        self.assertEqual(displayed['calls'],0);self.assertTrue(displayed['platform_config_backfilled'])
        self.assertEqual(displayed['platform_motion']['initial_longitude'],122)
        self.assertEqual(path.read_bytes(),before)
        preview=self.client.get('/api/incremental/sessions/'+created['session_id']+'/preview').json()
        self.assertIn('platform_state',preview['context'])

    def test_missing_sources_and_bad_id(self):
        self.save('candidate_evidence.json',{'images':[]})
        response=self.client.get('/api/incremental/batches/'+RID)
        self.assertEqual(response.status_code,404)
        self.assertIn('platform.json',response.text)
        self.assertEqual(self.client.get('/api/incremental/batches/not-a-result').status_code,404)


if __name__=='__main__':unittest.main()
