import json
from pathlib import Path
import tempfile
import unittest

from backend.app.infrastructure.llm.client import build_request, ClientError
from backend.app.services.data_input import prepare_data, read_document, MAX_DATA_BYTES
from backend.app.services.trajectory import parse_tracks, render_tracks
from backend.tests.test_trajectory import CONTEXT


def semantic_fixture():
    return {'schema_version':'1.0.0','sim_settings':{'t_max_s':600},
        'source':{'run_dir':'do-not-read-this-path'},'summary_nl':'untrusted document text',
        'platforms':[{'platform_id':'A','self':{'position_start':{'lat':30,'lon':122}},
            'targets':[{'target_id':'T1','kinematics':{
                'position_start':{'lat':30.01,'lon':122.01},
                'position_end':{'lat':30.02,'lon':122.02},'observation_window_s':[60,560]}}]}]}


class DataInputTests(unittest.TestCase):
    def test_semantic_targets_not_platform_and_full_interval(self):
        context,text,info=prepare_data(json.dumps(semantic_fixture()).encode())
        doc=json.loads(text)
        self.assertEqual(info['source_type'],'semantic_summary')
        self.assertEqual(info['objects'],1)
        self.assertEqual(info['platform_count'],1)
        self.assertEqual(doc['tracks'][0]['id'],'T1')
        self.assertEqual([p['t'] for p in doc['tracks'][0]['points']],[0,600])
        self.assertNotIn('do-not-read-this-path',text)
        self.assertNotIn('untrusted document text',text)
        self.assertIn('不是观测窗端点',text)
        self.assertEqual(context['platforms'][0]['name'],'A')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            saved=render_tracks(text,folder,context)
            self.assertEqual(saved['plot_kind'],'endpoints')
            self.assertTrue((Path(folder)/'estimated_track.png').exists())

    def test_semantic_cross_platform_deduplicates_consistent_targets(self):
        import copy
        doc=semantic_fixture();other=copy.deepcopy(doc['platforms'][0]);other['platform_id']='B'
        doc['platforms'].append(other)
        self.assertEqual(prepare_data(json.dumps(doc).encode())[2]['objects'],1)
        other['targets'][0]['kinematics']['position_end']['lat']=31
        with self.assertRaisesRegex(ValueError,'不一致'):prepare_data(json.dumps(doc).encode())

    def test_semantic_bad_and_missing_endpoints_rejected(self):
        for field in ('position_start','position_end'):
            doc=semantic_fixture();del doc['platforms'][0]['targets'][0]['kinematics'][field]
            with self.assertRaises(ValueError):prepare_data(json.dumps(doc).encode())
        doc=semantic_fixture();doc['platforms'][0]['targets']=[]
        with self.assertRaises(ValueError):prepare_data(json.dumps(doc).encode())

    def test_platform_as_static_drawing_object(self):
        context, text, info = prepare_data(json.dumps(CONTEXT).encode())
        points = json.loads(text)['tracks'][0]['points']
        self.assertEqual(points, [{'t': 0, 'x': 122.0, 'y': 30.0}, {'t': 600, 'x': 122.0, 'y': 30.0}])
        self.assertTrue(info['derived_from_initial_state'])
        self.assertIn('platforms', CONTEXT)
        self.assertNotIn('platforms', context)  # No invented observer marker.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            doc = render_tracks(text, tmp, context)
            self.assertEqual(doc['input_mode'], 'data')
            self.assertTrue((Path(tmp) / 'estimated_track.png').read_bytes().startswith(b'\x89PNG'))

    def test_single_point_without_platform(self):
        raw = b'{"coordinate_system":"lon_lat","points":[{"t":0,"x":122,"y":30}]}'
        context, text, info = prepare_data(raw)
        self.assertEqual(info['points'], 1)
        self.assertEqual(parse_tracks(text, context)['input_mode'], 'data')
        with self.assertRaises(ValueError):parse_tracks(text)  # Image mode unchanged.

    def test_js_only_json_wrappers(self):
        raw = '{"coordinate_system":"local_xy_m","points":[{"t":0,"x":1,"y":2}]}'
        for prefix in ('const points = ', 'export default ', 'module.exports = '):
            self.assertEqual(prepare_data((prefix+raw+';').encode(), 'points.js')[2]['points'], 1)
        for bad in ('const points = alert(1);', 'const points = '+raw+'; fetch("https://example.com");', "const points = {'x':1};"):
            with self.assertRaises(ValueError):read_document(bad.encode(), 'bad.js')

    def test_jsonl_and_array_named_positions(self):
        rows = [{'target_id':'T1','time_s':0,'lon':122,'lat':30},
                {'target_id':'T1','time_s':5,'lon':122.001,'lat':30.002}]
        raw = '\n'.join(json.dumps(p) for p in rows).encode()
        self.assertEqual(prepare_data(raw, 'points.jsonl')[2]['objects'], 1)
        self.assertEqual(prepare_data(json.dumps(rows).encode())[2]['points'], 2)

    def test_reject_bad_points_and_ambiguous_coordinates(self):
        for doc in ({'points':[{'t':0,'x':1,'y':2}]},
                    {'coordinate_system':'lon_lat','points':[{'t':0,'x':500,'y':2}]},
                    {'coordinate_system':'lon_lat','points':[{'t':0,'x':float('nan'),'y':2}]},
                    {'coordinate_system':'lon_lat','points':[{'t':0,'x':1,'y':2},{'t':0,'x':2,'y':3}]}):
            with self.assertRaises(ValueError):prepare_data(json.dumps(doc).encode())
        with self.assertRaises(ValueError):prepare_data(b'x'*(MAX_DATA_BYTES+1))

    def test_model_cannot_invent_or_change_source(self):
        context, text, _ = prepare_data(json.dumps(CONTEXT).encode())
        doc=json.loads(text);doc['tracks'][0]['points'][1]['x']+=.1
        with self.assertRaisesRegex(ValueError, '更改'):parse_tracks(json.dumps(doc), context)
        doc=json.loads(text);doc['tracks'].append(doc['tracks'][0])
        with self.assertRaisesRegex(ValueError, '数量'):parse_tracks(json.dumps(doc), context)
        doc=json.loads(text);doc['coordinate_system']='bearing_time'
        with self.assertRaisesRegex(ValueError, '坐标系'):parse_tracks(json.dumps(doc), context)

    def test_text_only_requires_explicit_flag(self):
        with self.assertRaises(ClientError):build_request([], 'test', 'model')
        body, images = build_request([], 'validated numeric data', 'model', allow_text_only=True)
        self.assertEqual(images, [])
        self.assertEqual([p['type'] for p in body['messages'][1]['content']], ['text'])


if __name__ == '__main__':unittest.main()
