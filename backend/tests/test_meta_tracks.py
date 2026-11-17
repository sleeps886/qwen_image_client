import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from backend.app.services.data_input import prepare_data
from backend.app.services.meta_tracks import SafeCurve, reconstruct_meta
from backend.app.services.trajectory import parse_tracks, render_tracks


def meta_fixture():
    return {'state':'completed','files':{'truth':'do-not-read-this-file'},'params':{
        'T_MAX':20,'DT':1,'EARTH_R':6371000,'OUT_DIR':'do-not-write-this-path',
        'platforms':[{'name':'A','start_ll':[30,122]}],
        'targets':[
            {'name':'T1','start':[30.01,122.04],'speed_kn':20,'path_type':'waypoints',
                'waypoints':[[30.01,122.03],[30.02,122.03]],'color':'#ff9d00'},
            {'name':'T2','start':[30,122],'path_type':'curve','t_on':5,'t_off':15,
                'curve_func':'(30 + 0.0045*np.sin(2*np.pi*t/20.0), 122 + 0.0001227*t)',
                'color':'#ff00cc'}]}}


class MetaTests(unittest.TestCase):
    def test_full_sampling_wave_global_time_and_checkpoints(self):
        context,text,info=prepare_data(json.dumps(meta_fixture()).encode(),'meta.json')
        self.assertEqual(info['source_type'],'meta_parameters')
        self.assertEqual(info['objects'],2);self.assertEqual(info['points'],42)
        self.assertEqual(info['model_checkpoint_points'],18)
        self.assertNotIn('do-not-',text)
        wave=context['source_tracks'][1]['points']
        self.assertEqual([p['t'] for p in wave],list(range(21)))
        self.assertAlmostEqual(wave[5]['y'],30.0045)
        self.assertAlmostEqual(wave[15]['y'],29.9955)
        self.assertEqual(wave[0]['t'],0);self.assertEqual(wave[-1]['t'],20)
        doc=parse_tracks(text,context)
        self.assertEqual(len(doc['tracks'][1]['points']),21)
        self.assertEqual(doc['plot_kind'],'meta_reconstruction')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            render_tracks(text,folder,context)
            saved=json.loads((Path(folder)/'tracks.json').read_text(encoding='utf-8'))
            self.assertEqual(len(saved['tracks'][1]['points']),21)
            self.assertTrue((Path(folder)/'estimated_track.png').read_bytes().startswith(b'\x89PNG'))

    def test_does_not_accept_changed_model_checkpoints(self):
        context,text,_=prepare_data(json.dumps(meta_fixture()).encode())
        doc=json.loads(text);doc['tracks'][1]['points'][1]['x']+=.01
        with self.assertRaisesRegex(ValueError,'更改'):parse_tracks(json.dumps(doc),context)

    def test_waypoint_arrival_stop_and_static(self):
        raw=meta_fixture();raw['params']['targets']=raw['params']['targets'][:1]
        target=raw['params']['targets'][0]
        target.update(start=[0,0],waypoints=[[0,0],[0,.000001],[.000001,.000001]],speed_kn=20)
        points=reconstruct_meta(raw)[0]['tracks'][0]['points']
        self.assertEqual(points[0],{'t':0,'x':0,'y':0})
        self.assertEqual(points[1]['x'],.000001);self.assertEqual(points[-1]['y'],.000001)
        target['speed_kn']=0
        self.assertTrue(all(p['x']==0 and p['y']==0 for p in reconstruct_meta(raw)[0]['tracks'][0]['points']))

    def test_safe_math_and_conditional_formulas(self):
        formula=SafeCurve('(30 + math.cos(t/2)*0.01, 122 if t<5 else 122.01)')
        self.assertAlmostEqual(formula(0)[0],30.01);self.assertEqual(formula(6)[1],122.01)
        self.assertEqual(SafeCurve('(30,np.clip(t,121,123))')(122),(30,122))

    def test_reject_scripts_unsafe_attributes_or_resource_abuse(self):
        for expression in (
            '__import__("os").system("echo unsafe")',
            '(30,np.load("secret"))','(30,np.save("bad",t))','(30,np.__dict__)',
            '(30,122) if t<0 else (30,np.load("secret"))',
            '(lambda t:(30,122))(t)','[x for x in (30,122)]','(30,2**1000000)',
            '(30,1/0)','(100,122)','(30,float("nan"))'):
            with self.subTest(expression=expression),self.assertRaises(ValueError):SafeCurve(expression)(0)

    def test_bad_metadata_and_oversampling(self):
        for field,value in [('DT',0),('DT',float('nan')),('DT',.001),('T_MAX',-1),('T_MAX',True)]:
            raw=meta_fixture();raw['params'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):prepare_data(json.dumps(raw).encode())
        raw=meta_fixture();raw['state']='running'
        with self.assertRaises(ValueError):prepare_data(json.dumps(raw).encode())
        raw=meta_fixture();raw['params']['targets'].append(copy.deepcopy(raw['params']['targets'][0]))
        with self.assertRaises(ValueError):prepare_data(json.dumps(raw).encode())
        raw=meta_fixture();raw['params']['targets'][0]['path_type']='unknown'
        with self.assertRaises(ValueError):prepare_data(json.dumps(raw).encode())

    def test_bare_params_and_non_divisible_sampling(self):
        raw=meta_fixture()['params'];raw['T_MAX']=20.5
        context,text,info=prepare_data(json.dumps(raw).encode())
        self.assertEqual(info['source_type'],'meta_parameters')
        self.assertEqual(context['source_tracks'][0]['points'][-1]['t'],20)
        self.assertLess(len(text),40000)


if __name__=='__main__':unittest.main()
