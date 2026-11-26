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
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed,update_mixed,review_mixed,public_mixed
from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
from backend.app.services.inputs.workspace_observations import enable
from backend.tests.model_plot_fixture import plot_answer


def fixture():
    grouped=[[81,294,340,473],[171,575],[102,318,540]]
    times=[13,54,128,202,228,259,373,397,422,447,506]
    rows=[dict(candidate_id=f'h{i}',t=t,platform_id='A',array='hull',bearing_deg=150-i) for i,t in enumerate(times)]
    for group,ts in enumerate(grouped):
        rows.extend(dict(candidate_id=f't{group}-{i}',t=t,platform_id='A',array='tow',bearing_deg=30+group*30+i) for i,t in enumerate(ts))
    state=create_mixed({'candidates':rows});ctx,_=prepare_mixed(state,[p['candidate_id'] for p in rows]);aliases=state['aliases']
    answer={'step_id':1,'base_state_version':0,'tracks':[{'track_ref':f'N{group+1}', 'observation_ids':[aliases[f't{group}-{i}'] for i in range(len(ts))], 'reason':'离线测试分组，不是身份真值'} for group,ts in enumerate(grouped)],'unassigned':[aliases[f'h{i}'] for i in range(11)]}
    proposed=update_mixed(state,ctx,validate_mixed(state,ctx,json.dumps(answer)))
    return review_mixed(proposed,proposed['version'],True)


class SparsePlotDiagnosticsTests(unittest.TestCase):
    def test_sparse_gap_is_fragment_specific_not_minimum_across_targets(self):
        state=fixture();before=copy.deepcopy(state);ctx,_=prepare_model_plot(state)
        self.assertEqual([ctx['tracks'][tid]['max_connected_gap_s'] for tid in ('T001','T002','T003')],[399,1212,648])
        self.assertTrue(all(t['gap_diagnostics']['scope']=='this_fragment_only' for t in ctx['tracks'].values()))
        self.assertTrue(all(not t['gap_diagnostics']['cadence_calibrated'] for t in ctx['tracks'].values()))
        self.assertEqual(state,before)

    def test_model_unassigned_is_not_misreported_as_local_filter(self):
        state=fixture();public=public_mixed(state);audit=public['association_audit']
        self.assertEqual((audit['submitted_points'],audit['assigned_points'],audit['unassigned_points']),(20,9,11))
        self.assertEqual(audit['strict_synchronous_groups'],0)
        self.assertEqual(audit['by_array'],{'hull':{'submitted':11,'assigned':0},'tow':{'submitted':9,'assigned':9}})
        self.assertTrue(all(d['reason_code']=='model_unassigned' for d in public['unassigned_details']))
        self.assertEqual(audit['target_hypotheses'],0)

    def test_bearing_diagnostic_is_explicit_and_not_published_as_spatial(self):
        state=fixture();ctx,_=prepare_model_plot(state,purpose='bearing')
        self.assertEqual(ctx['output_goal'],'bearing_diagnostic')
        result=apply_model_plot(state,ctx,json.dumps(plot_answer(ctx)))
        self.assertEqual(result['plot_status'],'bearing_generated')
        self.assertFalse(result['rounds'][-1]['model_plot']['spatial_result']['tracks'])
        self.assertEqual(result['rounds'][-1]['model_plot']['spatial_result']['status'],'not_requested')
        self.assertTrue(any(len(seg['curve'])>=2 for tr in result['tracks'].values() for seg in tr['model_segments']))

    def test_space_request_without_candidates_cannot_consume_call(self):
        with tempfile.TemporaryDirectory() as root,patch.object(api,'MIXED_ROOT',Path(root)),patch.object(api,'model_settings',return_value=ModelSettings('fake','http://invalid.local','fake')):
            state=fixture();directory=api.ResultRepository(api.MIXED_ROOT).create_run();path=directory/'state.json';state=enable(path,state,'clock');api.save_state(path,state)
            client=TestClient(app,base_url='http://127.0.0.1');base='/api/incremental/mixed-sessions/'+directory.name
            snapshot=client.post(base+'/plot-preview',json={'version':state['version']}).json()
            self.assertFalse(snapshot['can_generate_spatial'])
            class NoCall:
                def invoke(self,*args):raise AssertionError('must not dispatch')
            with patch.dict(app.dependency_overrides,{api.get_prediction_gateway:lambda:NoCall()}):
                for suffix in ('/plot-step','/plot-step?stream=true'):
                    reply=client.post(base+suffix,json={'version':state['version'],'consent':True,'context_digest':snapshot['context_digest']})
                    self.assertEqual(reply.status_code,400,reply.text)
                self.assertEqual(client.get(base).json()['calls'],state['calls'])
                legacy=copy.deepcopy(state);legacy.pop('workspace_input',None);api.save_state(path,legacy)
                snapshot=client.post(base+'/plot-preview',json={'version':state['version'],'purpose':'spatial'}).json()
                reply=client.post(base+'/plot-step',json={'version':state['version'],'purpose':'spatial','consent':True,'context_digest':snapshot['context_digest']})
                self.assertEqual(reply.status_code,400,reply.text)
                self.assertEqual(client.get(base).json()['calls'],state['calls'])
            client.close()
