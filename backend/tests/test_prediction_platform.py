import copy
import json
import math
import unittest

from backend.app.services.localization.platform_motion import platform_metadata,platform_snapshot
from backend.app.services.association.incremental_prediction import create_state,prepare_round,public_state
from backend.tests.test_incremental_prediction import dataset


def navigation(speed=10,heading=90):
    return {'platforms':[{'name':'A','start_ll':[30.0623,122.0415],'heading_deg':heading,
        'speed_kn':speed,'hull_m':[0,0],'tow_m':[-150,0]}]}


class PredictionPlatformTests(unittest.TestCase):
    def test_stationary_platform_retains_coordinates_and_sensor_geometry(self):
        motion=platform_snapshot(platform_metadata(navigation(0,30)),'A',[12,30])
        self.assertEqual([p['t'] for p in motion['points']],[0,12,30])
        for p in motion['points']:
            self.assertEqual(p['latitude'],30.0623);self.assertEqual(p['longitude'],122.0415)
            self.assertAlmostEqual(p['displacement_m'],0)
            self.assertAlmostEqual(p['sensors']['tow']['east_m'],-75,places=5)
            self.assertAlmostEqual(p['sensors']['tow']['north_m'],-150*math.cos(math.radians(30)),places=5)

    def test_moving_platform_updates_east_north_and_ll(self):
        motion=platform_snapshot(platform_metadata(navigation()),'A',[10,20]);p=motion['points'][-1]
        self.assertAlmostEqual(p['east_m'],10*1852/3600*20,places=5)
        self.assertAlmostEqual(p['north_m'],0,places=5)
        self.assertGreater(p['longitude'],motion['initial_longitude'])
        self.assertAlmostEqual(p['sensors']['tow']['east_m'],p['east_m']-150,places=5)
        self.assertEqual(motion['position_source'],'configured_constant_heading_speed_model')

    def test_no_config_does_not_fabricate_platform_fields(self):
        self.assertIsNone(platform_metadata(None));self.assertIsNone(platform_snapshot(None,'A',[12]))
        raw=navigation();del raw['platforms'][0]['speed_kn'];self.assertIsNone(platform_metadata(raw))
        self.assertNotIn('platform_motion',public_state(create_state(dataset(),['P0'])))

    def test_context_only_contains_open_platform_times_and_metadata_is_frozen(self):
        data=dataset();data['platform_config']=platform_metadata(navigation())
        state=create_state(data,['P0','P1','P2']);context,prompt=prepare_round(state)
        self.assertEqual(max(p['t'] for p in context['platform_state']['points']),20)
        self.assertNotIn('P3',prompt)
        self.assertEqual(public_state(state)['platform_motion']['points'][-1]['t'],20)
        other=copy.deepcopy(data);other['platform_config']['platforms'][0]['speed_kn']=0
        self.assertNotEqual(state['dataset_version'],create_state(other,['P0','P1','P2'])['dataset_version'])
        data['platform_config']['platforms'][0]['speed_kn']=0
        self.assertEqual(state['platform_config']['platforms'][0]['speed_kn'],10)
