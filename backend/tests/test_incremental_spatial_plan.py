import copy
import json
import unittest

from backend.app.services.association.incremental_prediction import create_state,prepare_round,apply_answer,public_state
from backend.app.services.localization.forward_observation import GeoReference,observe
from backend.app.services.localization.platform_motion import platform_metadata
from backend.app.services.localization.incremental_spatial_plan import build_spatial_plan


def fixture(duplicates=False):
    platform={'name':'A','start_ll':[30,122],'heading_deg':0,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}
    ref=GeoReference(30,122);data={'platform_config':platform_metadata({'platforms':[platform]}),'candidates':[]};bearings={}
    for i,t in enumerate((0,10,20,30,40,50)):
        ll=ref.to_ll((1000+2*t,2000+t));observed=observe(platform,ll,t,ref);bearings[t]=observed
        for array,prefix in [('hull','P'),('tow','T')]:data['candidates'].append({'candidate_id':prefix+str(i),'t':t,
            'bearing_deg':observed[array+'_deg'],'array':array,'platform_id':'A','bearing_error_assumption_deg':.15})
        if duplicates:data['candidates'].append({**data['candidates'][-1],'candidate_id':'Q'+str(i),'bearing_deg':observed['tow_deg']+.3})
    state=create_state(data,['P0','P1','P2']);context,_=prepare_round(state,selected_times=[0,10,20])
    answer={'reason':'synthetic test only','step_id':1,'base_state_version':0,'decision':'extend','accepted_candidate_ids':[],
        'prediction':[{'t':t,'bearing_deg':bearings[t]['hull_deg']} for t in (30,40)]}
    return apply_answer(state,context,json.dumps(answer))


class IncrementalSpatialPlanTests(unittest.TestCase):
    def test_combines_platform_and_model_bearing_retains_mirrors(self):
        plan=build_spatial_plan(fixture())
        self.assertEqual(plan['status'],'spatial_prediction_candidates')
        self.assertEqual(plan['paired_observation_count'],3)
        self.assertGreaterEqual(len(plan['branches']),2)
        self.assertFalse(plan['unique_solution']);self.assertFalse(plan['identity_confirmed'])
        points=[p for b in plan['branches'] for s in b['prediction_segments'] for p in s]
        self.assertTrue(all(p['t']>20 for p in points))
        self.assertTrue(any(abs(p['x']-1080)<100 and abs(p['y']-2040)<100 for p in points if p['t']==40))
        self.assertTrue(any(p['x']<0 for p in points))
        self.assertEqual(plan['forecast_method'],'model_primary_bearing_plus_numeric_companion_projection')

    def test_unopened_companion_observations_do_not_affect_plan(self):
        state=fixture();plan=build_spatial_plan(state);other=copy.deepcopy(state)
        for p in other['companion_points']:
            if p['t']>20:p['bearing_deg']=170
        self.assertEqual(plan,build_spatial_plan(other))

    def test_no_geometry_or_unstable_geometry_never_fabricates_route(self):
        state=fixture();state['platform_config']=None
        self.assertEqual(build_spatial_plan(state)['branches'],[])
        state=fixture();state['platform_config']['platforms'][0]['tow_m']=[0,0]
        plan=build_spatial_plan(state);self.assertEqual(plan['status'],'bearing_only');self.assertEqual(plan['branches'],[])
        self.assertTrue(all(not r['range_known'] for r in plan['direction_rays']))

    def test_competing_companion_points_are_not_silently_paired(self):
        plan=build_spatial_plan(fixture(duplicates=True))
        self.assertEqual(plan['paired_observation_count'],0);self.assertEqual(plan['ambiguous_companion_times'],3)
        self.assertEqual(plan['branches'],[])

    def test_old_round_display_plan_is_read_only(self):
        state=fixture();before=copy.deepcopy(state);view=public_state(state)
        self.assertTrue(view['rounds'][0]['spatial_plan']['computed_on_read'])
        self.assertEqual(state,before)
