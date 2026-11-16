import json
from pathlib import Path
import tempfile
import unittest

from trajectory import parse_tracks,platform_context,render_tracks

CONTEXT={'T_MAX':600,'platforms':[{'name':'A','start_ll':[30,122],
    'heading_deg':30,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}]}


def result(frame='local_xy_m'):
    return {'coordinate_system':frame,'position_observable':frame!='bearing_time',
        'warnings':['左右舷候选未核验'],
        'tracks':[{'id':'T1','branch':'main','points':[
            {'t':0,'x':20,'y':0 if frame=='bearing_time' else 100},
            {'t':5,'x':30,'y':5 if frame=='bearing_time' else 110}]}]}


class TrajectoryTests(unittest.TestCase):
    def test_render_spatial_and_bearing(self):
        for frame in ('local_xy_m','bearing_time'):
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                doc=render_tracks(json.dumps(result(frame)),folder,CONTEXT)
                self.assertEqual((Path(folder)/'estimated_track.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
                self.assertEqual(json.loads((Path(folder)/'tracks.json').read_text(encoding='utf-8'))['coordinate_system'],frame)
                self.assertIn('未通过',doc['warnings'][0])

    def test_reject_unobservable_and_bad_numbers(self):
        with self.assertRaises(ValueError):parse_tracks(json.dumps(result()))
        for bad in (float('nan'),float('inf'),None,'10'):
            data=result();data['tracks'][0]['points'][0]['x']=bad
            with self.assertRaises(ValueError):parse_tracks(json.dumps(data),CONTEXT)
        data=result();data['tracks'][0]['points'][1]['t']=0
        with self.assertRaises(ValueError):parse_tracks(json.dumps(data),CONTEXT)
        self.assertEqual(parse_tracks('```json\n'+json.dumps(result('bearing_time'))+'\n```')['coordinate_system'],'bearing_time')

    def test_platform_reject_truth(self):
        self.assertEqual(platform_context(json.dumps(CONTEXT).encode())['platforms'][0]['name'],'A')
        with self.assertRaises(ValueError):platform_context(json.dumps({'params':CONTEXT,'targets':[]}).encode())


if __name__=='__main__':unittest.main()
