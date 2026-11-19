import json
from pathlib import Path
import tempfile
import unittest
import copy
from unittest.mock import patch

from backend.app.services.inputs.track_validation import parse_tracks
from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.rendering.trajectory import render_tracks,render_document

CONTEXT={'T_MAX':600,'platforms':[{'name':'A','start_ll':[30,122],
    'heading_deg':30,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}]}


def result(frame='local_xy_m'):
    return {'coordinate_system':frame,'position_observable':frame!='bearing_time',
        'warnings':['左右舷候选未核验'],
        'tracks':[{'id':'T1','branch':'main','points':[
            {'t':0,'x':20,'y':0 if frame=='bearing_time' else 100},
            {'t':5,'x':30,'y':5 if frame=='bearing_time' else 110}]}]}


class TrajectoryTests(unittest.TestCase):
    def test_spatial_fragments_share_identity_color_and_legend_without_joining_gaps(self):
        doc=result('lon_lat');doc['plot_kind']='spatial_candidates';doc['tracks']=[]
        for identity,side,start in [('H1',[1,1],0),('H1',[1,1],40),('H1',[-1,-1],0),('H2',[1,1],0)]:
            doc['tracks'].append({'id':identity,'branch':f'segment{len(doc["tracks"])}',
                'handedness':side,'points':[{'t':start,'x':122.01,'y':30.01},
                                           {'t':start+10,'x':122.02,'y':30.02}]})
        original=copy.deepcopy(doc['tracks']);captured=[]
        def capture(fig,*args,**kwargs):
            captured.extend({'color':line.get_color(),'label':line.get_label(),
                             'linestyle':line.get_linestyle(),'count':len(line.get_xdata())}
                            for line in fig.axes[0].lines if len(line.get_xdata())==2)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            with patch('matplotlib.figure.Figure.savefig',autospec=True,side_effect=capture):
                rendered=render_document(doc,folder,CONTEXT)
        self.assertEqual(len(captured),4)  # Still four separate polylines, no gap fill.
        self.assertEqual(len({line['color'] for line in captured[:3]}),1)
        self.assertNotEqual(captured[0]['color'],captured[3]['color'])
        self.assertEqual(captured[1]['label'],'_nolegend_')
        self.assertEqual(captured[0]['linestyle'],'-')
        self.assertEqual(captured[2]['linestyle'],'--')
        self.assertEqual(rendered['tracks'],original)
        self.assertEqual(rendered['plot_summary']['identity_hypotheses'],2)
        self.assertEqual(rendered['plot_summary']['candidate_segments'],4)
        self.assertFalse(rendered['plot_summary']['target_count_confirmed'])

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
