import copy
import json
import unittest

from backend.tests import test_incremental_prediction as prediction_tests
from backend.tests.test_incremental_prediction import answer,dataset
from backend.app.api.routers import incremental as api
from backend.app.main import app
from backend.app.services.association.incremental_prediction import create_state,public_state
from backend.app.services.association.prediction_review import all_point_proposal
from backend.tests.test_incremental_spatial_plan import fixture


class ReviewApiTests(unittest.TestCase):
    setUp=prediction_tests.PredictionApiTests.setUp
    create=prediction_tests.PredictionApiTests.create
    def propose(self):
        class Fake:
            def invoke(self,images,prompt,*args):
                context,_=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1])
                return {'answer':answer(context)}
        app.dependency_overrides[api.get_prediction_gateway]=Fake
        sid=self.create();url=f'/api/incremental/sessions/{sid}'
        before=self.client.get(url).json()
        proposed=self.client.post(url+'/step',json={'version':0,'consent':True}).json()
        self.assertEqual(proposed['status'],'awaiting_review')
        self.assertNotIn('_review_checkpoint',proposed)
        self.assertEqual(self.client.post(url+'/step',json={'version':1,'consent':True}).status_code,400)
        return url,before,proposed

    def test_reject_restores_state_keeps_cost_audit_and_requires_manual_resubmit(self):
        url,before,proposed=self.propose()
        self.assertEqual(self.client.get(url).json()['status'],'awaiting_review')
        self.assertEqual(self.client.post(url+'/review',json={'version':1,'adopt':False}).status_code,400)
        rejected=self.client.post(url+'/review',json={'version':1,'adopt':False,'reason':'选点不对'}).json()
        for key in ('history','prediction','visible_until','initialized','status','rounds'):
            self.assertEqual(rejected[key],before[key])
        self.assertEqual((rejected['calls'],rejected['version']),(1,2))
        self.assertEqual(rejected['review_history'][-1]['reason'],'选点不对')
        self.assertEqual(self.client.post(url+'/review',json={'version':1,'adopt':True}).status_code,400)
        self.assertEqual(self.client.get(url+'/preview').status_code,200)
        feedback=self.client.get(url+'/preview').json()['context']['previous_review_feedback']
        self.assertEqual(feedback['reason'],'选点不对')

    def test_adopt_commits_once_without_cloud_call(self):
        url,before,proposed=self.propose()
        adopted=self.client.post(url+'/review',json={'version':1,'adopt':True}).json()
        self.assertEqual((adopted['status'],adopted['version'],adopted['calls']),('tracking',2,1))
        self.assertEqual(adopted['rounds'][-1]['review_status'],'adopted')
        self.assertEqual(adopted['execution']['status'],'completed')
        self.assertNotIn('interrupt',adopted)

    def test_all_point_api_free_with_no_model_configuration(self):
        result=self.client.post('/api/incremental/all-points-preview',json={'dataset':dataset(),'seed_ids':['P0']})
        self.assertEqual(result.status_code,200,result.text)
        proposal=result.json()
        self.assertEqual((proposal['status'],proposal['calls']),('awaiting_review',0))
        self.assertEqual(len(proposal['history']),10)
        self.assertEqual(len(proposal['rounds'][0]['input']['selected_time_groups']),10)


class AllPointsTests(unittest.TestCase):
    def test_missing_companion_breaks_spatial_lines(self):
        source=fixture()
        data={'platform_config':source['platform_config'],
              'candidates':[p for p in source['points']+source['companion_points'] if p['candidate_id']!='T3']}
        proposal=all_point_proposal(create_state(data,['P0']))
        for branch in proposal['rounds'][0]['spatial_plan']['branches']:
            for segment in branch['observed_segments']:
                self.assertFalse(any(a['t']<30<b['t'] for a,b in zip(segment,segment[1:])))

    def test_all_points_include_more_than_old_sixty_limit(self):
        source=fixture();data={'platform_config':source['platform_config'],'candidates':[]}
        for i in range(80):
            for array,prefix,b in [('hull','H',30),('tow','T',32)]:
                data['candidates'].append({'candidate_id':prefix+str(i),'t':i*10,'bearing_deg':b,'array':array,'platform_id':'A'})
        state=create_state(data,['H0']);before=copy.deepcopy(state);new=all_point_proposal(state)
        self.assertEqual(state,before)
        self.assertEqual(new['rounds'][0]['spatial_plan']['paired_observation_count'],80)
        self.assertEqual(len(new['history']),80)
        self.assertEqual(new['prediction'],[])

    def test_competing_primary_is_excluded_not_silently_paired(self):
        data=dataset();data['candidates'].append(dict(data['candidates'][3],candidate_id='Q3'))
        new=all_point_proposal(create_state(data,['P0']))
        self.assertNotIn(30,[p['t'] for p in new['history']])
        self.assertEqual(new['rounds'][0]['excluded_ambiguous_times'],[30])
