import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from backend.app.services.localization.spatial_tracks import array_position,solve_spatial
from backend.app.services.inputs.input_service import prepare_input
from backend.app.services.rendering.trajectory import render_tracks


def spatial_fixture():
    context={'T_MAX':600,'B_MIN':0,'B_MAX':180,'require_spatial':True,
             'platforms':[{'name':'A','start_ll':[30,122],'heading_deg':0,'speed_kn':0,
                           'hull_m':[0,0],'tow_m':[-150,0]}]}
    platform=context['platforms'][0];points=[]
    for time,x,y in [(0,400,200),(10,410,210),(20,420,220)]:
        bearings=[]
        for offset in ('hull_m','tow_m'):
            a=array_position(platform,platform[offset],time,platform['start_ll'])
            angle=(math.degrees(math.atan2(x-a[0],y-a[1]))-platform['heading_deg'])%360
            bearings.append(min(angle,360-angle))
        points.append({'t':time,'hull_deg':bearings[0],'tow_deg':bearings[1]})
    return context,{'coordinate_system':'dual_array_bearings','warnings':[],
                    'tracks':[{'id':'T1','branch':'main','platform_id':'A','points':points}]}


class SpatialTracksTests(unittest.TestCase):
    def test_exact_geometry_and_mirror_not_arbitrarily_removed(self):
        context,observations=spatial_fixture()
        doc=solve_spatial(observations,context)
        self.assertEqual(doc['coordinate_system'],'lon_lat')
        self.assertEqual(len(doc['tracks']),2)
        self.assertFalse(doc['localization']['unique_solution'])
        for track in doc['tracks']:
            for point,x,y in zip(track['points'],(400,410,420),(200,210,220)):
                east=6371000*math.cos(math.radians(30))*math.radians(point['x']-122)
                north=6371000*math.radians(point['y']-30)
                self.assertAlmostEqual(abs(east),x,places=5)
                self.assertAlmostEqual(north,y,places=5)
                self.assertGreater(point['sensitivity_m'],0)

    def test_rotation_motion_and_port_offset_match_generator(self):
        context,_=spatial_fixture();p=context['platforms'][0]
        p.update(heading_deg=90,speed_kn=3600/1852)
        x,y=array_position(p,[5,7],10,[30,122])
        self.assertAlmostEqual(x,15);self.assertAlmostEqual(y,7)

    def test_no_platform_no_direct_positions_and_bad_pairs(self):
        context,observations=spatial_fixture()
        with self.assertRaisesRegex(ValueError,'platform.json'):solve_spatial(observations,{})
        with self.assertRaisesRegex(ValueError,'dual_array_bearings'):
            solve_spatial({'coordinate_system':'lon_lat'},context)
        for key,value in [('tow_deg',None),('hull_deg',float('nan')),('t',-1)]:
            bad=copy.deepcopy(observations);bad['tracks'][0]['points'][0][key]=value
            with self.assertRaises(ValueError):solve_spatial(bad,context)
        for point in observations['tracks'][0]['points']:point['tow_deg']=point['hull_deg']
        with self.assertRaisesRegex(ValueError,'未通过'):solve_spatial(observations,context)

    def test_render_and_backend_prompt_contract(self):
        context,observations=spatial_fixture()
        prepared=prepare_input(2,None,'test','images')
        self.assertTrue(prepared.context['require_spatial'])
        self.assertIn('dual_array_bearings',prepared.prompt)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            doc=render_tracks(json.dumps(observations),directory,context)
            self.assertEqual(doc['plot_kind'],'spatial_candidates')
            self.assertTrue((Path(directory)/'estimated_track.png').is_file())
            with self.assertRaises(ValueError):
                render_tracks('{"coordinate_system":"bearing_time","tracks":[]}',directory,context)
