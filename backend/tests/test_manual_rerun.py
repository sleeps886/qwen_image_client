"""Manual retry preparation must never spend, erase history or silently reset limits."""
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
from backend.app.services.association.manual_rerun import rerun_policy,prepare_manual_rerun
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,review_mixed
from backend.app.services.association.model_plot import prepare_model_plot
from backend.tests.test_mixed_prediction import dataset,apply


def association_failure(unknown=False):
    s=create_mixed(dataset());ctx,_=prepare_mixed(s,['A038_0','A038_1'])
    s.update(calls=1,status='call_unknown' if unknown else 'invalid_response')
    return s,ctx


def plot_failure(unknown=False):
    s=apply(create_mixed(dataset()),['A038_0','A038_1'],[('N1',['A038_0','A038_1'])])
    s=review_mixed(s,s['version'],True);ctx,_=prepare_model_plot(s)
    s.update(calls=2,status='plot_call_unknown' if unknown else 'plot_invalid_response')
    return s,ctx


class ManualRerunTests(unittest.TestCase):
    def test_failed_association_restores_exact_points_free_with_new_version(self):
        s,ctx=association_failure();before=copy.deepcopy(s)
        new,prepared,form=prepare_manual_rerun(s,ctx)
        self.assertEqual(s,before);self.assertEqual(new['calls'],1);self.assertEqual(new['version'],1)
        self.assertEqual(new['status'],'ready');self.assertEqual(set(form['selected_ids']),{'A038_0','A038_1'})
        self.assertFalse(form['recheck']);self.assertEqual(prepared['base_state_version'],1)
        self.assertEqual(prepare_mixed(new,form['selected_ids'],form['question'],recheck=form['recheck'])[0],prepared)
        self.assertFalse(rerun_policy(new)['available'])
        with self.assertRaises(ValueError):prepare_manual_rerun(new,ctx)

    def test_plot_rerun_preserves_confirmation_and_forecast_ledger(self):
        s,_=plot_failure();s['prediction_ledger']=[{'issued_before':True}]
        new,ctx,form=prepare_manual_rerun(s)
        self.assertEqual(form['phase'],'plot');self.assertEqual(new['calls'],2)
        self.assertEqual(new['tracks'],s['tracks']);self.assertEqual(new['rounds'],s['rounds'])
        self.assertEqual(new['prediction_ledger'],s['prediction_ledger']);self.assertEqual(ctx['base_state_version'],s['version']+1)

    def test_unknown_result_requires_separate_cost_acknowledgement(self):
        for s,ctx in (association_failure(True),plot_failure(True)):
            with self.subTest(status=s['status']):
                with self.assertRaisesRegex(ValueError,'未知'):prepare_manual_rerun(s,ctx)
                new,_,_=prepare_manual_rerun(s,ctx,acknowledge_unknown=True)
                self.assertEqual(new['calls'],s['calls']);self.assertTrue(new['rerun_history'][-1]['unknown_cost_acknowledged'])

    def test_zero_new_points_can_reanalyse_current_confirmed_round(self):
        s,_=plot_failure();s.update(status='ready',plot_status='awaiting_association_recheck')
        s['association_recheck_requests']=[{'fragment_id':'F001','reason':'复核归属'}]
        new,ctx,form=prepare_manual_rerun(s,s['rounds'][-1]['input'])
        self.assertTrue(form['recheck']);self.assertEqual(set(form['selected_ids']),set(s['submitted_ids']))
        self.assertEqual(ctx['revision_mode'],'historical_replay');self.assertIn('requested_association_review',ctx)
        with self.assertRaises(ValueError):prepare_model_plot(new)

    def test_failed_plot_can_explicitly_return_to_association(self):
        s,_=plot_failure();new,_,form=prepare_manual_rerun(s,s['rounds'][-1]['input'],phase='association')
        self.assertEqual(form['phase'],'association');self.assertTrue(form['recheck'])
        self.assertEqual(new['tracks'],s['tracks'])

    def test_pending_success_and_limits_cannot_be_bypassed(self):
        s,ctx=association_failure()
        for status in ('pending','plot_pending','awaiting_review'):
            changed={**s,'status':status}
            with self.subTest(status=status),self.assertRaises(ValueError):prepare_manual_rerun(changed,ctx,acknowledge_unknown=True)
        exhausted={**s,'calls':s['max_calls']}
        with self.assertRaisesRegex(ValueError,'上限'):prepare_manual_rerun(exhausted,ctx)
        with self.assertRaises(ValueError):prepare_manual_rerun(s,ctx,phase='plot')


class ManualRerunApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(api,'MIXED_ROOT',Path(self.temp.name));self.root.start()
        self.settings=patch.object(api,'model_settings',return_value=ModelSettings(api_key='fake-placeholder',model='fake',base_url='http://localhost'));self.settings.start()
        self.client=TestClient(app,base_url='http://127.0.0.1');self.calls=0;owner=self
        class Fake:
            def invoke(self,images,prompt,*args):
                owner.calls+=1;ctx=json.loads(prompt.split('\n',1)[1])
                return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[]})}
        app.dependency_overrides[api.get_prediction_gateway]=lambda:Fake()

    def tearDown(self):
        app.dependency_overrides.clear();self.settings.stop();self.root.stop();self.temp.cleanup()

    def save_failure(self,s,ctx):
        directory=api.ResultRepository(api.MIXED_ROOT).create_run();api.save_state(directory/'state.json',s)
        prefix='plot_attempt' if s['status'].startswith('plot_') else 'attempt'
        api.save_state(directory/f"{prefix}_{s['calls']:03d}.json",{'status':'call_unknown' if 'unknown' in s['status'] else 'returned','input':ctx,'answer':'original bad reply'})
        return directory,'/api/incremental/mixed-sessions/'+directory.name

    def test_free_prepare_then_new_consent_and_digest_required(self):
        s,ctx=association_failure();directory,url=self.save_failure(s,ctx)
        original=(directory/'attempt_001.json').read_bytes()
        result=self.client.post(url+'/prepare-rerun',json={'version':0});self.assertEqual(result.status_code,200,result.text)
        prepared=result.json();self.assertEqual(prepared['model_calls'],0);self.assertEqual(self.calls,0);self.assertEqual(prepared['calls'],1)
        self.assertEqual(self.client.post(url+'/prepare-rerun',json={'version':0}).status_code,400)
        body={'version':prepared['version'],'selected_ids':prepared['rerun']['selected_ids'],'recheck':prepared['rerun']['recheck'],'question':prepared['rerun']['question'],'context_digest':prepared['rerun']['context_digest']}
        self.assertEqual(self.client.post(url+'/step',json=body).status_code,400);self.assertEqual(self.calls,0)
        bad={**body,'consent':True,'context_digest':api.context_digest(ctx)}
        self.assertEqual(self.client.post(url+'/step',json=bad).status_code,400);self.assertEqual(self.calls,0)
        response=self.client.post(url+'/step',json={**body,'consent':True});self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.calls,1);self.assertEqual(response.json()['calls'],2)
        self.assertEqual((directory/'attempt_001.json').read_bytes(),original);self.assertTrue((directory/'attempt_002.json').is_file())
        self.assertNotIn('rerun_prepared',response.json())

    def test_unknown_plot_does_not_reopen_until_acknowledged(self):
        s,ctx=plot_failure(True);_,url=self.save_failure(s,ctx)
        self.assertEqual(self.client.post(url+'/prepare-rerun',json={'version':s['version']}).status_code,400)
        r=self.client.post(url+'/prepare-rerun',json={'version':s['version'],'acknowledge_unknown':True})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['calls'],2);self.assertEqual(self.calls,0)
        body={'version':r.json()['version'],'context_digest':r.json()['rerun']['context_digest']}
        self.assertEqual(self.client.post(url+'/plot-step',json=body).status_code,400);self.assertEqual(self.calls,0)


if __name__=='__main__':unittest.main()
