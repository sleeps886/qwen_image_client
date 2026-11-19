import copy
import math
import os
import unittest
from unittest.mock import patch

import numpy as np

from backend.app.services.localization.continuous_tracks import estimate_continuous,reproject_tracks,to_xy,tracking_options
from backend.app.services.localization.spatial_tracks import array_position,solve_spatial
from backend.app.agents.sonar_agent import build_tracking_graph


def tracking_fixture(noise=0.,count=20):
    rng=np.random.default_rng(231)
    context={'T_MAX':(count-1)*10,'B_MIN':0,'B_MAX':180,'image_time_axis':'bottom_to_top',
             'platforms':[{'name':'A','start_ll':[30,122],'heading_deg':0,'speed_kn':0,
                           'hull_m':[0,0],'tow_m':[-150,0]}]}
    platform=context['platforms'][0];points=[];truth={}
    evidence={'images':[{'image_index':i,'array':array,'platform_id':'A','roi':[20,20,1020,620],'candidates':[]}
                         for i,array in enumerate(('hull','tow'))]}
    for index in range(count):
        time=index*10;xy=np.array([600+2*time,400+.5*time]);truth[time]=xy
        row={'t':time,'sample_index':index}
        for i,array in enumerate(('hull','tow')):
            center=array_position(platform,platform[array+'_m'],time,platform['start_ll'])
            bearing=math.degrees(math.atan2(xy[0]-center[0],xy[1]-center[1]))+rng.normal(0,noise)
            candidate_id=f'I{i}R{index}P0'
            row[array+'_deg']=bearing;row[array+'_candidate_id']=candidate_id
            evidence['images'][i]['candidates'].append({'id':candidate_id,'sample_index':index,'t':time,
                'x_px':20+bearing/180*1000,'y_px':620-time/context['T_MAX']*600,'bearing_deg':bearing})
        points.append(row)
    observations={'coordinate_system':'dual_array_bearings','warnings':[],
                  'tracks':[{'id':'T1','platform_id':'A','branch':'main','points':points}]}
    return context,observations,evidence,truth


class ContinuousTracksTests(unittest.TestCase):
    def test_exact_motion_preserves_timestamps_mirrors_and_reprojects(self):
        context,observations,evidence,truth=tracking_fixture()
        raw=solve_spatial(observations,context)
        document,tracking=estimate_continuous(raw,context)
        self.assertGreater(tracking['optimized_windows'],0)
        self.assertEqual(tracking['added_timestamps'],0)
        self.assertEqual(len(document['tracks']),2)
        for track in document['tracks']:
            self.assertEqual([p['t'] for p in track['points']],list(truth))
            for point in track['points']:
                xy=to_xy(point,context['platforms'][0]['start_ll'])
                np.testing.assert_allclose([abs(xy[0]),xy[1]],truth[point['t']],atol=1e-5)
        accepted,report=reproject_tracks(document,context,evidence)
        self.assertEqual(report['rejected_branch_points'],0)
        self.assertEqual(report['retained_observation_fraction'],1)
        self.assertEqual(len(accepted['tracks']),2)

    def test_noisy_example_improves_position_without_editing_observations(self):
        context,observations,evidence,truth=tracking_fixture(noise=.15)
        frozen=copy.deepcopy(observations);raw=solve_spatial(observations,context)
        document,report=estimate_continuous(raw,context)
        def rmse(doc):
            errors=[np.linalg.norm(to_xy(p,context['platforms'][0]['start_ll'])-truth[p['t']])**2
                    for track in doc['tracks'] if track['handedness']==[1,1] for p in track['points']]
            return math.sqrt(float(np.mean(errors)))
        self.assertLess(rmse(document),rmse(raw))
        self.assertEqual(observations,frozen)
        self.assertEqual(sum(len(t['points']) for t in document['tracks']),sum(len(t['points']) for t in raw['tracks']))

    def test_missing_sample_and_rejected_point_never_bridged(self):
        context,observations,evidence,_=tracking_fixture()
        observations['tracks'][0]['points'].pop(7)
        raw=solve_spatial(observations,context)
        optimized,report=estimate_continuous(raw,context)
        self.assertGreater(report['splits'],0)
        for track in optimized['tracks']:
            times=[p['t'] for p in track['points']]
            self.assertFalse(60 in times and 80 in times)
        # A reprojection-rejected observation must also split, not get skipped by a polyline.
        for track in optimized['tracks']:
            for p in track['points']:
                if p['t']==120:p['x']+=.04
        filtered,reprojection=reproject_tracks(optimized,context,evidence)
        self.assertGreater(reprojection['rejected_branch_points'],0)
        for track in filtered['tracks']:
            times=[p['t'] for p in track['points']]
            self.assertFalse(110 in times and 130 in times)

    def test_wrong_projection_and_wrong_mirror_sign_rejected(self):
        context,observations,evidence,_=tracking_fixture()
        raw=solve_spatial(observations,context)
        for track in raw['tracks']:track['handedness']=[-s for s in track['handedness']]
        result,report=reproject_tracks(raw,context,evidence)
        self.assertFalse(result['tracks']);self.assertGreater(report['rejected_branch_points'],0)

    def test_explicit_graph_nodes_and_invalid_options(self):
        context,observations,evidence,_=tracking_fixture()
        state=build_tracking_graph().invoke({'context':context,'observations':observations,'evidence':evidence,'trace':[]})
        self.assertEqual(state['trace'],['spatial_localization','continuous_trajectory_estimation','reprojection_quality_check'])
        self.assertNotIn('failure',state)
        for value in ('nan','0','bad'):
            with patch.dict(os.environ,{'TRACK_MAX_SPEED_MPS':value}),self.assertRaises(ValueError):tracking_options(600)

    def test_hard_speed_gate_breaks_impossible_jumps(self):
        context,observations,_,_=tracking_fixture()
        raw=solve_spatial(observations,context)
        for track in raw['tracks']:
            for i,p in enumerate(track['points']):
                if i%2:p['x']+=.1
        result,report=estimate_continuous(raw,context)
        self.assertFalse(result['tracks']);self.assertGreater(report['splits'],0)

    def test_two_times_are_not_claimed_as_optimized_full_track(self):
        context,observations,evidence,_=tracking_fixture()
        observations['tracks'][0]['points']=observations['tracks'][0]['points'][:2]
        raw=solve_spatial(observations,context)
        document,report=estimate_continuous(raw,context)
        self.assertEqual(report['optimized_windows'],0)
        self.assertEqual(report['short_segments'],2)
        self.assertTrue(all(t['estimation']=='short_or_unoptimized' for t in document['tracks']))
