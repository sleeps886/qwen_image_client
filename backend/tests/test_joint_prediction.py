import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.app.services.association.joint_prediction import create_joint,prepare_joint,apply_joint,review_joint,public_joint


def fixture():
    return {'candidates':[{'candidate_id':f'{tid}{i}','t':i*10,'bearing_deg':base+i,
                           'platform_id':'A','array':'hull'} for tid,base in [('L',30),('R',110)] for i in range(6)]}


TRACKS=[{'track_id':tid,'seed_ids':[f'{tid}{i}' for i in range(3)]} for tid in ('L','R')]


def response(context,conflict=False):
    return json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],
        'decisions':[{'track_id':tid,'reason':'趋势支持暂定接续','decision':'extend',
          'accepted_candidate_ids':[('L' if conflict else tid)+str(int(c['visible_until']/10))],
          'prediction':[{'t':c['visible_until']+10,'bearing_deg':34 if tid=='L' else 114}]}
          for tid,c in context['tracks'].items()]})


class JointPredictionTests(unittest.TestCase):
    def setUp(self):self.state=create_joint(fixture(),TRACKS)

    def test_shared_pool_without_future_or_cross_history(self):
        context,prompt=prepare_joint(self.state)
        self.assertNotIn('L4',prompt);self.assertNotIn('R4',prompt)
        self.assertEqual(context['tracks']['L']['new_candidates'],context['tracks']['R']['new_candidates'])
        self.assertEqual([p['candidate_id'] for p in context['tracks']['L']['history']],['L0','L1','L2'])
        self.assertEqual([p['candidate_id'] for p in context['tracks']['R']['history']],['R0','R1','R2'])
        self.assertNotIn('L4',json.dumps(public_joint(self.state)))

    def test_future_values_cannot_change_prompt(self):
        data=fixture();data['candidates'][5]['bearing_deg']=179
        other=create_joint(data,TRACKS)
        # Dataset digest records full frozen input, but no future values support a decision.
        a,_=prepare_joint(self.state);b,_=prepare_joint(other)
        a.pop('dataset_version');b.pop('dataset_version')
        for track in a['tracks']:a['tracks'][track].pop('dataset_version');b['tracks'][track].pop('dataset_version')
        self.assertEqual(a,b)

    def test_atomic_conflict_and_bad_second_track(self):
        context,_=prepare_joint(self.state);before=copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError,'占用冲突'):apply_joint(self.state,context,response(context,True))
        answer=json.loads(response(context));answer['decisions'][1]['prediction'][0]['bearing_deg']=181
        with self.assertRaises(ValueError):apply_joint(self.state,context,json.dumps(answer))
        self.assertEqual(self.state,before)

    def test_joint_commit_review_and_growth(self):
        context,_=prepare_joint(self.state);new=apply_joint(self.state,context,response(context))
        self.assertEqual(new['status'],'awaiting_review')
        with self.assertRaises(ValueError):prepare_joint(new)
        new=review_joint(new,1,True)
        context,_=prepare_joint(new)
        self.assertEqual(context['tracks']['L']['new_candidates'][0]['t'],40)
        self.assertEqual(new['tracks']['L']['history'][-1]['candidate_id'],'L3')
        self.assertEqual(new['tracks']['R']['history'][-1]['candidate_id'],'R3')

    def test_rejection_restores_both_and_keeps_cost(self):
        context,_=prepare_joint(self.state);self.state['calls']=1
        new=apply_joint(self.state,context,response(context));new=review_joint(new,1,False,'两个目标接续需复核')
        self.assertEqual(new['calls'],1);self.assertEqual(new['version'],2)
        self.assertEqual(new['tracks'],self.state['tracks'])
        self.assertIn('previous_review_feedback',prepare_joint(new)[0])
        with self.assertRaises(ValueError):review_joint(new,1,True)

    def test_initial_constraints(self):
        for tracks in [[TRACKS[0],TRACKS[0]], [TRACKS[0],{'track_id':'R','seed_ids':['L0','R1','R2']}],
                       [TRACKS[0],{'track_id':'R','seed_ids':['R1','R2','R3']}]]:
            with self.assertRaises(ValueError):create_joint(fixture(),tracks)

    def test_future_reference_and_ambiguity(self):
        context,_=prepare_joint(self.state);doc=json.loads(response(context))
        doc['decisions'][0]['accepted_candidate_ids']=['L4']
        with self.assertRaisesRegex(ValueError,'未开放'):apply_joint(self.state,context,json.dumps(doc))
        doc=json.loads(response(context));doc['decisions'][0].update(decision='ambiguous',accepted_candidate_ids=[])
        new=apply_joint(self.state,context,json.dumps(doc))
        self.assertEqual(len(new['tracks']['L']['history']),3)
        self.assertEqual(len(new['tracks']['R']['history']),4)


class JointApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for replacement in (patch.object(api,'JOINT_ROOT',Path(self.temp.name)),patch.object(api,'model_settings',return_value=ModelSettings(model='fake',base_url='https://invalid.test',api_key='fake'))):
            replacement.start();self.addCleanup(replacement.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1');self.addCleanup(self.client.close);self.addCleanup(app.dependency_overrides.clear)
        self.sid=self.client.post('/api/incremental/joint-sessions',json={'dataset':fixture(),'tracks':TRACKS}).json()['session_id']

    def test_one_call_restore_review_and_version_protection(self):
        calls=[]
        class Fake:
            def invoke(self,images,prompt,*args):
                self_context=json.loads(prompt.split('\n',1)[1]);calls.append(self_context)
                return {'answer':response(self_context),'usage':{'total_tokens':1}}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Fake()
        base=f'/api/incremental/joint-sessions/{self.sid}'
        preview=self.client.get(base+'/preview').json()
        body={'version':0,'consent':True,'context_digest':preview['context_digest']}
        self.assertEqual(self.client.post(base+'/step',json={**body,'consent':False}).status_code,400)
        self.assertEqual(self.client.post(base+'/step',json={**body,'context_digest':'0'*64}).status_code,400)
        result=self.client.post(base+'/step',json=body);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(calls),1);self.assertEqual(result.json()['calls'],1)
        self.assertEqual(self.client.get(base).json()['status'],'awaiting_review')
        self.assertNotEqual(self.client.post(base+'/step',json=body).status_code,200)
        self.assertEqual(len(calls),1)
        reviewed=self.client.post(base+'/review',json={'version':1,'adopt':True}).json()
        self.assertEqual(reviewed['status'],'ready');self.assertEqual(reviewed['version'],2)

    def test_conflict_preserves_both_histories_without_retry(self):
        calls=[]
        class Fake:
            def invoke(self,images,prompt,*args):
                calls.append(1);return {'answer':response(json.loads(prompt.split('\n',1)[1]),True)}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Fake()
        base=f'/api/incremental/joint-sessions/{self.sid}'
        preview=self.client.get(base+'/preview').json()
        result=self.client.post(base+'/step',json={'version':0,'consent':True,'context_digest':preview['context_digest']})
        self.assertNotEqual(result.status_code,200)
        stored=self.client.get(base).json();self.assertEqual(stored['status'],'invalid_response')
        self.assertEqual(stored['calls'],1);self.assertEqual(len(stored['tracks']['L']['history']),3)
        self.assertEqual(len(stored['tracks']['R']['history']),3)
        self.assertNotEqual(self.client.get(base+'/preview').status_code,200);self.assertEqual(len(calls),1)

    def test_unknown_call_stops_without_replay(self):
        calls=[]
        class Fake:
            def invoke(self,*args):calls.append(1);raise TimeoutError('test only')
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Fake()
        base=f'/api/incremental/joint-sessions/{self.sid}'
        preview=self.client.get(base+'/preview').json()
        body={'version':0,'consent':True,'context_digest':preview['context_digest']}
        self.assertNotEqual(self.client.post(base+'/step',json=body).status_code,200)
        self.assertEqual(self.client.get(base).json()['status'],'call_unknown')
        self.assertNotEqual(self.client.post(base+'/step',json=body).status_code,200)
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
